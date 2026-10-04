import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { EnvironmentName } from "../api/types";
import { Card, Empty, ErrorBox, Spinner } from "../components/ui";
import { useAsync } from "../hooks/useAsync";

export const ENV_NAMES: EnvironmentName[] = ["development", "staging", "production"];
const DEFAULT_BRANCH: Record<EnvironmentName, string> = { development: "dev", staging: "develop", production: "main" };

function NewProjectForm({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [envs, setEnvs] = useState<Record<EnvironmentName, { on: boolean; branch: string }>>({
    development: { on: false, branch: DEFAULT_BRANCH.development },
    staging: { on: false, branch: DEFAULT_BRANCH.staging },
    production: { on: true, branch: DEFAULT_BRANCH.production },
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const chosen = ENV_NAMES.filter((n) => envs[n].on).map((n) => ({ name: n, branch: envs[n].branch.trim() || "main" }));
    if (chosen.length === 0) return setError("Select at least one environment");
    setBusy(true);
    setError(null);
    try {
      const p = await api.createProject({ name: name.trim(), repo_url: repoUrl.trim(), environments: chosen });
      navigate(`/projects/${p.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the project");
      setBusy(false);
    }
  };

  return (
    <Card>
      <h3>New project</h3>
      <form onSubmit={(e) => void submit(e)}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Repository (URL or local path)
          <input value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} required placeholder="https://github.com/user/app" />
        </label>
        <fieldset>
          <legend>Environments (each deploys its own branch)</legend>
          {ENV_NAMES.map((n) => (
            <div className="row" key={n}>
              <label className="inline">
                <input type="checkbox" checked={envs[n].on} onChange={(e) => setEnvs({ ...envs, [n]: { ...envs[n], on: e.target.checked } })} /> {n}
              </label>
              <input
                aria-label={`${n} branch`}
                value={envs[n].branch}
                disabled={!envs[n].on}
                onChange={(e) => setEnvs({ ...envs, [n]: { ...envs[n], branch: e.target.value } })}
              />
            </div>
          ))}
        </fieldset>
        {error && <ErrorBox message={error} />}
        <div className="row">
          <button disabled={busy}>{busy ? "Creating…" : "Create project"}</button>
          <button type="button" className="secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </Card>
  );
}

export function Projects() {
  const projects = useAsync(() => api.projects(), []);
  const [params, setParams] = useSearchParams();
  const creating = params.has("new");
  return (
    <>
      <div className="row between">
        <h1>Projects</h1>
        {!creating && (
          <button onClick={() => setParams({ new: "1" })}>+ New project</button>
        )}
      </div>
      {creating && <NewProjectForm onCancel={() => setParams({})} />}
      {projects.loading && !projects.data && <Spinner />}
      {projects.error && <ErrorBox message={projects.error} onRetry={projects.reload} />}
      {projects.data?.length === 0 && !creating && <Empty>No projects yet.</Empty>}
      <ul className="plain">
        {projects.data?.map((p) => (
          <li key={p.id}>
            <Card>
              <Link to={`/projects/${p.id}`}>
                <strong>{p.name}</strong>
              </Link>{" "}
              <span className="muted">
                {p.repo_url} · {p.environments.map((e) => e.name).join(", ")}
              </span>
            </Card>
          </li>
        ))}
      </ul>
    </>
  );
}
