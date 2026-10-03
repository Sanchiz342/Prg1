# Architecture

```
 Applications ──► POST /ingest/{metrics,logs,deployments}  (X-API-Key)
                          │
                    FastAPI backend
   ┌──────────────┬───────┴────────┬───────────────┐
   Storage     Processing      Incident engine    WebSocket hub
 (Postgres)   (aggregation,    (rules + anomaly,   (live events)
              rules, ML)        timeline, summary)
                                    │
                             AI Orchestrator
                 ┌──────────────┬───┴───────────┐
              Local ML      Local LLM        Cloud LLM
          (IsolationForest, (Ollama /        (OpenAI /
           log clustering,   mock heuristic)  Anthropic)
           embeddings,
           forecast)
                                    │
                        React dashboard (REST + WS)
```

## Detection pipeline (every `DETECTION_INTERVAL_S`)
1. **Rules** – windowed means (default 5 min) checked against warning/critical thresholds
   (error rate, p95 latency, CPU, DB connections, disk).
2. **Anomaly pass** – Isolation Forest is fit on the last 3 h of a metric and scores the latest points. An anomaly
   needs both a high forest score (>=0.6) **and** a robust z-score >=4, so flat-but-healthy series don't alert and
   spikes that stay under static thresholds are still caught.
3. **Incident lifecycle** – open (deduplicated per service+metric) -> peak/severity updates -> auto-resolve with
   hysteresis (below 70 % of the warning threshold) or manual resolve.
4. **Timeline** – deployments in the previous 45 min + the first moment each related metric left its baseline
   (median of 60->10 min before the incident) + detection + recovery.
5. **Context retrieval** – metric baseline/current/peak, <=10 log clusters, <=20 representative error/warn logs,
   deployments in the previous hour. The LLM never sees the whole database.
6. **AI analysis** – structured `AnalysisResult` (summary, severity, confidence, ranked causes, related services,
   recommended checks), validated with Pydantic. Causes are always presented as hypotheses.
7. **Resolution summary** – duration, impact, likely cause, related deployment, timeline.
8. **Similar incidents** – local hashing embeddings of an incident signature + cosine similarity.

## AI modes (`AI_MODE`)
| Mode | Flow | Leaves the server? |
|------|------|--------------------|
| `local`  | context -> `AI_LOCAL_PROVIDER` (mock heuristic or Ollama) | never |
| `cloud`  | context -> `AI_PROVIDER` as-is | yes, unredacted |
| `hybrid` (default) | context -> local redaction (emails, IPs, tokens, JWTs, passwords, cards, DB URLs) -> `AI_PROVIDER` | yes, redacted |

Providers implement `AIProvider` (`analyze`, `ask`). Any provider failure (timeout, bad JSON, schema violation,
missing key) falls back to the local heuristic and the reason is recorded in `analysis.meta.fallback_reason`.
Redaction is skipped when the provider is itself local (e.g. Ollama).

Every analysis records mode, provider, latency, redaction count and whether data was sent externally, so the
privacy/cost/latency trade-offs are visible in the UI.

## Task -> engine
| Task | Engine |
|------|--------|
| CPU/RAM/traffic/latency anomaly | Local ML (Isolation Forest) |
| Error-rate anomaly | Rules + local ML |
| Log clustering | Local (template normalisation + categories) |
| Similar incidents | Local embeddings (swap for a transformer + pgvector) |
| Forecasting / time-to-threshold | Local linear trend |
| Root-cause analysis, summaries, recommendations, NL questions | LLM provider (or local heuristic) |

## Security
JWT auth with `admin` / `viewer` roles (viewers are read-only), separate API key for ingest, input validation via
Pydantic, WebSocket requires a token, secrets only via env. **Change every default in `.env.example` before exposing
the service.**

## Known limitations / next steps
- One root cause often opens several incidents (error rate, latency, DB connections); grouping them is future work.
- WebSocket hub is in-process (single backend replica). Use Redis pub/sub to scale out; Redis is not used yet.
- Metrics live in plain tables; for high volume use TimescaleDB / retention jobs.
- Users are env-configured; swap for a user table + hashed passwords for multi-tenant use.
- Hashing embeddings are a lightweight stand-in for semantic embeddings (no pgvector yet).
- Real OpenAI / Anthropic / Ollama calls are implemented but were not exercised against live services here
  (only the mock provider and the fallback path are covered by tests).
