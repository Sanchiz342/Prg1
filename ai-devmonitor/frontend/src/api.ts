export const API = import.meta.env.VITE_API_URL ?? "/api";

export type Role = "admin" | "viewer";
export interface Session { token: string; role: Role }

export interface Cause { cause: string; confidence: number }
export interface Analysis {
  summary: string;
  severity: string;
  confidence: number;
  possible_causes: Cause[];
  related_services: string[];
  recommended_checks: string[];
  disclaimer: string;
  meta: { mode: string; provider: string; requested_provider: string; latency_ms: number; redactions: number; sent_externally: boolean; fallback_reason: string | null };
}
export interface Incident {
  id: number; title: string; severity: "warning" | "critical"; status: string; service: string;
  trigger_metric: string; baseline_value: number; peak_value: number; source: string;
  started_at: string; resolved_at: string | null; affected_services: string[];
  analysis: Analysis | null;
  summary?: Summary | null;
  timeline?: { ts: string; kind: string; description: string }[];
}
export interface Summary {
  duration_minutes: number; impact: string; likely_cause: string | null; related_deployment: string | null; note: string;
}
export interface ServiceStatus {
  name: string; kind: string; status: "green" | "yellow" | "red" | "unknown";
  metrics: Record<string, number>; open_incident_id: number | null;
}
export type Point = { ts: string; value: number };

const KEY = "devmonitor.session";
export const loadSession = (): Session | null => {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "null"); } catch { return null; }
};
export const saveSession = (s: Session | null) => {
  try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY); } catch { /* storage unavailable */ }
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function request<T>(path: string, session: Session | null, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (session) headers.Authorization = `Bearer ${session.token}`;
  const res = await fetch(`${API}${path}`, { ...init, headers: { ...headers, ...(init.headers as object) } });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-json body */ }
    throw new ApiError(res.status, String(detail));
  }
  return res.json();
}

export async function login(username: string, password: string): Promise<Session> {
  const r = await request<{ access_token: string; role: Role }>("/auth/login", null, {
    method: "POST", body: JSON.stringify({ username, password }),
  });
  return { token: r.access_token, role: r.role };
}

export function wsUrl(token: string): string {
  const base = API.startsWith("http") ? API.replace(/^http/, "ws") : `${location.origin.replace(/^http/, "ws")}${API}`;
  return `${base}/ws?token=${encodeURIComponent(token)}`;
}
