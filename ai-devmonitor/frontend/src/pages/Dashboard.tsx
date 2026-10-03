import { Link } from "react-router-dom";
import { Incident, ServiceStatus, Session } from "../api";
import AnalysisCard from "../components/AnalysisCard";
import AskBox from "../components/AskBox";
import Bar from "../components/Bar";
import { useApi } from "../hooks";
import { fmtTime, severityClass } from "../util";

interface Props { session: Session; tick: number; onUnauthorized: () => void }
const DOT = { green: "🟢", yellow: "🟡", red: "🔴", unknown: "⚪" } as const;

export default function Dashboard({ session, tick, onUnauthorized }: Props) {
  const [services, svcErr] = useApi<ServiceStatus[]>("/services", session, onUnauthorized, tick);
  const [incidents, incErr] = useApi<Incident[]>("/incidents", session, onUnauthorized, tick);
  const active = (incidents ?? []).filter((i) => i.status !== "resolved");
  const hero = active.find((i) => i.severity === "critical") ?? active[0];
  const past = (incidents ?? []).filter((i) => i.status === "resolved");

  return (
    <div className="grid">
      {(svcErr || incErr) && <p className="error">{svcErr ?? incErr}</p>}

      {hero ? (
        <section className={`card hero ${severityClass(hero.severity)}`}>
          <h2>🔴 Active incident #{hero.id} — {hero.title}</h2>
          <p className="muted">
            {hero.trigger_metric}: {hero.baseline_value.toFixed(1)} → {hero.peak_value.toFixed(1)} · started {fmtTime(hero.started_at)} · {hero.status}
          </p>
          {hero.analysis ? <AnalysisCard a={hero.analysis} compact /> : <p className="muted">Analysis pending…</p>}
          <Link to={`/incidents/${hero.id}`}>Open incident →</Link>
        </section>
      ) : (
        <section className="card hero ok"><h2>✅ No active incidents</h2></section>
      )}

      <section className="card">
        <h3>Services</h3>
        {(services ?? []).length === 0 && <p className="muted">No data yet. Start the demo agent or POST to /ingest.</p>}
        {(services ?? []).map((s) => (
          <details key={s.name} className="service" open={s.status !== "green"}>
            <summary>
              {DOT[s.status]} <b>{s.name}</b> <span className="muted">{s.kind}</span>
              {s.open_incident_id && <Link to={`/incidents/${s.open_incident_id}`} className="pill sev-critical">incident #{s.open_incident_id}</Link>}
            </summary>
            {Object.entries(s.metrics).map(([n, v]) => <Bar key={n} name={n} value={v} />)}
          </details>
        ))}
      </section>

      <section className="card">
        <h3>Incidents</h3>
        <table>
          <thead><tr><th>#</th><th>Title</th><th>Severity</th><th>Status</th><th>Started</th></tr></thead>
          <tbody>
            {[...active, ...past].map((i) => (
              <tr key={i.id}>
                <td>{i.id}</td>
                <td><Link to={`/incidents/${i.id}`}>{i.title}</Link></td>
                <td><span className={`pill ${severityClass(i.severity)}`}>{i.severity}</span></td>
                <td>{i.status}</td>
                <td>{fmtTime(i.started_at)}</td>
              </tr>
            ))}
            {(incidents ?? []).length === 0 && <tr><td colSpan={5} className="muted">Nothing yet</td></tr>}
          </tbody>
        </table>
      </section>

      <AskBox session={session} />
    </div>
  );
}
