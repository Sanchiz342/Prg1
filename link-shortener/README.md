# ✂️ Snip — URL shortener with click analytics

A production-style URL shortener in Go: short links with custom aliases and expiry, a redirect path that never
waits for the database, click analytics (countries, referrers, devices, browsers, unique visitors, bots), a
distributed rate limiter, and a deployment of two instances behind nginx sharing PostgreSQL and Redis.

![Snip dashboard](docs/screenshot.png)

Self-contained mini-project: everything lives in this folder.

## Highlights
- **Hot path in Redis.** A redirect is one rate-limit script + one cache read. Unknown codes are negatively cached,
  cache entries never outlive the link's expiry, and concurrent misses collapse into one query (singleflight).
- **Analytics off the request path.** Clicks go to an in-process buffer → **Redis Stream** → a **consumer group**
  that any instance can join → batched `INSERT` into PostgreSQL. At-least-once delivery, made idempotent by an
  `event_id` primary key; entries abandoned by a crashed instance are reclaimed with `XAUTOCLAIM`.
- **Distributed token-bucket rate limiting** as one atomic Lua script, using Redis' clock (no skew between
  instances). Per-IP for redirects, per-API-key for the API, standard `X-RateLimit-*` / `Retry-After` headers.
- **Degrades instead of failing:** with Redis down, redirects are served straight from PostgreSQL and the rate
  limiter fails open (configurable); `/readyz` reports the outage.
- **Privacy by design:** no raw IPs are stored. Unique visitors use an HMAC of IP + User-Agent keyed by a server
  secret and the UTC date, so it rotates daily.
- **Abuse checks on targets:** only http(s), no `user@host` phishing URLs, no private/loopback/link-local
  addresses or single-label intranet hosts, no loops through the shortener itself.
- REST API with API keys (stored as SHA-256 hashes), keyset pagination, ownership isolation (other owners' links
  are 404, not 403); Prometheus metrics; graceful shutdown that flushes buffered clicks; ~48 MB distroless image.

## Quick start (Docker)
```bash
docker compose up --build
```
- Dashboard: <http://localhost:8080/app/>. API key: `snip_demo_key_change_me` (set `BOOTSTRAP_API_KEY` to change it).
- Two app instances (`app1`, `app2`) sit behind nginx. Both run migrations at start; an advisory lock makes this safe.

Demo traffic (synthetic IPs, countries, browsers and referrers) goes straight to instance 1, which compose exposes
on `127.0.0.1:8081` for load tests:
```bash
go run ./cmd/loadgen -base http://127.0.0.1:8081 -key snip_demo_key_change_me -links 10 -duration 10s
```

## Running locally
Requires Go 1.26+, PostgreSQL 14+ and Redis 6.2+ (for `XAUTOCLAIM`).
```bash
export DATABASE_URL=postgres://postgres:postgres@localhost:5432/shortener?sslmode=disable REDIS_URL=redis://localhost:6379/0
go run ./cmd/snip create-key me          # prints an API key once
go run ./cmd/snip serve                  # http://localhost:8080
```
All settings are listed in [`.env.example`](.env.example).

## API
All `/api` endpoints need `Authorization: Bearer <api key>`. Errors look like `{"error": {"code": "...", "message": "..."}}`.

| Method | Path | |
|---|---|---|
| `POST` | `/api/links` | `{"url": "...", "alias"?: "launch", "expires_at"?: RFC 3339 \| "expires_in"?: seconds}` → `201` |
| `GET` | `/api/links?limit=20&cursor=…` | your links, newest first, with click counts |
| `GET` | `/api/links/{code}` | one link (`status`: `active` / `expired` / `disabled`) |
| `PATCH` | `/api/links/{code}` | `{"url"?, "expires_at"?: time \| null, "disabled"?}` — invalidates the cache |
| `DELETE` | `/api/links/{code}` | deletes the link and its clicks |
| `GET` | `/api/links/{code}/stats?from=YYYY-MM-DD&to=YYYY-MM-DD` | daily series, uniques, bots, top countries / referrers / devices / browsers |
| `GET` | `/{code}` | `302` to the target; `404` unknown, `410` expired or disabled, `429` rate limited |
| `GET` | `/healthz`, `/readyz`, `/metrics` | liveness, dependencies, Prometheus |

