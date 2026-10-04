import type { Stage } from "../api/types";
import { formatDuration } from "../lib/format";
import { StatusBadge } from "./ui";

export function StageList({ stages }: { stages: Stage[] }) {
  if (stages.length === 0) return <p className="muted">Waiting for a worker to pick this deployment up…</p>;
  return (
    <ol className="stages">
      {stages.map((s) => (
        <li key={s.name} className={`stage ${s.status.toLowerCase()}`}>
          <StatusBadge status={s.status} />
          <strong>{s.name}</strong>
          <span className="muted">
            {s.started_at ? formatDuration(s.started_at, s.finished_at) : ""}
            {s.attempts > 1 ? ` · ${s.attempts} attempts` : ""}
            {s.status === "FAILED" && s.exit_code !== null ? ` · exit ${s.exit_code}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}
