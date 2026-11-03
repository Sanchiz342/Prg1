// Package cache keeps hot link lookups in Redis so redirects rarely touch PostgreSQL.
//
//   - Positive entries live for CACHE_TTL, but never past the link's expiry, so an expired link can't be served
//     from cache.
//   - Unknown codes are cached as a short-lived "negative" entry: scanners hammering random codes hit Redis, not
//     the database. Creating a link deletes its negative entry, so a fresh alias works immediately.
//   - On a miss, concurrent lookups for the same code in one process share a single DB query (singleflight),
//     which prevents a stampede when a popular entry expires.
package cache

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
	"golang.org/x/sync/singleflight"

	"github.com/sanchiz342/prg1/link-shortener/internal/store"
)

// Entry is the minimal data a redirect needs.
type Entry struct {
	ID        int64  `json:"id"`
	URL       string `json:"url"`
	ExpiresAt int64  `json:"exp,omitempty"` // unix seconds, 0 = never
	Disabled  bool   `json:"disabled,omitempty"`
}

func (e Entry) Expired(now time.Time) bool { return e.ExpiresAt != 0 && now.Unix() >= e.ExpiresAt }

// Result says where a lookup was answered from (exported as a metric label).
type Result string

const (
	Hit         Result = "hit"
	NegativeHit Result = "negative_hit"
	Miss        Result = "miss"
	Bypass      Result = "bypass" // Redis unavailable, answered from the DB
)

const negativeMarker = "-"

type Loader func(ctx context.Context, code string) (store.Link, error)

type LinkCache struct {
	rdb         *redis.Client
	prefix      string
	ttl         time.Duration
	negativeTTL time.Duration
	load        Loader
	group       singleflight.Group
}

func New(rdb *redis.Client, prefix string, ttl, negativeTTL time.Duration, load Loader) *LinkCache {
	return &LinkCache{rdb: rdb, prefix: prefix + "link:", ttl: ttl, negativeTTL: negativeTTL, load: load}
}

func (c *LinkCache) key(code string) string { return c.prefix + code }

// Get returns the entry for code, or store.ErrNotFound.
func (c *LinkCache) Get(ctx context.Context, code string) (Entry, Result, error) {
	raw, err := c.rdb.Get(ctx, c.key(code)).Result()
	switch {
	case err == nil && raw == negativeMarker:
		return Entry{}, NegativeHit, store.ErrNotFound
	case err == nil:
		var e Entry
		if json.Unmarshal([]byte(raw), &e) == nil {
			return e, Hit, nil
		}
		// Corrupt entry: fall through to a reload.
	case !errors.Is(err, redis.Nil):
		// Redis is down: keep redirects working straight from the database.
		e, lerr := c.fromDB(ctx, code)
		return e, Bypass, lerr
	}

	v, err, _ := c.group.Do(code, func() (any, error) {
		// Shared by every waiter: detach from the first caller's cancellation, bound it on its own.
		lctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
		defer cancel()
		e, err := c.fromDB(lctx, code)
		switch {
		case errors.Is(err, store.ErrNotFound):
			c.rdb.Set(lctx, c.key(code), negativeMarker, c.negativeTTL)
		case err == nil:
			c.put(lctx, code, e)
		}
		return e, err
	})
	return v.(Entry), Miss, err
}

func (c *LinkCache) fromDB(ctx context.Context, code string) (Entry, error) {
	l, err := c.load(ctx, code)
	if err != nil {
		return Entry{}, err
	}
	return FromLink(l), nil
}

func FromLink(l store.Link) Entry {
	e := Entry{ID: l.ID, URL: l.TargetURL, Disabled: l.Disabled}
	if l.ExpiresAt != nil {
		e.ExpiresAt = l.ExpiresAt.Unix()
	}
	return e
}

func (c *LinkCache) put(ctx context.Context, code string, e Entry) {
	ttl := c.ttl
	if e.ExpiresAt != 0 {
		if left := time.Until(time.Unix(e.ExpiresAt, 0)); left < ttl {
			ttl = left
		}
	}
	if ttl <= 0 {
		// Already expired: cache briefly so repeated hits on a dead link stay cheap.
		ttl = c.negativeTTL
	}
	data, _ := json.Marshal(e)
	c.rdb.Set(ctx, c.key(code), data, ttl)
}

// Invalidate drops the cached entry (positive or negative) after a create, update or delete.
// Errors are returned so callers can log them: a failed invalidation means stale data until TTL.
func (c *LinkCache) Invalidate(ctx context.Context, code string) error {
	return c.rdb.Del(ctx, c.key(code)).Err()
}
