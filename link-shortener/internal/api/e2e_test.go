package api

// End-to-end tests against real PostgreSQL and Redis. They are skipped unless both are configured:
//
//	SNIP_TEST_DATABASE_URL=postgres://...  SNIP_TEST_REDIS_URL=redis://...  go test ./...
//
// Each test truncates the database and uses its own Redis key prefix.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/sanchiz342/prg1/link-shortener/internal/analytics"
	"github.com/sanchiz342/prg1/link-shortener/internal/config"
	"github.com/sanchiz342/prg1/link-shortener/internal/store"
)

type env struct {
	t      *testing.T
	srv    *Server
	http   *httptest.Server
	st     *store.Store
	rdb    *redis.Client
	pub    *analytics.Publisher
	writer *analytics.Writer
	key    string
}

func newEnv(t *testing.T, tweak func(*config.Config)) *env {
	t.Helper()
	dbURL, redisURL := os.Getenv("SNIP_TEST_DATABASE_URL"), os.Getenv("SNIP_TEST_REDIS_URL")
	if dbURL == "" || redisURL == "" {
		t.Skip("SNIP_TEST_DATABASE_URL / SNIP_TEST_REDIS_URL not set")
	}
	ctx := context.Background()
	st, err := store.Open(ctx, dbURL)
	if err != nil {
		t.Fatal(err)
	}
	if err := st.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if err := st.Truncate(ctx); err != nil {
		t.Fatal(err)
	}
	opts, _ := redis.ParseURL(redisURL)
	rdb := redis.NewClient(opts)

	base, _ := url.Parse("https://sho.rt")
	cfg := &config.Config{
		BaseURL:           base,
		RedisPrefix:       fmt.Sprintf("test:%s:%d:", t.Name(), time.Now().UnixNano()),
		TrustProxy:        true,
		GeoHeader:         "CF-IPCountry",
		VisitorSalt:       "test-salt",
		CacheTTL:          time.Hour,
		NegativeCacheTTL:  time.Minute,
		RedirectLimit:     config.Limit{Rate: 1000, Burst: 1000},
		CreateLimit:       config.Limit{Rate: 1000, Burst: 1000},
		APILimit:          config.Limit{Rate: 1000, Burst: 1000},
		RateLimitFailOpen: true,
		StreamMaxLen:      100_000,
		EventBuffer:       1000,
	}
	if tweak != nil {
		tweak(cfg)
	}
	pub := analytics.NewPublisher(rdb, cfg.RedisPrefix, cfg.EventBuffer, cfg.StreamMaxLen)
	srv := New(cfg, st, rdb, pub)
	e := &env{t: t, srv: srv, http: httptest.NewServer(srv.Handler()), st: st, rdb: rdb, pub: pub,
		writer: analytics.NewWriter(rdb, st, pub.Stream()).WithConsumer("test")}
	e.writer.Block = 50 * time.Millisecond
	e.key, _, err = st.NewAPIKey(ctx, "test")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		e.http.Close()
		pub.Close()
		rdb.Close()
		st.Close()
	})
	return e
}

func (e *env) do(method, path, key string, body any, headers ...string) (*http.Response, map[string]any) {
	e.t.Helper()
	var r io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		r = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, e.http.URL+path, r)
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	if strings.HasPrefix(resp.Header.Get("Content-Type"), "application/json") {
		_ = json.NewDecoder(resp.Body).Decode(&out)
	}
	return resp, out
}

func (e *env) create(body map[string]any) map[string]any {
	e.t.Helper()
	resp, out := e.do("POST", "/api/links", e.key, body)
	if resp.StatusCode != http.StatusCreated {
		e.t.Fatalf("create: %d %v", resp.StatusCode, out)
	}
	return out
}

// drain moves everything published so far through Redis into PostgreSQL.
func (e *env) drain() {
	e.t.Helper()
	e.pub.Sync()
	if _, err := e.writer.ProcessOnce(context.Background()); err != nil {
		e.t.Fatal(err)
	}
}