```bash
curl -X POST localhost:8080/api/links -H "Authorization: Bearer $KEY" \
     -d '{"url": "https://go.dev/doc/", "alias": "go-docs", "expires_in": 604800}'
```
Redirects are `302` with `Cache-Control: private, no-store` on purpose. A `301` would be cached by browsers
forever: repeat clicks would never be counted, and editing or expiring the link would not reach those visitors.

## Measurements
Everything ran in one 4-vCPU cloud sandbox: server, PostgreSQL 16, Redis 7 and the load generator on the same
machine, so they competed for CPU. Treat the numbers as an order of magnitude, not a benchmark.

**Redirect throughput**: one instance run directly (no Docker), `loadgen -c 32 -duration 10s`, 15 links,
20 000 synthetic client IPs, 2% unknown codes:

| requests | throughput | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 84 623 | 8 453 req/s | 3.3 ms | 6.5 ms | 11.7 ms | 59 ms |

All 82 942 redirects (`302`) produced click events, and the stream writer stored all 82 942 rows in PostgreSQL
(`snip_clicks_published_total` = `snip_clicks_written_total`, 0 pending). The cache answered 82 898 lookups;
the 1 723 misses were mostly the random unknown codes.

**Two instances behind nginx** (`docker compose`): I sent 100 sequential redirects from one client in about a second.
- The shared rate-limit bucket (burst 60, refilling at 20/s) let 80 through and returned 20 × `429`. The rejections
  fell on both instances (9 and 11), because they share one bucket.
- nginx split the requests 42 / 38 between the instances. Through the consumer group, each instance wrote 40 of
  the clicks, regardless of which one received them. PostgreSQL had exactly 80 rows.

## Tests
```bash
go test ./...                                   # unit tests; integration tests skip without databases
SNIP_TEST_DATABASE_URL=postgres://postgres@localhost:5432/shortener_test?sslmode=disable \
SNIP_TEST_REDIS_URL=redis://localhost:6379/1 go test -race ./...
```
There are 20 test functions. The integration tests run against real PostgreSQL and Redis and cover:
- the full lifecycle, with analytics checked end to end;
- the negative-cache entry being cleared when a new alias is created;
- cache invalidation on update and delete;
- expiry (`410`) and disabled links;
- ownership isolation;
- pagination;
- per-IP and per-key `429`s;
- a crashed consumer whose pending clicks another instance reclaims, with redelivery not double counting;
- clicks for a link deleted mid-flight not blocking a batch;
- redirects surviving a Redis outage.

The rate-limiter test runs 200 concurrent callers against one bucket and checks that exactly `burst` pass.

## Design notes & limitations
- **Why a stream and not `INSERT` per click?** It decouples request rate from DB write rate and lets the DB take
  batches of up to 500 rows in one statement. Losing analytics on a Redis outage is the accepted cost. Clicks still
  in the in-process buffer when the process crashes are lost too; a graceful shutdown flushes them.
- **Statistics are eventually consistent** (usually under a second behind). They are computed from raw click rows
  with a `(link_id, ts)` index. That is fine at this scale; at higher volume, add daily rollup tables or move clicks
  to a column store.
- **Geo** comes from a CDN header (`CF-IPCountry`), trusted only with `TRUST_PROXY=true`. There is no bundled
  GeoIP database: the free MaxMind database needs a license, so `analytics.Country` is the place to plug one in.
  Without a CDN, countries show as unknown (`ZZ`).
- **User-Agent parsing** uses explainable heuristics, not a full UA database. Bots are counted but kept out of the
  human numbers.
- **Unique visitors** are counted per day, because the visitor hash rotates daily. A visitor who returns on another
  day counts again; that is the privacy trade-off.
- The cache is Redis only. An in-process L1 cache would be faster but would need cross-instance invalidation
  (pub/sub). That is a natural next step.

Architecture details: [docs/architecture.md](docs/architecture.md).
