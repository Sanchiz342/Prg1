# Architecture

```
                      ┌──────────────┐
  client ──▶ nginx ──▶│ app instance │──┐     (any number of identical instances)
                      └──────────────┘  │
                         │   ▲          │
     1. rate limit (Lua) │   │ 2. link  │ 3. click event (async, buffered, pipelined XADD)
                         ▼   │  lookup  ▼
                      ┌──────────────────────────────┐
                      │ Redis                         │
                      │  rl:<bucket>:<id>   hashes    │  token buckets
                      │  link:<code>        strings   │  cache (+ negative entries)
                      │  clicks             stream    │  consumer group "click-writers"
                      └──────────────────────────────┘
                         │ miss                    │ XREADGROUP / XAUTOCLAIM (batches of ≤ 500)
                         ▼                         ▼
                      ┌──────────────────────────────┐
                      │ PostgreSQL                    │  api_keys · links · clicks
                      └──────────────────────────────┘
```

## Redirect path (`GET /{code}`)
1. Reject syntactically impossible codes before touching Redis.
2. Token bucket per client IP (`X-Real-IP` from nginx when `TRUST_PROXY=true`).
3. `link:<code>` from Redis. On a miss, load from PostgreSQL through `singleflight` and cache the entry for
   `min(CACHE_TTL, time until expiry)`; unknown codes are cached as `-` for `NEGATIVE_CACHE_TTL`.
4. `404` / `410` (disabled, expired) or `302`.
5. GET (not HEAD) produces an event: UUID, link id, UTC time, visitor HMAC, country, referrer host, device,
   browser, bot flag. `Publish` is a non-blocking channel send; a full buffer drops and counts the event.

## Click pipeline
- **Publisher** (one per instance) batches events every 100 ms or 256 events into one pipelined round-trip of
  `XADD … MAXLEN ~ STREAM_MAXLEN`.
- **Writer** (one per instance, `WORKER_ENABLED`) is a member of consumer group `click-writers`:
  - `XREADGROUP >` reads up to 500 new entries and inserts them in one statement (`unnest` arrays):
    - `ON CONFLICT (event_id) DO NOTHING`, so a redelivery is harmless;
    - `WHERE EXISTS (link)`, so clicks for a deleted link are skipped instead of failing the batch on the foreign key;
    - the same statement bumps `links.click_count` by the number of rows actually inserted (a CTE chain).
  - It sends `XACK` only after the commit. If the insert fails, the entries stay pending.
  - Every `ClaimIdle / 2`, `XAUTOCLAIM` takes over entries that have been pending longer than `ClaimIdle`. This picks
    up batches from crashed instances and failed attempts.
  - It drops malformed entries and acknowledges them, so one poison message can't stall the group.
  - The `snip_click_stream_pending` gauge shows the backlog.

## Rate limiting
One Lua script per check does the following atomically:
1. Reads `TIME`.
2. Refills the bucket by elapsed time × rate, capped at the burst.
3. Takes a token or computes the retry delay.
4. Writes the state back, with a `PEXPIRE` long enough for a full refill.

Because Redis executes scripts one at a time, all instances see one consistent bucket. The concurrency test checks
that 200 concurrent callers get exactly `burst` passes.

## Schema
| table | notes |
|---|---|
| `api_keys` | `key_hash` = SHA-256 of the key; the plaintext is shown once by `snip create-key` |
| `links` | `code` unique (case-sensitive base62, 7 chars random or a 3–32 char alias), `expires_at`, `disabled`, denormalised `click_count` |
| `clicks` | `event_id` UUID PK, `(link_id, ts)` index, `visitor_hash` (16 bytes), `country`, `referrer_host`, `device`, `browser`, `is_bot` |

Migrations are embedded SQL files, applied at startup under a PostgreSQL advisory lock. Every connection uses
`timezone=UTC`, so daily buckets are UTC days.

## Failure modes
| failure | behaviour |
|---|---|
| Redis down | redirects are served from PostgreSQL (`cache=bypass` metric); rate limits fail open (or `503` with `RATE_LIMIT_FAIL_OPEN=false`); click events are dropped and counted; `/readyz` returns `503` |
| PostgreSQL down | cached redirects keep working; API and cache misses return `500`; the writer stops and resumes with no loss, since entries stay in the stream |
| an instance crashes | its pending stream entries are reclaimed by another instance after `ClaimIdle` (30 s); events still in its in-memory buffer are lost |
| a link is deleted while its clicks are in flight | its clicks are skipped; the rest of the batch is written |
| a link is edited | the cache entry is deleted; if that delete fails, the old target is served until the TTL expires (logged) |

The Redis outage, crashed consumer and deleted-link cases have integration tests (`internal/api/e2e_test.go`).
The PostgreSQL-down behaviour follows from the code paths above but has no automated test yet.
