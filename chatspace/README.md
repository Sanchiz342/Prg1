# ChatSpace

Real-time team chat (workspaces, channels, threads, reactions, presence) built to showcase
WebSockets, Redis Pub/Sub, RBAC and testing. Fully independent of the other mini-projects.

## Run

```bash
docker compose up --build      # UI + API on :8000 (second instance on :8001)
```

Dev without Docker (SQLite + in-process fake Redis, single instance):

```bash
cd backend && pip install -r requirements-dev.txt
uvicorn app.main:get_app --factory --reload   # API on http://localhost:8000
pytest

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
- **PostgreSQL** (SQLite for dev/tests) via async SQLAlchemy: users, workspaces, members (OWNER/ADMIN/MEMBER),
  channels (PUBLIC/PRIVATE), messages (soft delete, `reply_to`), reactions, notifications.
- **Redis**: Pub/Sub bus (`app/realtime/hub.py`) so any instance reaches sockets on every other instance;
  presence (`presence:user:<id>` + TTL, refreshed by heartbeat) and typing state are ephemeral and never hit SQL.
- **Authz**: every REST call checks workspace role / channel membership; non-members get 404.
  A socket must authenticate, and `subscribe` re-checks channel access, so private channels can't be eavesdropped.
- Cursor pagination: `GET /api/channels/{id}/messages?limit=50&before=<message_id>`.
- Search is ACL-filtered (`GET /api/workspaces/{id}/search?q=`), currently `ILIKE`.
- Ops: `/api/health`, `/metrics` (Prometheus text), per-IP rate limit on `/auth/*`.

### WebSocket protocol

Client → server: `subscribe`, `typing.started`, `typing.stopped`, `heartbeat` (optional `status: AWAY`).
Server → client: `ready`, `subscribed`, `error`, `message.created|updated|deleted`, `reaction.added|removed`,
`user.online|offline`, `typing.started|stopped`, `channel.created`, `notification.created`.

## Status / not done yet

Done: stages 1–5 core (auth, RBAC, messaging, real-time, Redis distribution, Docker, metrics, tests).
Not yet: Alembic migrations (tables are created on startup), PostgreSQL full-text search, DMs, attachments (MinIO),
AI assistant, CI workflow. The frontend has no browser E2E suite in the repo yet (the flow was verified manually with Playwright against a real backend). Presence on multi-instance deployments is per-user TTL: closing one of several instances' sockets
for the same user clears presence until the next heartbeat.
