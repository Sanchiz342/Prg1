# ChatSpace

Real-time team chat (workspaces, channels, threads, reactions, presence) built to showcase
WebSockets, Redis Pub/Sub, RBAC and testing. Fully independent of the other mini-projects.

## Run

**Production-like (PostgreSQL + Redis):** `docker compose up --build` starts postgres, redis, a one-shot
`migrate` service (`python -m app.migrate`) and two API instances (:8000, :8001) that wait for it.
See *Verified with Docker Compose* below for exactly what was exercised.

**PostgreSQL is the primary database.** Full-text search, the GIN index and concurrent-safe migrations are
PostgreSQL features, and the database must be **UTF8** (migration `0002` refuses to run otherwise, because with
`SQL_ASCII` full-text search would silently never match non-ASCII text such as Ukrainian).
SQLite + in-process fakeredis exist only as a zero-setup **dev/test fallback** (substring search instead of FTS,
single process, no cross-instance fan-out).

### Database migrations (Alembic)

```bash
cd backend
export DATABASE_URL=postgresql+asyncpg://chat:chat@localhost:5432/chatspace
python -m app.migrate            # upgrade to head (also adopts DBs created before Alembic existed)
alembic current                  # show revision
alembic downgrade 0001           # step back (0002 drops the FTS column + index, data is kept)
alembic revision --autogenerate -m "describe change"   # after editing app/models.py
```

- The API **no longer creates tables**. On start it verifies the schema is at the revision it was built for
  and exits with an explanation if not. `AUTO_MIGRATE=true` makes it migrate on startup instead
  (dev / single instance). Several processes may migrate simultaneously (PostgreSQL advisory lock).
- `0001` is the baseline (everything `create_all()` used to create); `0002` adds a generated `tsvector`
  column + GIN index on `messages` (PostgreSQL only; no-op elsewhere); `0003` adds `channel_reads` (unread markers)
  and backfills one per user and visible channel so existing history does not suddenly count as unread.
- A database created by the pre-Alembic version is detected (tables but no `alembic_version`), stamped as
  `0001` and upgraded — existing rows are preserved and become searchable.

### Search

`GET /api/workspaces/{id}/search?q=…` — on PostgreSQL: full-text over `messages.search_vector`
(`simple` configuration, because the content is multilingual and PostgreSQL ships no Ukrainian stemmer),
every word is a prefix match and all words must match (`datab pool` finds "database connection pool"),
results ranked with `ts_rank_cd`. The query text is reduced to word tokens, so tsquery syntax can't be injected.
The visibility filter (public channels + private channels you belong to, in this workspace) is part of the same SQL
query, so a message you can't read can never be returned.

### Unread counters

- `GET /api/workspaces/{id}/unread` → `{"channels": {"<channel_id>": {"unread": n, "mentions": m}}}` (non-zero entries
  only, visible channels only); `POST /api/channels/{id}/read` moves the caller's marker to the newest message
  (never backwards) and clears that channel's mention/reply notifications.
- Unread = messages newer than the user's marker (`channel_reads.last_read_at`, a message timestamp, not wall-clock),
  not deleted, not written by the user; replies count. No marker yet = since the channel was created, so a *new*
  channel's first messages are unread for everyone, while a user who is added to a workspace/channel starts caught up.
- Live: every message sends a content-free `channel.activity` ping (public channel → workspace room, private → only
  that channel's members' user rooms, so nothing leaks to non-members) and reading sends `channel.read` to the
  user's own room, so a second tab/device clears its badge. All of it goes through Redis, i.e. across instances.
- Client: badges (capped at `99+`, red when mentioned), tab title `(n) ChatSpace`, read-while-viewing only when the
  tab is visible and scrolled to the live end. Read requests are single-flight and sent immediately, never
  debounced: a delayed request would mark whatever arrived meanwhile as read (found by the Compose run).
- Only the open workspace's counters are tracked live; other workspaces are not shown yet.

### Dev without Docker

```bash
cd backend && pip install -r requirements-dev.txt
AUTO_MIGRATE=true uvicorn app.main:get_app --factory --reload   # SQLite file + fakeredis, API on :8000
pytest                                                           # SQLite
TEST_DATABASE_URL=postgresql+asyncpg://user:pw@localhost:5432/chatspace_test pytest   # full suite on PostgreSQL
                                                                 # (needs a UTF8 database; its public schema is wiped per test)

cd frontend && npm install
npm run dev        # Vite on http://localhost:5173, proxies /api and /ws to :8000
npm test           # vitest
npm run build      # -> frontend/dist, served by the backend at /
```

## Verified with Docker Compose

`docker compose up --build` on clean volumes, two API instances (:8000 → alice, :8001 → bob), checked with
`e2e/compose_e2e.mjs` (Playwright, real Chromium) and `e2e/two_instances.py` (raw HTTP + WebSocket per instance):

