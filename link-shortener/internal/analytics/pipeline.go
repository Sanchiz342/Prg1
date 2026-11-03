package analytics

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/sanchiz342/prg1/link-shortener/internal/metrics"
	"github.com/sanchiz342/prg1/link-shortener/internal/store"
)

const group = "click-writers"

// Publisher buffers events in memory and appends them to the Redis Stream in pipelined batches, so a redirect
// costs a channel send instead of a network round-trip.
//
// Trade-off: events still in the buffer are lost if the process crashes (a graceful shutdown drains them).
// For a link shortener, losing a few clicks on a crash is acceptable; slowing every redirect is not.
type Publisher struct {
	rdb    *redis.Client
	stream string
	maxLen int64
	ch     chan Event
	sync   chan chan struct{}
	wg     sync.WaitGroup
}

func NewPublisher(rdb *redis.Client, prefix string, buffer int, maxLen int64) *Publisher {
	p := &Publisher{rdb: rdb, stream: prefix + "clicks", maxLen: maxLen, ch: make(chan Event, buffer), sync: make(chan chan struct{})}
	p.wg.Add(1)
	go p.loop()
	return p
}

func (p *Publisher) Stream() string { return p.stream }

// Publish never blocks: when the buffer is full the event is dropped and counted.
func (p *Publisher) Publish(e Event) {
	select {
	case p.ch <- e:
	default:
		metrics.ClicksDropped.WithLabelValues("buffer_full").Inc()
	}
}

func (p *Publisher) loop() {
	defer p.wg.Done()
	batch := make([]Event, 0, 256)
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	flush := func() {
		if len(batch) == 0 {
			return
		}
		p.flush(batch)
		batch = batch[:0]
	}
	for {
		select {
		case e, ok := <-p.ch:
			if !ok {
				flush()
				return
			}
			batch = append(batch, e)
			if len(batch) == cap(batch) {
				flush()
			}
		case <-ticker.C:
			flush()
		case done := <-p.sync:
			for drained := false; !drained; {
				select {
				case e := <-p.ch:
					batch = append(batch, e)
				default:
					drained = true
				}
			}
			flush()
			close(done)
		}
	}
}

func (p *Publisher) flush(batch []Event) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	pipe := p.rdb.Pipeline()
	for _, e := range batch {
		data, _ := json.Marshal(e)
		pipe.XAdd(ctx, &redis.XAddArgs{Stream: p.stream, MaxLen: p.maxLen, Approx: true, Values: []any{"e", data}})
	}
	if _, err := pipe.Exec(ctx); err != nil {
		metrics.ClicksDropped.WithLabelValues("redis_error").Add(float64(len(batch)))
		slog.Error("publish click events", "count", len(batch), "err", err)
		return
	}
	metrics.ClicksPublished.Add(float64(len(batch)))
}

// Sync blocks until every event published before the call has been written to Redis (used by tests and tools).
func (p *Publisher) Sync() {
	done := make(chan struct{})
	p.sync <- done
	<-done
}

// Close drains the buffer into Redis. Call it after the HTTP server has stopped accepting requests.
func (p *Publisher) Close() {
	close(p.ch)
	p.wg.Wait()
}

// Writer consumes the stream as a member of a consumer group and writes batches to PostgreSQL.
//
// Delivery is at-least-once: entries are acknowledged only after the INSERT commits. A crash between commit and
// XACK redelivers the batch, and the event_id primary key turns the duplicate insert into a no-op. Entries left
// pending by a dead consumer are reclaimed with XAUTOCLAIM after ClaimIdle.
type Writer struct {
	rdb       *redis.Client
	st        *store.Store
	stream    string
	consumer  string
	BatchSize int64
	Block     time.Duration
	ClaimIdle time.Duration
}

func NewWriter(rdb *redis.Client, st *store.Store, stream string) *Writer {
	host, _ := os.Hostname()
	return &Writer{
		rdb: rdb, st: st, stream: stream,
		consumer:  fmt.Sprintf("%s-%d", host, os.Getpid()),
		BatchSize: 500, Block: time.Second, ClaimIdle: 30 * time.Second,
	}
}

func (w *Writer) WithConsumer(name string) *Writer { w.consumer = name; return w }

func (w *Writer) ensureGroup(ctx context.Context) error {
	err := w.rdb.XGroupCreateMkStream(ctx, w.stream, group, "0").Err()
	if err != nil && !strings.Contains(err.Error(), "BUSYGROUP") {
		return err
	}
	return nil
}

