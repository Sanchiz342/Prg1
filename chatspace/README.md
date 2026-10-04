# ChatSpace

Real-time team chat (workspaces, channels, threads, reactions, presence) built to showcase
WebSockets, Redis Pub/Sub, RBAC and testing. Fully independent of the other mini-projects.

## Run

**Production-like (PostgreSQL + Redis):** `docker compose up --build` starts postgres, redis, a one-shot
`migrate` service (`python -m app.migrate`) and two API instances (:8000, :8001) that wait for it.
*(The compose file has not been exercised end to end yet — that is the next planned step.)*

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
  column + GIN index on `messages` (PostgreSQL only; no-op elsewhere).
- A database created by the pre-Alembic version is detected (tables but no `alembic_version`), stamped as
  `0001` and upgraded — existing rows are preserved and become searchable.

### Search

`GET /api/workspaces/{id}/search?q=…` — on PostgreSQL: full-text over `messages.search_vector`
(`simple` configuration, because the content is multilingual and PostgreSQL ships no Ukrainian stemmer),
every word is a prefix match and all words must match (`datab pool` finds "database connection pool"),
results ranked with `ts_rank_cd`. The query text is reduced to word tokens, so tsquery syntax can't be injected.
The visibility filter (public channels + private channels you belong to, in this workspace) is part of the same SQL
query, so a message you can't read can never be returned.

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

Done: stages 1–5 core (auth, RBAC, messaging, real-time, Redis distribution, metrics, tests; Dockerfile and compose written but not yet run).
Not yet: verified Docker Compose run, unread counters, DMs, attachments (MinIO),
AI assistant, CI workflow. The frontend has no browser E2E suite in the repo yet (the flow was verified manually with Playwright against a real backend). Presence on multi-instance deployments is per-user TTL: closing one of several instances' sockets
for the same user clears presence until the next heartbeat.
