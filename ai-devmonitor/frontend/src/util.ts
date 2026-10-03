export const fmtTime = (iso: string) => new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleTimeString([], { hour12: false });

export const pct = (x: number) => `${Math.round(x * 100)}%`;

export const metricUnit = (name: string) =>
  ({ cpu: "%", ram: "%", disk: "%", error_rate: "%", db_connections: "%", latency_p95: " ms", requests: " req" } as Record<string, string>)[name] ?? "";

export const metricMax = (name: string, value: number) =>
  ["cpu", "ram", "disk", "error_rate", "db_connections"].includes(name) ? 100 : Math.max(value * 1.5, 1);

export function severityClass(sev: string): string {
  return sev === "critical" ? "sev-critical" : sev === "warning" ? "sev-warning" : "sev-info";
}
