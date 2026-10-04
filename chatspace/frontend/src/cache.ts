import type { InfiniteData } from "@tanstack/react-query";
import type { Message, MessagePage } from "./types";

/** Pure helpers that apply realtime events to the React Query message cache (pages are newest-first). */
export type MessagesCache = InfiniteData<MessagePage, unknown>;

const mapAll = (data: MessagesCache, fn: (m: Message) => Message): MessagesCache => ({
  ...data,
  pages: data.pages.map((p) => ({ ...p, messages: p.messages.map(fn) })),
});

export const hasMessage = (data: MessagesCache, id: string) =>
  data.pages.some((p) => p.messages.some((m) => m.id === id));

/** Insert a new top-level message, or replace an existing one (keeps viewer-specific `mine`). */
export function upsertMessage(data: MessagesCache, msg: Message): MessagesCache {
  if (hasMessage(data, msg.id)) {
    return mapAll(data, (m) => (m.id === msg.id ? { ...msg, mine: msg.mine ?? m.mine, pending: false } : m));
  }
  // the websocket echo of our own optimistic message replaces it instead of duplicating it
  const pending = data.pages.flatMap((p) => p.messages).find((m) => m.pending && m.author_id === msg.author_id && m.content === msg.content);
  if (pending) return mapAll(data, (m) => (m.id === pending.id ? msg : m));
  const [first, ...rest] = data.pages;
  if (!first) return data;
  return { ...data, pages: [{ ...first, messages: [msg, ...first.messages] }, ...rest] };
}

export function patchMessage(data: MessagesCache, id: string, fn: (m: Message) => Message): MessagesCache {
  return mapAll(data, (m) => (m.id === id ? fn(m) : m));
}

export function removeMessage(data: MessagesCache, id: string): MessagesCache {
  return { ...data, pages: data.pages.map((p) => ({ ...p, messages: p.messages.filter((m) => m.id !== id) })) };
}

export function markDeleted(data: MessagesCache, id: string): MessagesCache {
  return patchMessage(data, id, (m) => ({ ...m, content: "", deleted_at: new Date().toISOString() }));
}

/** Swap an optimistic message for the confirmed one (or drop it if the websocket echo already added it). */
export function confirmPending(data: MessagesCache, tmpId: string, real: Message): MessagesCache {
  return hasMessage(data, real.id) ? removeMessage(data, tmpId) : mapAll(data, (m) => (m.id === tmpId ? real : m));
}

export function applyReaction(data: MessagesCache, id: string, emoji: string, delta: 1 | -1, byMe: boolean): MessagesCache {
  return patchMessage(data, id, (m) => {
    const count = Math.max(0, (m.reactions[emoji] ?? 0) + delta);
    const reactions = { ...m.reactions };
    if (count === 0) delete reactions[emoji]; else reactions[emoji] = count;
    let mine = m.mine;
    if (byMe) {
      const set = new Set(m.mine ?? []);
      if (delta === 1) set.add(emoji); else set.delete(emoji);
      mine = [...set];
    }
    return { ...m, reactions, mine };
  });
}

export const bumpReplyCount = (data: MessagesCache, parentId: string): MessagesCache =>
  patchMessage(data, parentId, (m) => ({ ...m, reply_count: m.reply_count + 1 }));

export const flatten = (data: MessagesCache | undefined): Message[] =>
  data ? data.pages.flatMap((p) => p.messages).slice().reverse() : [];