- `migrate` exits 0 and finishes *before* the API containers start; the API containers log no migration activity,
  and an API container started against an empty database refuses to start and creates no tables.
- PostgreSQL database is UTF8; `alembic_version` = `0002`; GIN index present; Redis answers and both instances subscribe to the bus.
- Frontend is served by both instances; each instance holds its own WebSocket and Redis publishes from both.
- Browser flow across instances: register/login, workspace, public + private channels (private hidden from non-members),
  messages both directions, typing, presence, reactions, replies, edit, delete, mention/reply notifications,
  offline → reconnect catch-up, full-text search with private-channel isolation, unread badges across instances
  (incl. mention badge, no leak from private channels, two tabs, reload).
- Upgrade path: a stack started on the previous release (schema 0002, with data) was rebuilt on the same volume;
  `migrate` applied 0003, kept all rows and backfilled the markers (existing history showed 0 unread).
- `docker compose down` then `up` with the same volume: users, messages, migration state kept, `migrate` is a no-op,
  login works on the other instance, search still works.

Bugs this run found (fixed, with regression tests): a socket opened before a user joined a workspace never received
that workspace's events and a removed member kept receiving channel events (hub now applies membership changes
across instances); a rejected WebSocket token surfaced as a bare 403 that browsers can't distinguish from a network
error (now accepted-then-closed with code 4401 so the client logs out instead of retrying forever); a `SyntaxWarning`
under Python 3.12.

Not covered: TLS/reverse proxy, multi-node Redis, load, and Redis persistence (presence/typing are ephemeral by design).
Run it yourself: `docker compose up --build -d`, then `cd e2e && npm i && CHROME=/path/to/chromium npm run flow`
(use a fresh volume: the flow registers `alice`/`bob`).

## Frontend (React + TypeScript + Vite)

TanStack Query holds server state; the WebSocket only patches that cache (`src/realtime.tsx`, pure helpers in `src/cache.ts`).
- `src/ws.ts`: `RealtimeClient` with exponential-backoff reconnect + jitter, automatic channel resubscribe, heartbeat
  (`AWAY` when the tab is hidden); a 4401 close logs the user out. After a reconnect all visible queries are refetched
  so nothing missed while offline is lost, and a banner shows the connection state.
- Optimistic UI: sent messages appear instantly as pending, are reconciled with the WebSocket echo (no duplicates),
  and show Retry/Dismiss on failure; reactions toggle optimistically with rollback.
- Cursor pagination ("Load older messages", scroll position preserved), threads, edit/delete, reactions, typing,
  presence, notifications, ACL-aware search, loading/error/empty states, responsive layout (drawers on mobile).

## Architecture

- **FastAPI** REST under `/api`, WebSocket at `/ws?token=<jwt>`.
- **PostgreSQL** (primary; SQLite only as dev/test fallback) via async SQLAlchemy + Alembic migrations: users, workspaces, members (OWNER/ADMIN/MEMBER),
  channels (PUBLIC/PRIVATE), messages (soft delete, `reply_to`), reactions, notifications.
- **Redis**: Pub/Sub bus (`app/realtime/hub.py`) so any instance reaches sockets on every other instance;
  presence (`presence:user:<id>` + TTL, refreshed by heartbeat) and typing state are ephemeral and never hit SQL.
- **Authz**: every REST call checks workspace role / channel membership; non-members get 404.
  A socket must authenticate, and `subscribe` re-checks channel access, so private channels can't be eavesdropped.
- Cursor pagination: `GET /api/channels/{id}/messages?limit=50&before=<message_id>`.
- Search is ACL-filtered PostgreSQL full-text search (see *Search*).
- Ops: `/api/health`, `/metrics` (Prometheus text), per-IP rate limit on `/auth/*`.

### WebSocket protocol

Client → server: `subscribe`, `typing.started`, `typing.stopped`, `heartbeat` (optional `status: AWAY`).
Server → client: `ready`, `subscribed`, `error`, `message.created|updated|deleted`, `reaction.added|removed`,
`user.online|offline`, `typing.started|stopped`, `channel.created`, `notification.created`.

## Status / not done yet

Done: stages 1–5 core (auth, RBAC, messaging, real-time, Redis distribution, metrics, tests, Docker Compose stack verified end to end).
Not yet: unread counts for workspaces other than the open one, a "new messages" divider, DMs, attachments (MinIO),
AI assistant, CI workflow. The frontend has no browser E2E suite in the repo yet (the flow was verified manually with Playwright against a real backend). Presence on multi-instance deployments is per-user TTL: closing one of several instances' sockets
for the same user clears presence until the next heartbeat.
