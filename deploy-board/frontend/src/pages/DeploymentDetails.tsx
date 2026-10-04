import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { LogViewer } from "../components/LogViewer";
import { StageList } from "../components/StageList";
import { Card, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { useDeploymentStream } from "../hooks/useDeploymentStream";
import { formatDuration, isFinished, shortSha, timeAgo } from "../lib/format";

export function DeploymentDetails() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const dep = useAsync(() => api.deployment(id), [id]);
  const { lines, state, addLines } = useDeploymentStream(id, true, dep.reload);
  const [rollbackError, setRollbackError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const d = dep.data;

  // if the live stream is unavailable, fall back to the stored logs
  useEffect(() => {
    if (state === "error") api.logs(id).then(addLines).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, id]);

  if (!Number.isFinite(id)) return <ErrorBox message="Invalid deployment id" />;
  if (dep.loading && !d) return <Spinner />;
  if (!d) return <ErrorBox message={dep.error ?? "Deployment not found"} onRetry={dep.reload} />;

  const rollback = async () => {
    setBusy(true);
    setRollbackError(null);
    try {
      const next = await api.rollback(d.id);
      navigate(`/deployments/${next.id}`);
    } catch (err) {
      setRollbackError(err instanceof Error ? err.message : "Rollback failed");
      setBusy(false);
    }
  };

  const canRollback = isFinished(d.status) && d.trigger !== "rollback";
  return (
    <>
      <p>
        <Link to={`/projects/${d.project_id}?env=${d.environment}`}>← {d.environment} history</Link>
      </p>
      <div className="row between">
        <h1>
          Deployment #{d.id} <StatusBadge status={d.status} />
        </h1>
        {canRollback && (
          <button className="secondary" disabled={busy} onClick={() => void rollback()}>
            {d.status === "FAILED" ? "Roll back to last good version" : "Redeploy this version"}
          </button>
        )}
      </div>
      {rollbackError && <ErrorBox message={rollbackError} />}
      {dep.error && <ErrorBox message={dep.error} onRetry={dep.reload} />}
      <Card>
        <dl className="facts">
          <dt>Environment</dt>
          <dd>{d.environment}</dd>
          <dt>Commit</dt>
          <dd>
            <code>{shortSha(d.commit)}</code> on {d.branch || "–"} by {d.author || "–"}
          </dd>
          <dt>Trigger</dt>
          <dd>
            {d.trigger}
            {d.rollback_of ? ` (of #${d.rollback_of})` : ""}
          </dd>
          <dt>Image</dt>
          <dd>{d.image ? <code>{d.image}</code> : "–"}</dd>
          <dt>Started</dt>
          <dd>{timeAgo(d.started_at ?? d.created_at)}</dd>
          <dt>Duration</dt>
          <dd>{formatDuration(d.started_at, d.finished_at)}</dd>
          {d.attempts > 1 && (
            <>
              <dt>Attempts</dt>
              <dd>{d.attempts} (a worker was lost)</dd>
            </>
          )}
        </dl>
        {d.error && <ErrorBox message={d.error} />}
      </Card>
      <h2>Pipeline</h2>
      <Card>
        <StageList stages={d.stages} />
      </Card>
      <h2>Logs</h2>
      <LogViewer lines={lines} state={state} />
    </>
  );
}