func TestLinkLifecycleAndAnalytics(t *testing.T) {
	e := newEnv(t, nil)
	link := e.create(map[string]any{"url": "https://example.com/page", "alias": "launch"})
	if link["short_url"] != "https://sho.rt/launch" || link["status"] != "active" {
		t.Fatalf("unexpected link %v", link)
	}

	visit := func(ip, country, ua, ref string) *http.Response {
		resp, _ := e.do("GET", "/launch", "", nil, "X-Real-IP", ip, "CF-IPCountry", country, "User-Agent", ua, "Referer", ref)
		return resp
	}
	iphone := "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
	chrome := "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
	resp := visit("203.0.113.1", "UA", iphone, "https://t.me/news")
	if resp.StatusCode != http.StatusFound || resp.Header.Get("Location") != "https://example.com/page" {
		t.Fatalf("redirect: %d %s", resp.StatusCode, resp.Header.Get("Location"))
	}
	if resp.Header.Get("Cache-Control") != "private, no-store" {
		t.Fatalf("redirects must not be cached by browsers: %q", resp.Header.Get("Cache-Control"))
	}
	visit("203.0.113.1", "UA", iphone, "https://t.me/news") // same visitor again
	visit("198.51.100.2", "PL", chrome, "")
	visit("198.51.100.3", "PL", "Googlebot/2.1", "")
	if r, _ := e.do("HEAD", "/launch", "", nil); r.StatusCode != http.StatusFound {
		t.Fatalf("HEAD: %d", r.StatusCode)
	}
	e.drain()

	resp, stats := e.do("GET", "/api/links/launch/stats", e.key, nil)
	if resp.StatusCode != 200 {
		t.Fatalf("stats: %d %v", resp.StatusCode, stats)
	}
	s := stats["stats"].(map[string]any)
	check := func(name string, got any, want float64) {
		if got != want {
			t.Errorf("%s = %v, want %v", name, got, want)
		}
	}
	check("clicks (humans)", s["clicks"], 3)
	check("unique visitors", s["unique_visitors"], 2)
	check("bot clicks", s["bot_clicks"], 1)
	check("total clicks (HEAD not counted)", stats["total_clicks"], 4)
	countries := s["countries"].([]any)
	if top := countries[0].(map[string]any); top["key"] != "UA" || top["clicks"] != 2.0 {
		t.Errorf("countries = %v", countries)
	}
	refs := fmt.Sprint(s["referrers"])
	if !strings.Contains(refs, "t.me") {
		t.Errorf("referrers = %v", refs)
	}
	ts := s["timeseries"].([]any)
	if len(ts) != 30 || ts[29].(map[string]any)["clicks"] != 3.0 {
		t.Errorf("timeseries should cover 30 days ending today with today's 3 clicks: %v", ts[len(ts)-1])
	}

	// List shows the denormalised click counter.
	_, list := e.do("GET", "/api/links", e.key, nil)
	if items := list["items"].([]any); len(items) != 1 || items[0].(map[string]any)["clicks"] != 4.0 {
		t.Fatalf("list = %v", list)
	}
}

func TestNegativeCacheIsClearedWhenAliasIsCreated(t *testing.T) {
	e := newEnv(t, nil)
	if r, _ := e.do("GET", "/promo2026", "", nil); r.StatusCode != http.StatusNotFound {
		t.Fatalf("want 404, got %d", r.StatusCode)
	}
	e.create(map[string]any{"url": "https://example.com/promo", "alias": "promo2026"})
	if r, _ := e.do("GET", "/promo2026", "", nil); r.StatusCode != http.StatusFound {
		t.Fatalf("new alias must work immediately despite the cached 404, got %d", r.StatusCode)
	}
}

