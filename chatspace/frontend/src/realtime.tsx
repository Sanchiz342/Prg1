import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "./auth";
import { bumpUnread, clearUnread, singleFlight } from "./unread";
import { applyReaction, bumpReplyCount, markDeleted, patchMessage, upsertMessage, type MessagesCache } from "./cache";
import { RealtimeClient, type ConnectionStatus } from "./ws";
import { api } from "./api";
import type { AppNotification, Member, Message, ServerEvent, UnreadMap } from "./types";

interface RealtimeState {
  status: ConnectionStatus;
  subscribe(channelId: string): void;
  unsubscribe(channelId: string): void;
  sendTyping(channelId: string, started: boolean): void;
  typingIn(channelId: string): string[];
  /** The chat view tells us which channel is on screen and whether the user is following the live end of it. */
  setViewing(channelId: string | null, workspaceId?: string, following?: boolean): void;
  markRead(channelId: string, workspaceId: string): void;
}

const Ctx = createContext<RealtimeState | null>(null);
const TYPING_MS = 5000;

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const { token, user, logout } = useAuth();
  const userIdRef = useRef(user?.id);
  userIdRef.current = user?.id;
  const qc = useQueryClient();
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [typing, setTyping] = useState<Record<string, string[]>>({});
  const client = useRef<RealtimeClient | null>(null);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const viewing = useRef<{ channelId: string | null; workspaceId?: string; following: boolean }>({ channelId: null, following: false });

  const workspaceOf = useRef(new Map<string, string>());
  const sendRead = useRef(singleFlight(
    (channelId) => api.markChannelRead(channelId).catch((e) => {
      qc.invalidateQueries({ queryKey: ["unread", workspaceOf.current.get(channelId)] }); // unsure what the server has: resync
      throw e;
    }),
    (channelId) => viewing.current.channelId === channelId && viewing.current.following && document.visibilityState === "visible",
  ));
  const markRead = useCallback((channelId: string, workspaceId: string) => {
    workspaceOf.current.set(channelId, workspaceId);
    qc.setQueryData<UnreadMap>(["unread", workspaceId], (d) => clearUnread(d, channelId)); // optimistic
    sendRead.current(channelId);
  }, [qc]);

  const isViewing = (channelId: string) =>
    viewing.current.channelId === channelId && viewing.current.following && document.visibilityState === "visible";

  const setTypingUser = useCallback((channel: string, user: string, on: boolean) => {
    const key = `${channel}:${user}`;
    clearTimeout(timers.current.get(key));
    timers.current.delete(key);
    if (on) timers.current.set(key, setTimeout(() => setTypingUser(channel, user, false), TYPING_MS));
    setTyping((prev) => {
      const cur = prev[channel] ?? [];
      if (on === cur.includes(user)) return prev;
      return { ...prev, [channel]: on ? [...cur, user] : cur.filter((u) => u !== user) };
    });
  }, []);

  useEffect(() => {
    if (!token) return;
    const timerMap = timers.current;
    const patch = (channelId: string, fn: (d: MessagesCache) => MessagesCache) =>
      qc.setQueryData<MessagesCache>(["messages", channelId], (d) => (d ? fn(d) : d));

    const onEvent = (e: ServerEvent) => {
      switch (e.type) {
        case "message.created": {
          const m = e.message;
          setTypingUser(m.channel_id, m.author_id, false);
          if (m.reply_to) {
            patch(m.channel_id, (d) => bumpReplyCount(d, m.reply_to!));
            qc.setQueryData<Message[]>(["replies", m.reply_to], (d) => (d && !d.some((x) => x.id === m.id) ? [...d, m] : d));
          } else patch(m.channel_id, (d) => upsertMessage(d, m));
          break;
        }
        case "message.updated": {
          const m = e.message;
          patch(m.channel_id, (d) => patchMessage(d, m.id, (old) => ({ ...m, mine: old.mine, pending: false })));
          qc.setQueryData<Message[]>(["replies", m.reply_to], (d) => d?.map((x) => (x.id === m.id ? { ...m, mine: x.mine } : x)));
          break;
        }
        case "message.deleted":
          patch(e.channel_id, (d) => markDeleted(d, e.message_id));
          qc.invalidateQueries({ queryKey: ["replies"] });
          break;
        case "reaction.added":
        case "reaction.removed": {
          if (e.user_id === userIdRef.current) break; // own reactions are applied optimistically
          const delta = e.type === "reaction.added" ? 1 : -1;
          qc.getQueryCache().findAll({ queryKey: ["messages"] }).forEach((q) => {
            qc.setQueryData<MessagesCache>(q.queryKey, (d) => (d ? applyReaction(d, e.message_id, e.reaction, delta, false) : d));
          });
          qc.setQueriesData<Message[]>({ queryKey: ["replies"] }, (d) =>
            d?.map((m) => (m.id === e.message_id ? { ...m, reactions: nextReactions(m.reactions, e.reaction, delta) } : m)));
          break;
        }
        case "typing.started":
        case "typing.stopped":
          if (e.user_id !== userIdRef.current) setTypingUser(e.channel_id, e.user_id, e.type === "typing.started");
          break;
        case "user.online":
        case "user.offline":
          qc.setQueriesData<Member[]>({ queryKey: ["members"] }, (d) =>
            d?.map((m) => (m.id === e.user_id ? { ...m, presence: e.type === "user.online" ? "ONLINE" : "OFFLINE" } : m)));
          break;
        case "channel.created":
          qc.invalidateQueries({ queryKey: ["channels", e.channel.workspace_id] });
          break;
        case "channel.activity": {
          if (e.author_id === userIdRef.current) break;
          if (isViewing(e.channel_id)) { markRead(e.channel_id, e.workspace_id); break; }  // on screen: already seen
          qc.setQueryData<UnreadMap>(["unread", e.workspace_id], (d) => (d ? bumpUnread(d, e.channel_id) : d));
          break;
        }
        case "channel.read":
          qc.setQueryData<UnreadMap>(["unread", e.workspace_id], (d) => clearUnread(d, e.channel_id));
          break;
        case "notification.created":
          qc.setQueryData<AppNotification[]>(["notifications"], (d) => [e.notification, ...(d ?? [])]);
          if (e.notification.kind === "mention" && e.notification.channel_id && !isViewing(e.notification.channel_id)) {
            qc.setQueriesData<UnreadMap>({ queryKey: ["unread"] }, (d) =>
              d && e.notification.channel_id && e.notification.channel_id in d ? bumpUnread(d, e.notification.channel_id, "mentions") : d);
          }
          if (e.notification.kind === "invitation") qc.invalidateQueries({ queryKey: ["workspaces"] }); // newly added to a workspace
          break;
      }
    };

    const rt = new RealtimeClient({
      url: () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?token=${encodeURIComponent(token)}`,
      onEvent,
      onStatus: setStatus,
      // after a drop we may have missed events: refetch everything visible
      onReady: (isReconnect) => {
        if (isReconnect) qc.invalidateQueries({ predicate: (q) => ["messages", "replies", "members", "notifications", "channels", "unread"].includes(q.queryKey[0] as string) });
      },
      onAuthFailure: logout,
    });
    client.current = rt;
    rt.start();
    const onVis = () => rt.setAway(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      rt.stop();
      client.current = null;
      timerMap.forEach(clearTimeout);
      timerMap.clear();
    };
  }, [token, qc, logout, setTypingUser]);

  const value = useMemo<RealtimeState>(() => ({
    status,
    subscribe: (c) => client.current?.subscribe(c),
    unsubscribe: (c) => client.current?.unsubscribe(c),
    sendTyping: (c, s) => client.current?.typing(c, s),
    typingIn: (c) => typing[c] ?? [],
    setViewing: (channelId, workspaceId, following = true) => { viewing.current = { channelId, workspaceId, following }; },
    markRead,
  }), [status, typing, markRead]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function nextReactions(r: Record<string, number>, emoji: string, delta: 1 | -1) {
  const out = { ...r, [emoji]: Math.max(0, (r[emoji] ?? 0) + delta) };
  if (!out[emoji]) delete out[emoji];
  return out;
}

export function useRealtime() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useRealtime outside RealtimeProvider");
  return v;
}
