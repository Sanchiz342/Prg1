import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Card, Empty, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { shortSha, timeAgo } from "../lib/format";

export function Dashboard() {
  const projects = useAsync(() => api.projects(), [], 5000);
  if (projects.loading && !projects.data) return <Spinner />;
  if (projects.error && !projects.data) return <ErrorBox message={projects.error} onRetry={projects.reload} />;
  const list = projects.data ?? [];
  return (
    <>
      <div className="row between">
        <h1>Dashboard</h1>
        <Link className="button" to="/projects?new=1">
          + New project
        </Link>
      </div>
      {projects.error && <ErrorBox message={projects.error} onRetry={projects.reload} />}
      {list.length === 0 && <Empty>No projects yet. Create one to start deploying.</Empty>}
      <div className="grid">
        {list.map((p) => (
          <Card key={p.id}>
            <h3>
              <Link to={`/projects/${p.id}`}>{p.name}</Link>
            </h3>
            <p className="muted">{p.repo_url}</p>
            <ul className="envlist">
              {p.environments.map((e) => (
                <li key={e.name}>
                  <Link to={e.last_deployment ? `/deployments/${e.last_deployment.id}` : `/projects/${p.id}?env=${e.name}`}>
                    <strong>{e.name}</strong>
                  </Link>
                  <StatusBadge status={e.last_deployment?.status} />
                  <span className="muted">
                    {e.last_deployment ? `#${e.last_deployment.id} · ${shortSha(e.last_deployment.commit)} · ${timeAgo(e.last_deployment.created_at)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </>
  );
}