func TestUpdateInvalidatesCacheAndExpiryDisable(t *testing.T) {
	e := newEnv(t, nil)
	e.create(map[string]any{"url": "https://example.com/v1", "alias": "moving"})
	e.do("GET", "/moving", "", nil) // warm the cache

	resp, out := e.do("PATCH", "/api/links/moving", e.key, map[string]any{"url": "https://example.com/v2"})
	if resp.StatusCode != 200 || out["url"] != "https://example.com/v2" {
		t.Fatalf("patch: %d %v", resp.StatusCode, out)
	}
	if r, _ := e.do("GET", "/moving", "", nil); r.Header.Get("Location") != "https://example.com/v2" {
		t.Fatalf("stale cache: %s", r.Header.Get("Location"))
	}

	e.do("PATCH", "/api/links/moving", e.key, map[string]any{"disabled": true})
	if r, _ := e.do("GET", "/moving", "", nil); r.StatusCode != http.StatusGone {
		t.Fatalf("disabled link: want 410, got %d", r.StatusCode)
	}
	e.do("PATCH", "/api/links/moving", e.key, map[string]any{"disabled": false})

	// Expiry: the link is valid now, then the clock moves past expires_at.
	e.do("PATCH", "/api/links/moving", e.key, map[string]any{"expires_at": time.Now().Add(time.Hour).Format(time.RFC3339)})
	if r, _ := e.do("GET", "/moving", "", nil); r.StatusCode != http.StatusFound {
		t.Fatalf("before expiry: %d", r.StatusCode)
	}
	e.srv.now = func() time.Time { return time.Now().Add(2 * time.Hour) }
	if r, _ := e.do("GET", "/moving", "", nil); r.StatusCode != http.StatusGone {
		t.Fatalf("after expiry: want 410, got %d", r.StatusCode)
	}
	_, got := e.do("GET", "/api/links/moving", e.key, nil)
	if got["status"] != "expired" {
		t.Fatalf("status = %v", got["status"])
	}
	e.srv.now = time.Now
	// expires_at: null removes the expiry.
	if _, out := e.do("PATCH", "/api/links/moving", e.key, map[string]any{"expires_at": nil}); out["expires_at"] != nil {
		t.Fatalf("expiry not cleared: %v", out)
	}

	if r, _ := e.do("DELETE", "/api/links/moving", e.key, nil); r.StatusCode != http.StatusNoContent {
		t.Fatalf("delete: %d", r.StatusCode)
	}
	if r, _ := e.do("GET", "/moving", "", nil); r.StatusCode != http.StatusNotFound {
		t.Fatalf("deleted link must not be served from cache, got %d", r.StatusCode)
	}
}

func TestValidationAndOwnership(t *testing.T) {
	e := newEnv(t, nil)
	for _, body := range []map[string]any{
		{"url": "javascript:alert(1)"},
		{"url": "http://192.168.1.1/"},
		{"url": "https://sho.rt/loop"},
		{"url": "https://example.com", "alias": "api"},
		{"url": "https://example.com", "alias": "x"},
		{"url": "https://example.com", "expires_in": -5},
		{"url": "https://example.com", "expires_at": "2001-01-01T00:00:00Z"},
	} {
		if r, out := e.do("POST", "/api/links", e.key, body); r.StatusCode != http.StatusUnprocessableEntity {
			t.Errorf("%v: want 422, got %d %v", body, r.StatusCode, out)
		}
	}
	if r, _ := e.do("POST", "/api/links", e.key, map[string]any{"url": "https://e.com", "bogus": 1}); r.StatusCode != 400 {
		t.Errorf("unknown field: want 400, got %d", r.StatusCode)
	}
	if r, _ := e.do("POST", "/api/links", "", map[string]any{"url": "https://example.com"}); r.StatusCode != 401 {
		t.Errorf("no key: want 401, got %d", r.StatusCode)
	}
	if r, _ := e.do("POST", "/api/links", "snip_wrong", map[string]any{"url": "https://example.com"}); r.StatusCode != 401 {
		t.Errorf("bad key: want 401, got %d", r.StatusCode)
	}

	e.create(map[string]any{"url": "https://example.com", "alias": "mine"})
	if r, _ := e.do("POST", "/api/links", e.key, map[string]any{"url": "https://example.org", "alias": "mine"}); r.StatusCode != http.StatusConflict {
		t.Errorf("duplicate alias: want 409, got %d", r.StatusCode)
	}
	other, _, _ := e.st.NewAPIKey(context.Background(), "someone else")
	for _, m := range []string{"GET", "DELETE"} {
		if r, _ := e.do(m, "/api/links/mine", other, nil); r.StatusCode != http.StatusNotFound {
			t.Errorf("%s by non-owner: want 404, got %d", m, r.StatusCode)
		}
	}
	if r, _ := e.do("GET", "/api/links/mine/stats", other, nil); r.StatusCode != http.StatusNotFound {
		t.Errorf("stats by non-owner: want 404, got %d", r.StatusCode)
	}
}

