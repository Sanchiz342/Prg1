# 🚀 DeployBoard

A mini CI/CD platform. Connect a Git repository; every push to a configured branch is cloned, tested, built, packaged
into a Docker image, deployed and health-checked in a chosen **environment** (development / staging / production) —
while you watch live logs in the dashboard, and roll back with one click.

Self-contained mini-project: everything lives in this folder and nothing outside it is used.

```
GitHub ──webhook──▶ ┌────────────┐   ┌───────┐   ┌──────────────┐   ┌─────────────────────┐
                    │ API (HTTP) │──▶│ Queue │──▶│ Worker pool  │──▶│ Pipeline engine     │
 Browser (React) ──▶│ + WebSocket│   │ mem / │   │ (embedded or │   │ checkout → shell →  │
                    └─────┬──────┘   │ Redis │   │  standalone) │   │ docker build → run →│
                          │          └───────┘   └──────┬───────┘   │ health check        │
                          ▼                             │           └──────────┬──────────┘
                  SQLite / PostgreSQL ◀── state, logs ──┘     runner: local process | Docker container
                          ▲                                                    │
                          └──── event bus (in-process / Redis pub/sub) ─ live logs
```

## Stack

| Part | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2, Pydantic 2, Fernet (`cryptography`), `redis` client |
| Database | SQLite (default) or PostgreSQL (`DB_URL`) |
| Queue / events | in-process queue (default) or Redis list + pub/sub |
| Frontend | React 18 + TypeScript + Vite, React Router (no UI framework) |
| Tests | pytest (backend), vitest (frontend helpers), Playwright run by hand for the UI smoke test |

## Project structure

```
deploy-board/
├── backend/
│   ├── app/
│   │   ├── api/          HTTP + WebSocket routers (thin: parse request, call a service)
│   │   ├── services/     business rules: auth, projects/environments/secrets, deployments, webhooks
│   │   ├── db/           models.py (schema), repositories.py (queries only), session.py (engine)
│   │   ├── pipeline/     definition.py (stage kinds, validation), engine.py (execution)
│   │   ├── runners/      local.py, docker.py, base.py (process streaming, timeouts)
│   │   ├── jobs/         JobQueue interface + memory / Redis implementations
│   │   ├── worker.py     WorkerPool (claim → run → recover);  worker_main.py = standalone worker process
│   │   ├── events.py     live event bus (+ Redis bridge);  github.py = signature + payload helpers
│   │   └── security.py, crypto.py, config.py, errors.py, schemas.py, main.py
│   └── tests/
├── frontend/src/         api/ (typed client), hooks/, components/, pages/, lib/
├── Dockerfile            API + worker image (builds the frontend too)
├── docker-compose.yml    PostgreSQL + Redis + API + worker
└── .env.example
```

Layering: `api → services → db/repositories`. Routers contain no queries and no rules; repositories contain no rules.

## Pipeline lifecycle

A **project** (repository + pipeline) has up to three **environments**. Each environment has its own branch, health URL,
port, variables/secrets and deployment history. A **deployment** always belongs to one environment.

1. A deployment is created `QUEUED` (manual deploy, webhook push, or rollback) and its id is put on the queue. The HTTP
   request returns immediately — **nothing is ever executed inside a request**.
2. A worker takes the id and **claims** it atomically in the database (`QUEUED → RUNNING`, compare-and-set). Only one
   deployment per environment runs at a time; others wait and are re-queued.
3. The engine runs the stages in order. Stage kinds: `checkout`, `shell`, `docker_build`, `docker_run`, `healthcheck`.
   Every stage records status, exit code, attempts and timestamps; every log line is stored and streamed.
4. Stage statuses: `PENDING → RUNNING → SUCCESS | FAILED`, or `SKIPPED`. The first failing stage fails the
   deployment; all later stages become `SKIPPED`. A deployment ends `SUCCESS` or `FAILED`; no stage is ever left `RUNNING`.
5. Reliability rules: per-stage `timeout` (default 600 s; the whole process group is killed), `retries` + `retry_delay`
   (checkout retries twice by default; timeouts are never retried), a deployment-wide time limit (1800 s), and a
   worker-crash path (below). Default pipeline: `checkout → install → test → build → docker → deploy → healthcheck`.
6. The workspace is deleted when the deployment ends (`DB_KEEP_WORKSPACE=1` keeps it).

Example custom pipeline (`PATCH /api/projects/{id}`):

```json
{"pipeline": [
  {"name": "checkout", "kind": "checkout"},
  {"name": "test", "kind": "shell", "run": "npm ci && npm test", "timeout": 300, "retries": 1},
  {"name": "image", "kind": "docker_build"},
  {"name": "deploy", "kind": "docker_run"},
  {"name": "health", "kind": "healthcheck", "attempts": 10, "interval": 2}
]}
```

