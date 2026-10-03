# 🧠 AI DevMonitor

Monitoring that explains itself. It collects metrics, logs and deployments, detects incidents (static rules +
Isolation-Forest anomaly detection), and builds a **structured, hypothesis-style root-cause analysis** from only the
relevant context, using local ML, a local LLM, a cloud LLM, or a privacy-preserving hybrid.

> Instead of "API has 18 % errors" you get: *error rate rose ~20x after deployment v1.8.2; the dominant log pattern is
> `DatabaseConnectionError`; DB connections are at 96 % -> likely connection-pool exhaustion (confidence 80 %).*

Self-contained mini-project: everything lives in this folder. Details: [docs/architecture.md](docs/architecture.md).

## Features
- Ingest API for metrics / logs / deployments (API-key protected)
- Rule alerts + Isolation Forest anomaly detection + linear forecast
- Log clustering and categorisation, similar-incident search
- Incident lifecycle, timeline, auto-generated resolution summary
- AI Orchestrator: `local` / `cloud` / `hybrid` modes, provider interface (mock, Ollama, OpenAI, Anthropic), redaction, fallback
- React + TypeScript dashboard with live WebSocket updates, per-incident page, "ask the monitor" box
- JWT auth with admin/viewer roles; Docker Compose; CI workflow; tests

## Quick start (no Docker)
```bash
# backend (SQLite by default)
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload           # http://localhost:8000/docs

# demo data: 90 min of history ending in a "deployment v1.8.2 -> DB pool exhaustion" incident
python ../agent-demo/agent.py --scenario backfill

# frontend
cd ../frontend && npm install && npm run dev   # http://localhost:5173  (login: admin / admin)
```
The background detector runs every 15 s; an incident appears on the dashboard automatically.

## Docker Compose (Postgres + backend + nginx frontend)
```bash
docker compose up --build                   # UI on http://localhost:8080
docker compose --profile demo up demo-agent # feed demo telemetry
```

## Sending your own telemetry
```bash
curl -X POST localhost:8000/ingest/metrics -H 'X-API-Key: dev-ingest-key' -H 'Content-Type: application/json' \
  -d '{"metrics":[{"service":"user-api","name":"error_rate","value":1.2}]}'
```
Recognised metric names: `cpu`, `ram`, `disk`, `error_rate` (%), `latency_p95` (ms), `requests`, `db_connections` (%).
Log levels: `DEBUG|INFO|WARN|ERROR|CRITICAL`. Deployments: `POST /ingest/deployments {service, version, notes}`.
Other projects in this repo can be monitored by pointing their telemetry at `/ingest`.

## AI configuration
See `.env.example`. Default is `AI_MODE=hybrid` with the offline `mock` provider, so everything works without keys.
```
AI_MODE=hybrid AI_PROVIDER=openai OPENAI_API_KEY=...        # redacted context -> OpenAI
AI_MODE=hybrid AI_PROVIDER=anthropic ANTHROPIC_API_KEY=...
AI_MODE=local  AI_LOCAL_PROVIDER=ollama OLLAMA_MODEL=llama3.1  # 100 % on-prem
```
Admins can re-run an analysis on any incident; the card shows mode, provider, latency, redactions and fallbacks.

## Tests
```bash
cd backend && pytest -q && ruff check app tests     # 19 tests: ingest, auth/RBAC, ML, redaction, orchestrator, e2e incident flow, WebSocket
cd frontend && npm test && npm run build
```

## Layout
```
backend/     FastAPI app (api, processing, ml, ai, incidents) + tests
frontend/    React + TS + Vite dashboard
agent-demo/  stdlib telemetry generator for demos
docs/        architecture & limitations
```

## Security note
Default credentials (`admin/admin`, `viewer/viewer`, `dev-ingest-key`, JWT secret) are for local development only.