func TestPagination(t *testing.T) {
	e := newEnv(t, nil)
	for i := 0; i < 5; i++ {
		e.create(map[string]any{"url": fmt.Sprintf("https://example.com/%d", i)})
	}
	var codes []string
	cursor := ""
	for page := 0; page < 5; page++ {
		path := "/api/links?limit=2"
		if cursor != "" {
			path += "&cursor=" + cursor
		}
		_, out := e.do("GET", path, e.key, nil)
		for _, it := range out["items"].([]any) {
			codes = append(codes, it.(map[string]any)["code"].(string))
		}
		next, _ := out["next_cursor"].(string)
		if next == "" {
			break
		}
		cursor = next
	}
	if len(codes) != 5 {
		t.Fatalf("paged through %d links, want 5: %v", len(codes), codes)
	}
}

func TestRateLimits(t *testing.T) {
	e := newEnv(t, func(c *config.Config) {
		c.RedirectLimit = config.Limit{Rate: 0.01, Burst: 3}
		c.CreateLimit = config.Limit{Rate: 0.01, Burst: 2}
	})
	e.create(map[string]any{"url": "https://example.com/a", "alias": "limited"})
	e.create(map[string]any{"url": "https://example.com/b"})
	r, _ := e.do("POST", "/api/links", e.key, map[string]any{"url": "https://example.com/c"})
	if r.StatusCode != http.StatusTooManyRequests || r.Header.Get("Retry-After") == "" {
		t.Fatalf("3rd create: want 429 with Retry-After, got %d", r.StatusCode)
	}

	for i := 0; i < 3; i++ {
		if r, _ := e.do("GET", "/limited", "", nil, "X-Real-IP", "203.0.113.9"); r.StatusCode != http.StatusFound {
			t.Fatalf("redirect %d: %d", i, r.StatusCode)
		}
	}
	r, _ = e.do("GET", "/limited", "", nil, "X-Real-IP", "203.0.113.9")
	if r.StatusCode != http.StatusTooManyRequests || r.Header.Get("X-RateLimit-Remaining") != "0" {
		t.Fatalf("4th redirect from same IP: want 429, got %d", r.StatusCode)
	}
	if r, _ := e.do("GET", "/limited", "", nil, "X-Real-IP", "203.0.113.10"); r.StatusCode != http.StatusFound {
		t.Fatalf("other IP must have its own bucket, got %d", r.StatusCode)
	}
}

