// Minimal Prometheus text-format metrics (no client library needed for a handful of counters).
export const metrics = {
  connections: 0,
  connectionsTotal: 0,
  rejectedConnections: 0,
  rateLimited: 0,
  rejectedUpdates: 0,
  persistedUpdates: 0,
  persistedBytes: 0,
  compactions: 0,
  messages: { sync: 0, awareness: 0 },
};

export function renderMetrics(extra: Record<string, number>): string {
  const lines: string[] = [];
  const gauge = (name: string, help: string, value: number, type = 'gauge') => {
    lines.push(`# HELP spilno_${name} ${help}`, `# TYPE spilno_${name} ${type}`, `spilno_${name} ${value}`);
  };
  gauge('connections', 'Open WebSocket connections.', metrics.connections);
  gauge('connections_total', 'WebSocket connections accepted.', metrics.connectionsTotal, 'counter');
  gauge('rejected_connections_total', 'Upgrades refused (bad board id, room full, origin).', metrics.rejectedConnections, 'counter');
  gauge('rate_limited_total', 'Connections closed for exceeding the message rate.', metrics.rateLimited, 'counter');
  gauge('rejected_updates_total', 'Edits refused because the board exceeded its size limit.', metrics.rejectedUpdates, 'counter');
  gauge('persisted_updates_total', 'Yjs updates appended to the log.', metrics.persistedUpdates, 'counter');
  gauge('persisted_bytes_total', 'Bytes of Yjs updates appended to the log.', metrics.persistedBytes, 'counter');
  gauge('compactions_total', 'Update-log compactions.', metrics.compactions, 'counter');
  lines.push('# HELP spilno_messages_total Messages received by type.', '# TYPE spilno_messages_total counter');
  for (const [type, n] of Object.entries(metrics.messages)) lines.push(`spilno_messages_total{type="${type}"} ${n}`);
  for (const [name, value] of Object.entries(extra)) gauge(name, name.replace(/_/g, ' ') + '.', value);
  return lines.join('\n') + '\n';
}
