import type { Deployment, Environment, EnvironmentName, LogLine, NewProject, Project, User, Variable } from "./types";

const TOKEN_KEY = "deployboard.token";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const tokenStore = {
  get: (): string | null => {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set: (t: string | null): void => {
    try {
      if (t) localStorage.setItem(TOKEN_KEY, t);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable: the session just won't survive a reload */
    }
  },
};

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

/** Turns FastAPI's error bodies ({detail: string | [{msg}]}) into a readable message. */
export function errorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? "invalid value").join("; ");
  return fallback;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "Cannot reach the DeployBoard API");
  }
  if (res.status === 401 && token) onUnauthorized();
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new ApiError(res.status, errorMessage(data, res.statusText || `HTTP ${res.status}`));
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const enc = encodeURIComponent;
const envPath = (pid: number, env: EnvironmentName) => `/projects/${pid}/environments/${env}`;

export const api = {
  authStatus: () => request<{ registration_open: boolean }>("GET", "/auth/status"),
  login: (username: string, password: string) =>
    request<{ token: string; user: User }>("POST", "/auth/login", { username, password }),
  register: (username: string, password: string) =>
    request<{ token: string; user: User }>("POST", "/auth/register", { username, password }),
  me: () => request<User>("GET", "/auth/me"),
  logout: () => request<void>("POST", "/auth/logout"),

  projects: () => request<Project[]>("GET", "/projects"),
  project: (id: number) => request<Project>("GET", `/projects/${id}`),
  createProject: (p: NewProject) => request<Project>("POST", "/projects", p),
  deleteProject: (id: number) => request<void>("DELETE", `/projects/${id}`),

  addEnvironment: (pid: number, env: { name: EnvironmentName; branch: string; health_url?: string; port?: number }) =>
    request<Environment>("POST", `/projects/${pid}/environments`, env),
  variables: (pid: number, env: EnvironmentName) => request<Variable[]>("GET", `${envPath(pid, env)}/variables`),
  putVariable: (pid: number, env: EnvironmentName, v: { key: string; value: string; is_secret: boolean }) =>
    request<void>("PUT", `${envPath(pid, env)}/variables`, v),
  deleteVariable: (pid: number, env: EnvironmentName, key: string) =>
    request<void>("DELETE", `${envPath(pid, env)}/variables/${enc(key)}`),

  deploy: (pid: number, env: EnvironmentName) => request<Deployment>("POST", `${envPath(pid, env)}/deploy`),
  deployments: (pid: number, env?: EnvironmentName) =>
    request<Deployment[]>("GET", `/projects/${pid}/deployments${env ? `?environment=${env}` : ""}`),
  deployment: (id: number) => request<Deployment>("GET", `/deployments/${id}`),
  logs: (id: number, after = 0) => request<LogLine[]>("GET", `/deployments/${id}/logs?after=${after}`),
  rollback: (id: number) => request<Deployment>("POST", `/deployments/${id}/rollback`),
};

export function streamUrl(deploymentId: number, token: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws/deployments/${deploymentId}?token=${enc(token)}`;
}
