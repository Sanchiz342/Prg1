import type { DeploymentStatus } from "../api/types";

export const isFinished = (s: DeploymentStatus): boolean => s === "SUCCESS" || s === "FAILED";

export function formatDuration(start: string | null, end: string | null, now: number = Date.now()): string {
  if (!start) return "–";
  const ms = (end ? Date.parse(end) : now) - Date.parse(start);
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function timeAgo(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "–";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export const shortSha = (sha: string): string => (sha ? sha.slice(0, 7) : "–");
