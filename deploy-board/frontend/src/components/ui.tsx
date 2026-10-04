import type { ReactNode } from "react";
import type { DeploymentStatus, StageStatus } from "../api/types";

const ICON: Record<string, string> = { SUCCESS: "✓", FAILED: "✕", RUNNING: "●", QUEUED: "○", PENDING: "○", SKIPPED: "–" };

export function StatusBadge({ status }: { status: DeploymentStatus | StageStatus | null | undefined }) {
  if (!status) return <span className="badge muted">no deployments</span>;
  return (
    <span className={`badge ${status.toLowerCase()}`}>
      {ICON[status]} {status.toLowerCase()}
    </span>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted" role="status">
      {label}
    </p>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error" role="alert">
      {message}
      {onRetry && (
        <button className="link" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted empty">{children}</p>;
}
