import { Analysis } from "../api";
import { pct, severityClass } from "../util";

export default function AnalysisCard({ a, compact = false }: { a: Analysis; compact?: boolean }) {
  const top = a.possible_causes[0];
  return (
    <div className="analysis">
      <h3>AI Analysis <span className={`pill ${severityClass(a.severity)}`}>{a.severity}</span></h3>
      <p>{a.summary}</p>
      {top && <p><strong>Most likely:</strong> {top.cause} <span className="muted">· confidence {pct(top.confidence)}</span></p>}
      {!compact && (
        <>
          <ul className="causes">
            {a.possible_causes.map((c) => (
              <li key={c.cause}>
                <span>{c.cause}</span>
                <div className="bar"><div className="fill" style={{ width: pct(c.confidence) }} /></div>
                <span className="muted">{pct(c.confidence)}</span>
              </li>
            ))}
          </ul>
          {a.recommended_checks.length > 0 && (
            <>
              <h4>Recommended checks</h4>
              <ul>{a.recommended_checks.map((c) => <li key={c}>{c}</li>)}</ul>
            </>
          )}
          <p className="muted small">
            {a.disclaimer} · mode <b>{a.meta.mode}</b> · provider <b>{a.meta.provider}</b> · {a.meta.latency_ms} ms ·{" "}
            {a.meta.redactions} redactions{a.meta.sent_externally ? " · sent externally" : " · stayed local"}
            {a.meta.fallback_reason ? ` · fallback: ${a.meta.fallback_reason}` : ""}
          </p>
        </>
      )}
    </div>
  );
}