## Worker architecture

* **Queue** (`jobs/`): `JobQueue` = `enqueue / reserve / size`. `MemoryQueue` for one process, `RedisQueue`
  (`DB_REDIS_URL`) to share jobs between the API and any number of worker processes. The database is the source of truth;
  the queue only wakes workers, so duplicate or lost entries are harmless.
* **Workers**: embedded in the API process by default (`DB_WORKERS` threads), or standalone:
  `DB_EMBEDDED_WORKERS=0` for the API and `python -m app.worker_main` for one or more workers (needs Redis and a shared
  database).
* **Recovery**: a running job holds a *lease* renewed by a heartbeat. If the worker dies, the lease expires; a *reaper*
  re-queues the job (stages reset, `attempts + 1`) up to `DB_MAX_ATTEMPTS`, then marks it `FAILED`. On startup, every
  `QUEUED` job is re-enqueued (the in-memory queue does not survive a restart) and expired `RUNNING` jobs are re-queued.
* **Live logs**: workers publish events to the event bus; the API relays them over `WS /ws/deployments/{id}`. With Redis
  the bus also uses pub/sub, so logs produced by a *separate* worker process reach the browser. The socket replays stored
  logs first, so a reconnect never loses or duplicates lines.

## Environments, secrets, auth

* Variables/secrets belong to **one environment**; a deployment only ever receives its own environment's values (plus
  `DEPLOYBOARD_PROJECT / _ENVIRONMENT / _DEPLOYMENT`). Rollback also never crosses environments.
* **Secrets** are Fernet-encrypted at rest, write-only through the API (listing shows `••••••••`), injected only into the
  runner/app environment, passed to Docker through a private `--env-file` (not on the command line), and masked as `***`
  in stored logs. The webhook secret is encrypted at rest and shown only to the project's owner/admin.
