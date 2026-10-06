import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { useAuth } from "../auth";
import { flatten } from "../cache";
import { useMembers, useMessageActions, useMessages, useSendMessage } from "../hooks";
import { useRealtime } from "../realtime";
import type { Channel, Workspace } from "../types";
import { Composer } from "./Composer";
import { MessageItem } from "./MessageItem";

interface Props { workspace: Workspace; channel: Channel; onThread(id: string): void }

export function Chat({ workspace, channel, onThread }: Props) {
  const { user } = useAuth();
  const rt = useRealtime();
  const members = useMembers(workspace.id);
  const q = useMessages(channel.id);
  const send = useSendMessage(channel.id, user!);
  const actions = useMessageActions(channel.id);
  const names = useMemo(() => new Map(members.data?.map((m) => [m.id, m.username])), [members.data]);
  const messages = useMemo(() => flatten(q.data), [q.data]);
  const listRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const prevHeight = useRef(0);

  // join the channel's realtime room (re-sent automatically after reconnects)
  useEffect(() => {
    rt.subscribe(channel.id);
    return () => rt.unsubscribe(channel.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel.id]);

  // unread: tell the realtime layer what is on screen, and mark the channel read while the user is
  // looking at its live end (tab visible + scrolled to the bottom)
  const latestId = messages.at(-1)?.id;
  const markIfSeen = () => {
    rt.setViewing(channel.id, workspace.id, stick.current);
    if (q.isSuccess && stick.current && document.visibilityState === "visible") rt.markRead(channel.id, workspace.id);
  };
  useEffect(() => {
    markIfSeen();
    document.addEventListener("visibilitychange", markIfSeen);
    window.addEventListener("focus", markIfSeen);
    return () => {
      document.removeEventListener("visibilitychange", markIfSeen);
      window.removeEventListener("focus", markIfSeen);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel.id, q.isSuccess, latestId]);
  useEffect(() => () => rt.setViewing(null), [channel.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    if (prevHeight.current && !stick.current) el.scrollTop += el.scrollHeight - prevHeight.current; // keep position after "load older"
    else if (stick.current) el.scrollTop = el.scrollHeight;
    prevHeight.current = 0;
  }, [messages]);

  const typers = rt.typingIn(channel.id).map((id) => names.get(id) ?? "Someone");
  const doSend = (content: string) => send.mutate({ content, tmpId: `tmp-${crypto.randomUUID()}` });

  return (
    <section className="chat" aria-label={`Channel ${channel.name}`}>
      <header className="chat-head"><h1>{channel.type === "PRIVATE" ? "🔒" : "#"} {channel.name}</h1></header>

      <div className="messages" ref={listRef} onScroll={(e) => {
        const el = e.currentTarget;
        const wasFollowing = stick.current;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        if (stick.current !== wasFollowing) markIfSeen(); // reached the bottom -> read; scrolled up -> new ones stay unread
      }}>
        {q.isPending && <p className="muted center">Loading messages…</p>}
        {q.isError && <p className="error center">Couldn't load messages. <button className="link" onClick={() => q.refetch()}>Retry</button></p>}
        {q.hasNextPage && (
          <button className="load-older" disabled={q.isFetchingNextPage} onClick={() => { prevHeight.current = listRef.current?.scrollHeight ?? 0; stick.current = false; q.fetchNextPage(); }}>
            {q.isFetchingNextPage ? "Loading…" : "Load older messages"}
          </button>
        )}
        {q.isSuccess && messages.length === 0 && <p className="muted center">No messages yet — say hello 👋</p>}
        {messages.map((m) => (
          <MessageItem key={m.id} message={m} author={names.get(m.author_id) ?? "Unknown"} meId={user!.id} role={workspace.role}
            onReact={(emoji, has) => actions.toggleReaction.mutate({ id: m.id, emoji, has })}
            onReply={() => onThread(m.id)}
            onEdit={(content) => actions.edit.mutateAsync({ id: m.id, content })}
            onDelete={() => actions.remove.mutate(m.id)}
            onRetry={() => { actions.dismiss(m.id); doSend(m.content); }}
            onDismiss={() => actions.dismiss(m.id)} />
        ))}
      </div>

      <div className="typing" aria-live="polite">{typers.length > 0 && `${typers.slice(0, 3).join(", ")} ${typers.length > 1 ? "are" : "is"} typing…`}</div>
      <Composer channelId={channel.id} placeholder={`Message #${channel.name}`} onSend={(c) => { stick.current = true; doSend(c); }} />
      {actions.remove.isError && <p role="alert" className="error">{(actions.remove.error as Error).message}</p>}
    </section>
  );
}