// Run processes events until ctx is cancelled.
func (w *Writer) Run(ctx context.Context) {
	for ctx.Err() == nil {
		if err := w.ensureGroup(ctx); err != nil {
			slog.Error("create consumer group", "err", err)
			sleep(ctx, 2*time.Second)
			continue
		}
		break
	}
	lastClaim := time.Time{}
	for ctx.Err() == nil {
		if time.Since(lastClaim) > w.ClaimIdle/2 {
			lastClaim = time.Now()
			if _, err := w.ReclaimOnce(ctx); err != nil && ctx.Err() == nil {
				slog.Warn("reclaim pending clicks", "err", err)
			}
		}
		if _, err := w.ProcessOnce(ctx); err != nil && ctx.Err() == nil {
			slog.Error("write clicks", "err", err)
			sleep(ctx, time.Second) // the batch stays pending and is retried via reclaim
		}
		w.reportLag(ctx)
	}
}

// ProcessOnce reads one batch of new entries and writes it. Returns the number of rows inserted.
func (w *Writer) ProcessOnce(ctx context.Context) (int64, error) {
	streams, err := w.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group: group, Consumer: w.consumer, Streams: []string{w.stream, ">"}, Count: w.BatchSize, Block: w.Block,
	}).Result()
	if errors.Is(err, redis.Nil) {
		return 0, nil
	}
	if err != nil {
		if !strings.Contains(err.Error(), "NOGROUP") {
			return 0, err
		}
		// Stream or group missing (first start, or Redis was flushed): create it from the beginning of the
		// stream and read again, so events published before the group existed are not skipped.
		if err := w.ensureGroup(ctx); err != nil {
			return 0, err
		}
		streams, err = w.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group: group, Consumer: w.consumer, Streams: []string{w.stream, ">"}, Count: w.BatchSize, Block: w.Block,
		}).Result()
		if errors.Is(err, redis.Nil) {
			return 0, nil
		}
		if err != nil {
			return 0, err
		}
	}
	var msgs []redis.XMessage
	for _, s := range streams {
		msgs = append(msgs, s.Messages...)
	}
	return w.write(ctx, msgs)
}

// ReclaimOnce takes over entries that another consumer (or a failed attempt) left unacknowledged.
func (w *Writer) ReclaimOnce(ctx context.Context) (int64, error) {
	var total int64
	start := "0-0"
	for {
		msgs, next, err := w.rdb.XAutoClaim(ctx, &redis.XAutoClaimArgs{
			Stream: w.stream, Group: group, Consumer: w.consumer, MinIdle: w.ClaimIdle, Start: start,
			Count: w.BatchSize,
		}).Result()
		if err != nil {
			return total, err
		}
		n, err := w.write(ctx, msgs)
		total += n
		if err != nil || next == "0-0" || len(msgs) == 0 {
			return total, err
		}
		start = next
	}
}

func (w *Writer) write(ctx context.Context, msgs []redis.XMessage) (int64, error) {
	if len(msgs) == 0 {
		return 0, nil
	}
	clicks := make([]store.Click, 0, len(msgs))
	ids := make([]string, 0, len(msgs))
	for _, m := range msgs {
		ids = append(ids, m.ID)
		c, err := decode(m)
		if err != nil {
			// A poison message would block the group forever; acknowledge it and move on.
			metrics.ClicksDropped.WithLabelValues("malformed").Inc()
			slog.Warn("drop malformed click event", "id", m.ID, "err", err)
			continue
		}
		clicks = append(clicks, c)
	}
	inserted, err := w.st.InsertClicks(ctx, clicks)
	if err != nil {
		return 0, err
	}
	if err := w.rdb.XAck(ctx, w.stream, group, ids...).Err(); err != nil {
		return inserted, err // rows are committed; redelivery will be deduplicated
	}
	metrics.ClicksWritten.Add(float64(inserted))
	return inserted, nil
}

func decode(m redis.XMessage) (store.Click, error) {
	raw, ok := m.Values["e"].(string)
	if !ok {
		return store.Click{}, errors.New("missing payload")
	}
	var e Event
	if err := json.Unmarshal([]byte(raw), &e); err != nil {
		return store.Click{}, err
	}
	visitor, err := hex.DecodeString(e.Visitor)
	if err != nil || e.ID == "" || e.LinkID == 0 || len(e.Country) != 2 {
		return store.Click{}, errors.New("invalid event fields")
	}
	return store.Click{
		EventID: e.ID, LinkID: e.LinkID, TS: e.TS, VisitorHash: visitor, Country: e.Country,
		ReferrerHost: e.Referrer, Device: e.Device, Browser: e.Browser, IsBot: e.Bot,
	}, nil
}

func (w *Writer) reportLag(ctx context.Context) {
	if p, err := w.rdb.XPending(ctx, w.stream, group).Result(); err == nil {
		metrics.StreamPending.Set(float64(p.Count))
	}
}

func sleep(ctx context.Context, d time.Duration) {
	select {
	case <-ctx.Done():
	case <-time.After(d):
	}
}
