import type { LogLine } from "../api/types";

/** Appends `incoming` lines to `current`, dropping any id already present (the WebSocket replays stored
 *  history on every (re)connect) and keeping the result ordered by id. */
export function mergeLogs(current: LogLine[], incoming: LogLine[]): LogLine[] {
  if (incoming.length === 0) return current;
  const seen = new Set(current.map((l) => l.id));
  const fresh = incoming.filter((l) => !seen.has(l.id));
  if (fresh.length === 0) return current;
  const merged = [...current, ...fresh];
  merged.sort((a, b) => a.id - b.id);
  return merged;
}
