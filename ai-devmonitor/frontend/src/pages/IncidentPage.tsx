import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Incident, Point, Session, request } from "../api";
import AnalysisCard from "../components/AnalysisCard";
import AskBox from "../components/AskBox";
import { useApi } from "../hooks";
import { fmtTime, severityClass } from "../util";

interface Props { session: Session; tick: number; onUnauthorized: () => void }
interface LogView {
  clusters: { template: string; category: string; level: string; count: number; first_seen: string; last_seen: string; services: string[] }[];
  logs: { ts: string; service: string; level: string; message: string }[];
}
interface Similar { id: number; title: string; score: number; likely_cause: string | null }

export default function IncidentPage({ session, tick, onUnauthorized }: Props) {
  const { id } = useParams();
  const base = `/incidents/${id}`;
  const [inc, err, reload] = useApi<Incident>(base, session, onUnauthorized, tick);
  const [metrics] = useApi<Record<string, Point[]>>(`${base}/metrics`, session, onUnauthorized, tick);
  const [logs] = useApi<LogView>(`${base}/logs`, session, onUnauthorized, tick);
  const [similar] = useApi<Similar[]>(`${base}/similar`, session, onUnauthorized, tick);
  const isAdmin = session.role === "admin";

  const act = (path: string) => request(`${base}${path}`, session, { method: "POST" }).then(reload).catch(() => undefined);

  if (err) return <p className="error">{err}</p>;
  if (!inc) return <p className="muted">Loading…</p>;
  const charts = Object.entries(metrics ?? {}).filter(([k]) => k.startsWith(`${inc.service}/`));

  return (
    <div className="grid">
      <section className={`card hero ${severityClass(inc.severity)}`}>
        <h2>INCIDENT #{inc.id} <span className={`pill ${severityClass(inc.severity)}`}>{inc.severity}</span> <span className="pill">{inc.status}</span></h2>
        <p>{inc.title} · {inc.trigger_metric} {inc.baseline_value.toFixed(1)} → {inc.peak_value.toFixed(1)}</p>
        <p className="muted">Started {fmtTime(inc.started_at)} · affected: {inc.affected_services.join(", ")} · detected by {inc.source}</p>
        {isAdmin && inc.status !== "resolved" && (
          <div className="row">
            <button onClick={() => act("/analyze")}>Re-run AI analysis</button>
            <button onClick={() => act("/status?status=identified")} className="ghost">Mark identified</button>
            <button onClick={() => act("/resolve")} className="ghost">Resolve</button>
          </div>
        )}
      </section>

      <section className="card">{inc.analysis ? <AnalysisCard a={inc.analysis} /> : <p className="muted">Analysis pending…</p>}</section>

      <section className="card">
        <h3>Timeline</h3>
        <ol className="timeline">
          {(inc.timeline ?? []).map((e, i) => (
            <li key={i} className={`k-${e.kind}`}><time>{fmtTime(e.ts)}</time> {e.description}</li>
          ))}
        </ol>
      </section>

      {inc.summary && (
        <section className="card">
          <h3>Incident summary</h3>
          <p>Duration <b>{inc.summary.duration_minutes} min</b> · {inc.summary.impact}</p>
          <p>Likely cause: <b>{inc.summary.likely_cause ?? "unknown"}</b></p>
          {inc.summary.related_deployment && <p>Related: {inc.summary.related_deployment}</p>}
          <p className="muted small">{inc.summary.note}</p>
        </section>
      )}

      <section className="card wide">
        <h3>Related metrics</h3>
        <div className="charts">
          {charts.map(([key, pts]) => (
            <div key={key} className="chart">
              <h4>{key.split("/")[1]}</h4>
              <ResponsiveContainer width="100%" height={140}>
                <LineChart data={pts.map((p) => ({ t: fmtTime(p.ts), v: Math.round(p.value * 100) / 100 }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a3342" />
                  <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={30} />
                  <YAxis tick={{ fontSize: 10 }} width={40} />
                  <Tooltip contentStyle={{ background: "#151b26", border: "1px solid #2a3342" }} />
                  <Line type="monotone" dataKey="v" stroke="#4da3ff" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ))}
        </div>
      </section>

      <section className="card wide">
        <h3>Related logs (clustered)</h3>
        <table>
          <thead><tr><th>Level</th><th>Category</th><th>Pattern</th><th>Count</th><th>Seen</th></tr></thead>
          <tbody>
            {(logs?.clusters ?? []).filter((c) => c.level !== "INFO").map((c) => (
              <tr key={c.level + c.template}>
                <td>{c.level}</td><td>{c.category}</td><td><code>{c.template}</code></td><td>{c.count}</td>
                <td>{fmtTime(c.first_seen)}–{fmtTime(c.last_seen)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {(similar ?? []).length > 0 && (
        <section className="card">
          <h3>Similar past incidents</h3>
          <ul>{similar!.map((s) => <li key={s.id}><Link to={`/incidents/${s.id}`}>#{s.id} {s.title}</Link> <span className="muted">· {Math.round(s.score * 100)}% match{s.likely_cause ? ` · ${s.likely_cause}` : ""}</span></li>)}</ul>
        </section>
      )}

      <AskBox session={session} incidentId={inc.id} />
    </div>
  );
}