// A consumer reads a batch and dies before acknowledging it. Another instance reclaims and writes it, and a
// redelivery of already-written events does not double count.
func TestCrashedConsumerIsReclaimedWithoutDuplicates(t *testing.T) {
	e := newEnv(t, nil)
	e.create(map[string]any{"url": "https://example.com", "alias": "robust"})
	for i := 0; i < 10; i++ {
		e.do("GET", "/robust", "", nil, "X-Real-IP", fmt.Sprintf("203.0.113.%d", i))
	}
	e.pub.Sync()
	ctx := context.Background()

	// The "dead" instance takes delivery of everything but never ACKs.
	if _, err := e.writer.ProcessOnce(ctx); err != nil { // creates the group
		t.Fatal(err)
	}
	if err := e.st.Truncate(ctx); err != nil { // simulate: those writes never happened
		t.Fatal(err)
	}
	e.key, _, _ = e.st.NewAPIKey(ctx, "test")
	e.create(map[string]any{"url": "https://example.com", "alias": "robust"})
	link, _ := e.st.LinkByCode(ctx, "robust")
	for i := 0; i < 10; i++ {
		e.do("GET", "/robust", "", nil, "X-Real-IP", fmt.Sprintf("198.51.100.%d", i))
	}
	e.pub.Sync()
	dead := analytics.NewWriter(e.rdb, e.st, e.pub.Stream()).WithConsumer("dead-instance")
	dead.Block = 50 * time.Millisecond
	msgs, err := e.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{Group: "click-writers", Consumer: "dead-instance",
		Streams: []string{e.pub.Stream(), ">"}, Count: 100}).Result()
	if err != nil || len(msgs[0].Messages) != 10 {
		t.Fatalf("dead consumer should hold 10 pending entries: %v %v", err, msgs)
	}

	survivor := analytics.NewWriter(e.rdb, e.st, e.pub.Stream()).WithConsumer("survivor")
	survivor.ClaimIdle = time.Millisecond
	time.Sleep(5 * time.Millisecond)
	n, err := survivor.ReclaimOnce(ctx)
	if err != nil || n != 10 {
		t.Fatalf("reclaimed %d (%v), want 10", n, err)
	}
	// Redelivery of the same events (e.g. crash after commit, before XACK) is a no-op.
	clicks := make([]store.Click, 0, 10)
	for _, m := range msgs[0].Messages {
		var ev analytics.Event
		_ = json.Unmarshal([]byte(m.Values["e"].(string)), &ev)
		clicks = append(clicks, store.Click{EventID: ev.ID, LinkID: link.ID, TS: ev.TS, VisitorHash: []byte{1},
			Country: ev.Country, Device: ev.Device, Browser: ev.Browser})
	}
	if again, err := e.st.InsertClicks(ctx, clicks); err != nil || again != 0 {
		t.Fatalf("duplicate insert wrote %d rows (%v), want 0", again, err)
	}
	link, _ = e.st.LinkByCode(ctx, "robust")
	if link.ClickCount != 10 {
		t.Fatalf("click_count = %d, want 10", link.ClickCount)
	}
	if p, _ := e.rdb.XPending(ctx, e.pub.Stream(), "click-writers").Result(); p.Count != 0 {
		t.Fatalf("%d entries still pending", p.Count)
	}
}

func TestClicksForDeletedLinkDoNotBlockTheBatch(t *testing.T) {
	e := newEnv(t, nil)
	e.create(map[string]any{"url": "https://example.com/1", "alias": "keep"})
	e.create(map[string]any{"url": "https://example.com/2", "alias": "gone"})
	e.do("GET", "/keep", "", nil)
	e.do("GET", "/gone", "", nil)
	e.pub.Sync()
	e.do("DELETE", "/api/links/gone", e.key, nil)
	n, err := e.writer.ProcessOnce(context.Background())
	if err != nil || n != 1 {
		t.Fatalf("wrote %d (%v), want 1 (the deleted link's click is skipped, not fatal)", n, err)
	}
}

// With Redis down, redirects are served straight from PostgreSQL and the rate limiter fails open.
func TestRedirectsSurviveRedisOutage(t *testing.T) {
	e := newEnv(t, nil)
	e.create(map[string]any{"url": "https://example.com/up", "alias": "resilient"})

	dead := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", DialTimeout: 50 * time.Millisecond, MaxRetries: -1})
	defer dead.Close()
	pub := analytics.NewPublisher(dead, "x:", 10, 100)
	defer pub.Close()
	srv := New(e.srv.cfg, e.st, dead, pub)
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()

	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Get(ts.URL + "/resilient")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusFound || resp.Header.Get("Location") != "https://example.com/up" {
		t.Fatalf("want 302 from DB fallback, got %d", resp.StatusCode)
	}
	resp, _ = client.Get(ts.URL + "/readyz")
	resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("readyz must report the outage, got %d", resp.StatusCode)
	}
}
