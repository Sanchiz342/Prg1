import type { UnreadMap } from "./types";

/** Pure helpers for the unread cache (server stays the source of truth; refetched on reconnect/focus). */
export const bumpUnread = (m: UnreadMap | undefined, channelId: string, field: "unread" | "mentions" = "unread"): UnreadMap => {
  const cur = m?.[channelId] ?? { unread: 0, mentions: 0 };
  return { ...m, [channelId]: { ...cur, [field]: cur[field] + 1 } };
};

export const clearUnread = (m: UnreadMap | undefined, channelId: string): UnreadMap => {
  if (!m || !(channelId in m)) return m ?? {};
  const { [channelId]: _gone, ...rest } = m;
  return rest;
};

export const totalUnread = (m: UnreadMap | undefined): number =>
  Object.values(m ?? {}).reduce((sum, c) => sum + c.unread, 0);

/** Badge text: counts above 99 collapse to "99+". */
export const badge = (n: number): string => (n > 99 ? "99+" : String(n));

/**
 * Single-flight request queue per key: at most one request in flight; calls made meanwhile collapse
 * into ONE trailing repeat, and only if `shouldRepeat` still holds when the first one finishes.
 *
 * Why not a debounce: a delayed "mark read" request is executed by the server against "everything that
 * exists now", so a timer that outlives the user's visit would mark messages they never saw as read.
 * With this queue a read is sent immediately, and the worst case is a badge that lingers (safe direction).
 */
export function singleFlight(send: (key: string) => Promise<unknown>, shouldRepeat: (key: string) => boolean) {
  const repeat = new Map<string, boolean>(); // key present = in flight; value = another call arrived meanwhile
  const run = (key: string) => {
    repeat.set(key, false);
    send(key)
      .catch(() => {})
      .finally(() => {
        const again = repeat.get(key);
        repeat.delete(key);
        if (again && shouldRepeat(key)) run(key);
      });
  };
  return (key: string) => {
    if (repeat.has(key)) repeat.set(key, true);
    else run(key);
  };
}
