# 🚀 DeployBoard

A mini CI/CD platform: connect a Git repository, and every `git push` is cloned, tested, built, packaged into a Docker
image, deployed and health-checked — while you watch live logs in the dashboard. Self-contained mini-project
(nothing outside this folder is used).

```
GitHub ──webhook──▶ API ──▶ Queue ──▶ Worker pool ──▶ Pipeline engine ──▶ Runner (local | Docker container)
                     │                                      │
                     └────── WebSocket (live logs) ◀── event bus
```

## Status

| Level | Scope | State |
|---|---|---|
| 1 MVP | Projects, webhook (HMAC), deployments, pipeline engine, workers, tests | ✅ |
| 2 Docker | docker build / run, health checks, env vars, isolated runner | ✅ code written; needs a Docker daemon and is not covered by automated tests |
| 3 Production-like | Live logs (WebSocket), encrypted secrets, history, rollback | partly: **no auth yet, in-process queue (Redis later), one environment per project, PostgreSQL possible via `DB_URL` but untested** |
| 4 Advanced | runner pool, blue/green, auto-rollback, AI failure analysis, DevMonitor integration | planned |

The dashboard is a dependency-free single page (`backend/app/static/index.html`); the React + TypeScript frontend from the
plan is not built yet.

## Run

```bash
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload            # UI: http://localhost:8000   API docs: /docs
python -m pytest                          # tests use a real local git repo
```

## Pipeline

A project's pipeline is an ordered list of stages (default: checkout → install → test → build → docker → deploy →
healthcheck). Stage kinds: `checkout`, `shell` (`run`), `docker_build`, `docker_run`, `healthcheck` (`retries`, `interval`).
A failing stage stops the run and the remaining stages become `SKIPPED`.

```bash
curl -XPOST localhost:8000/api/projects -H 'content-type: application/json' -d '{
  "name":"shop-api","repo_url":"https://github.com/user/shop-api","branch":"main",
  "health_url":"http://localhost:9000/health","port":9000}'
curl -XPUT localhost:8000/api/projects/1/variables -H 'content-type: application/json' \
     -d '{"key":"DATABASE_URL","value":"postgres://…","is_secret":true}'
curl -XPOST localhost:8000/api/projects/1/deploy
```

* **Secrets** are Fernet-encrypted at rest, never returned by the API, injected only into runner env, and masked (`***`) in logs.
* **Rollback** `POST /api/deployments/{id}/rollback` redeploys the image of that deployment (if it succeeded) or of the latest
  earlier successful one, running only `deploy` + `healthcheck`.
* **GitHub webhook**: point a `push` webhook at `POST /api/webhooks/github`, content type JSON, secret = the project's
  `webhook_secret`. Signature (`X-Hub-Signature-256`) is verified; only pushes to the project's branch deploy.
* **Runners**: `DB_RUNNER=local` executes shell stages on the worker host (demo/tests). `DB_RUNNER=docker` runs each shell
  stage in a throw-away container (`RUNNER_IMAGE` variable, default `node:22`) with the workspace mounted — use this for any
  repository you don't trust.

## API

`POST/GET /api/projects` · `GET/PATCH/DELETE /api/projects/{id}` · `PUT/GET /api/projects/{id}/variables` ·
`POST /api/projects/{id}/deploy` · `GET /api/projects/{id}/deployments` · `GET /api/deployments/{id}` ·
`GET /api/deployments/{id}/logs` · `POST /api/deployments/{id}/rollback` · `POST /api/webhooks/github` ·
`WS /ws/deployments/{id}` · `GET /api/health`