* **Auth**: the first account (`POST /api/auth/register`, only while no user exists) becomes admin; admins create further
  users (`POST /api/users`). Passwords: scrypt with per-user salt. Sessions: opaque random bearer tokens, only their
  SHA-256 is stored, 7-day expiry, revocable by logout. Users see and change only **their own projects and deployments**
  (someone else's resource answers `404`); admins see everything. The GitHub webhook is the one unauthenticated endpoint —
  its credential is the HMAC signature. WebSockets authenticate with `?token=`.

## GitHub webhook

Add a `push` webhook (content type `application/json`) to `https://<host>/api/webhooks/github` using the project's webhook
secret (shown on the project page). The server finds the project by repository URL (https / ssh / `.git` forms), verifies
`X-Hub-Signature-256` (HMAC-SHA256, constant-time compare) against *that project's* secret, then creates a deployment for
every environment whose `branch` equals the pushed branch and has `auto_deploy` on. Pings, other events, tag pushes,
branch deletions and non-matching branches are acknowledged but ignored.

## Rollback

`POST /api/deployments/{id}/rollback` redeploys an existing image without rebuilding: the deployment itself if it
succeeded, otherwise the latest earlier **successful deployment of the same environment** that produced an image. It runs
only `deploy` + `healthcheck`. `409` if no such deployment exists. Note: a deploy replaces the running container
(`docker rm -f` + `docker run`) — there is no blue/green, so a failed release means a short outage until you roll back.

## Run locally (no Docker needed)

```bash
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt            # + requirements-postgres.txt for PostgreSQL
(cd ../frontend && npm install && npm run build)   # optional: the API serves frontend/dist when it exists
uvicorn app.main:app --reload                  # http://localhost:8000  (API docs: /docs)
```

Open the UI, create the admin account, add a project (a local path works as repository URL), deploy. For UI development
run `npm run dev` in `frontend/` (port 5173, proxies `/api` and `/ws` to :8000).

With Redis and a separate worker:

```bash
DB_REDIS_URL=redis://localhost:6379/0 DB_EMBEDDED_WORKERS=0 uvicorn app.main:app &
DB_REDIS_URL=redis://localhost:6379/0 DB_SECRET_KEY=<same Fernet key> python -m app.worker_main
```

## Run with Docker

```bash
export DB_SECRET_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
export POSTGRES_PASSWORD=change-me
docker compose up --build        # UI + API on http://localhost:8000
```

The stack is PostgreSQL + Redis + API + a separate worker using `DB_RUNNER=docker` (each shell stage runs in a throw-away
container; set the `RUNNER_IMAGE` variable per environment, default `node:22`). It **mounts the host Docker socket**
(root-equivalent) — use a trusted machine. The workspace is mounted at the same absolute path inside and outside the
containers because the Docker daemon resolves bind mounts on the host. From inside the stack, health URLs of deployed apps
should use `http://host.docker.internal:<port>/health`.

## Configuration

See `.env.example` for every variable (`DB_URL`, `DB_REDIS_URL`, `DB_WORKERS`, `DB_EMBEDDED_WORKERS`, `DB_LEASE_SECONDS`,
`DB_MAX_ATTEMPTS`, `DB_RUNNER`, `DB_STAGE_TIMEOUT`, `DB_DEPLOYMENT_TIMEOUT`, `DB_RUNNER_IMAGE/CPUS/MEMORY`,
`DB_DOCKER_NETWORK`, `DB_TOKEN_TTL_HOURS`, `DB_SECRET_KEY`, …). Pipelines and environment settings are per project (API/UI).

## Testing

```bash
cd backend && python -m pytest                 # everything; tests that need an unavailable service are skipped
python -m pytest -m "not docker"               # without the Docker integration tests
cd ../frontend && npm test && npm run build    # vitest + typecheck + production build
```

The backend suite uses real components wherever possible: real git repositories, real subprocesses, a real `redis-server`
process, a real PostgreSQL server, and a real Docker daemon. It covers auth and access control, secrets, environments,
pipeline stages, failure propagation / `SKIPPED`, timeouts, retries, workers (atomic claim, per-environment
serialisation, crash handling), restart recovery (including a `SIGKILL`ed worker process), webhook signatures and branch
filtering, rollback, WebSocket streaming, runners, Redis queue/event bridge, PostgreSQL.

## Verification status

Verified by actually running them in the development environment (Linux sandbox, Python 3.11, Node 22):

| Area | How it was verified |
|---|---|
| Backend logic: auth, projects, environments, secrets, pipeline, worker, recovery, webhook, rollback selection, WebSocket, runners | pytest, real subprocesses/git |
| Redis queue, Redis event bridge, standalone worker process, crash recovery of a killed worker | pytest against a real `redis-server` 7.0 |
| PostgreSQL 16 (schema, full deployment flow, atomic job claim, recovery) | pytest against a real PostgreSQL server |
| Frontend typecheck, production build, helper unit tests | `npm run build`, `vitest` |
| Frontend ↔ backend in a real browser (login, create project, deploy, live logs, failure + skipped stages, masked secrets, errors) | one-off Playwright run in Chromium (script not committed) |
| **Docker runner, `docker_build`, `docker_run`, `healthcheck`, rollback with real images, timeout cleanup, secrets via env-file** | pytest `-m docker` against a real Docker 29.6 daemon — **see the limits below** |

**Docker integration: PARTIALLY VERIFIED.** The five tests in `tests/test_docker_integration.py` really run the Docker CLI
against a real daemon (they are skipped when no daemon is reachable). The daemon in the development sandbox had **no bridge
networking**, so the tests use `DB_DOCKER_NETWORK=host`. Therefore the following are **NOT VERIFIED**:

* published ports (`docker run -p port:port`) and the default bridge network;
* `docker compose up` of the full stack (only `docker compose config` — static syntax validation — was run);
* building the DeployBoard `Dockerfile` itself (the sandbox's build containers could not reach apt/npm; the file was only
  reviewed statically, including the `docker-cli` package name on the `python:3.12-slim-trixie` base);
* running as a non-root user (the sandbox runs everything as root, so the `-u uid:gid` mapping is untested for uid ≠ 0);
* the docker-socket / same-path workspace mount when DeployBoard itself runs in a container;
* private registries, image pulls of large images, resource limits (`--cpus/--memory`) actually being enforced.

Also not covered: the React UI has no automated component/E2E tests in the repository (only the one-off browser run above).

## Known limitations

* No pipeline editor in the UI (pipelines are edited through the API); no environment editing UI beyond adding one.
* No project sharing/roles beyond owner + admin; no login rate limiting; no password change / token management UI.
* No blue/green or canary deploys, no automatic rollback, no image pruning, no deployment cancellation.
* One deployment per environment at a time is enforced by an atomic database update; on PostgreSQL under heavy
  concurrency that is best-effort rather than serialised by a lock.
* Schema is created with `create_all`; there is no migration tool yet, so schema changes need a fresh database.
* The local runner executes repository code on the worker host — use `DB_RUNNER=docker` for untrusted repositories.
* Redis jobs popped by a worker that dies *before* claiming them are only re-queued at the next startup.
* Log lines are stored one row per line; very chatty builds make large tables.
