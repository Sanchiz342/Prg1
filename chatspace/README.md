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
uvicorn app.main:get_app --factory --reload   # http://localhost:8000
pytest
```

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
AI assistant, React + TypeScript frontend (the bundled `frontend/index.html` is a minimal vanilla client),
CI workflow. Presence on multi-instance deployments is per-user TTL: closing one of several instances' sockets
for the same user clears presence until the next heartbeat.
