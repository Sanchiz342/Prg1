import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Environment, EnvironmentName, Project } from "../api/types";
import { Card, Empty, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatDuration, shortSha, timeAgo } from "../lib/format";
import { ENV_NAMES } from "./Projects";

function Variables({ project, env }: { project: Project; env: EnvironmentName }) {
  const vars = useAsync(() => api.variables(project.id, env), [project.id, env]);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [secret, setSecret] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.putVariable(project.id, env, { key: key.trim(), value, is_secret: secret });
      setKey("");
      setValue("");
      vars.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the variable");
    }
  };
  const remove = async (k: string) => {
    try {
      await api.deleteVariable(project.id, env, k);
      vars.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the variable");
    }
  };

  return (
    <Card>
      <h3>Variables &amp; secrets · {env}</h3>
      <p className="muted">Scoped to this environment only. Secret values are write-only: they are never shown again.</p>
      {vars.loading && !vars.data && <Spinner />}
      {vars.error && <ErrorBox message={vars.error} onRetry={vars.reload} />}
      {vars.data?.length === 0 && <Empty>No variables.</Empty>}
      <table className="table">
        <tbody>
          {vars.data?.map((v) => (
            <tr key={v.key}>
              <td>
                <code>{v.key}</code>
              </td>
              <td>{v.is_secret ? <span className="muted">{v.value} (secret)</span> : <code>{v.value}</code>}</td>
              <td>
                <button className="link" onClick={() => void remove(v.key)}>
                  delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="row" onSubmit={(e) => void add(e)}>
        <input placeholder="NAME" aria-label="variable name" value={key} onChange={(e) => setKey(e.target.value)} required />
        <input placeholder="value" aria-label="variable value" value={value} onChange={(e) => setValue(e.target.value)} required />
        <label className="inline">
          <input type="checkbox" checked={secret} onChange={(e) => setSecret(e.target.checked)} /> secret
        </label>
        <button>Save</button>
      </form>
      {error && <ErrorBox message={error} />}
    </Card>
  );
}

function AddEnvironment({ project, onAdded }: { project: Project; onAdded: (name: EnvironmentName) => void }) {
  const missing = ENV_NAMES.filter((n) => !project.environments.some((e) => e.name === n));
  const [name, setName] = useState<EnvironmentName | "">("");
  const [branch, setBranch] = useState("main");
  const [error, setError] = useState<string | null>(null);
  if (missing.length === 0) return null;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const chosen = name || missing[0];
    try {
      await api.addEnvironment(project.id, { name: chosen, branch: branch.trim() || "main" });
      onAdded(chosen);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the environment");
    }
  };
  return (
    <form className="row" onSubmit={(e) => void submit(e)}>
      <select aria-label="new environment" value={name || missing[0]} onChange={(e) => setName(e.target.value as EnvironmentName)}>
        {missing.map((n) => (
          <option key={n}>{n}</option>
        ))}
      </select>
      <input aria-label="branch" value={branch} onChange={(e) => setBranch(e.target.value)} />
      <button className="secondary">+ Add environment</button>
      {error && <ErrorBox message={error} />}
    </form>
  );
}

function EnvironmentPanel({ project, env, onDeployed }: { project: Project; env: Environment; onDeployed: (id: number) => void }) {
  const history = useAsync(() => api.deployments(project.id, env.name), [project.id, env.name], 4000);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const deploy = async () => {
    setBusy(true);
    setError(null);
    try {
      onDeployed((await api.deploy(project.id, env.name)).id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the deployment");
      setBusy(false);
    }
  };

  return (
    <>
      <Card>
        <div className="row between">
          <dl className="facts">
            <dt>Branch</dt>
            <dd>
              <code>{env.branch}</code> {env.auto_deploy ? <span className="muted">(auto-deploy on push)</span> : <span className="muted">(manual only)</span>}
            </dd>
            <dt>Health URL</dt>
            <dd>{env.health_url ? <code>{env.health_url}</code> : "–"}</dd>
            <dt>Port</dt>
            <dd>{env.port || "–"}</dd>
          </dl>
          <button disabled={busy} onClick={() => void deploy()}>
            {busy ? "Queuing…" : `Deploy to ${env.name}`}
          </button>
        </div>
        {error && <ErrorBox message={error} />}
      </Card>
      <h2>Deployment history</h2>
      {history.loading && !history.data && <Spinner />}
      {history.error && <ErrorBox message={history.error} onRetry={history.reload} />}
      {history.data?.length === 0 && <Empty>No deployments in {env.name} yet.</Empty>}
      {history.data && history.data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Status</th>
              <th>Commit</th>
              <th>Trigger</th>
              <th>Duration</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {history.data.map((d) => (
              <tr key={d.id}>
                <td>
                  <Link to={`/deployments/${d.id}`}>#{d.id}</Link>
                </td>
                <td>
                  <StatusBadge status={d.status} />
                </td>
                <td>
                  <code>{shortSha(d.commit)}</code> <span className="muted">{d.author}</span>
                </td>
                <td>{d.trigger}</td>
                <td>{formatDuration(d.started_at, d.finished_at)}</td>
                <td className="muted">{timeAgo(d.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Variables project={project} env={env.name} />
    </>
  );
}

export function ProjectDetails() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const project = useAsync(() => api.project(id), [id]);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const p = project.data;

  if (project.loading && !p) return <Spinner />;
  if (!p) return <ErrorBox message={project.error ?? "Project not found"} onRetry={project.reload} />;

  const selected = p.environments.find((e) => e.name === params.get("env")) ?? p.environments[0];
  const remove = async () => {
    if (!window.confirm(`Delete project "${p.name}" and its whole deployment history?`)) return;
    try {
      await api.deleteProject(p.id);
      navigate("/projects");
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Could not delete the project");
    }
  };

  return (
    <>
      <p>
        <Link to="/projects">← Projects</Link>
      </p>
      <div className="row between">
        <h1>{p.name}</h1>
        <button className="secondary danger" onClick={() => void remove()}>
          Delete project
        </button>
      </div>
      {deleteError && <ErrorBox message={deleteError} />}
      <p className="muted">{p.repo_url}</p>
      <div className="tabs" role="tablist">
        {p.environments.map((e) => (
          <button
            key={e.name}
            role="tab"
            aria-selected={e.name === selected?.name}
            className={e.name === selected?.name ? "tab active" : "tab"}
            onClick={() => setParams({ env: e.name })}
          >
            {e.name} <StatusBadge status={e.last_deployment?.status} />
          </button>
        ))}
      </div>
      <AddEnvironment project={p} onAdded={(name) => (project.reload(), setParams({ env: name }))} />
      {selected ? (
        <EnvironmentPanel project={p} env={selected} onDeployed={(did) => navigate(`/deployments/${did}`)} />
      ) : (
        <Empty>This project has no environments.</Empty>
      )}
      <details className="webhook">
        <summary>GitHub webhook</summary>
        <p className="muted">
          Add a <code>push</code> webhook (content type JSON) pointing at <code>{window.location.origin}/api/webhooks/github</code> with this secret:
        </p>
        <code>{p.webhook_secret}</code>
      </details>
    </>
  );
}
