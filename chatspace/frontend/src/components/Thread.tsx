import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth";
import { flatten } from "../cache";
import { useMembers, useMessageActions, useMessages, useReplies, useSendMessage } from "../hooks";
import type { Message, Workspace } from "../types";
import { Composer } from "./Composer";
import { MessageItem } from "./MessageItem";

interface Props { workspace: Workspace; channelId: string; parentId: string; onClose(): void }

export function Thread({ workspace, channelId, parentId, onClose }: Props) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const members = useMembers(workspace.id);
  const replies = useReplies(parentId);
  const send = useSendMessage(channelId, user!, parentId);
  const actions = useMessageActions(channelId);
  const names = useMemo(() => new Map(members.data?.map((m) => [m.id, m.username])), [members.data]);
  const messages = useMessages(channelId); // subscribes to the shared cache so the parent stays live
  const parent = flatten(messages.data).find((m) => m.id === parentId);

  const item = (m: Message) => (
    <MessageItem key={m.id} message={m} author={names.get(m.author_id) ?? "Unknown"} meId={user!.id} role={workspace.role}
      onReact={(emoji, has) => actions.toggleReaction.mutate({ id: m.id, emoji, has })}
      onEdit={(c) => actions.edit.mutateAsync({ id: m.id, content: c })}
      onDelete={() => actions.remove.mutate(m.id)}
      onRetry={() => { qc.setQueryData<Message[]>(["replies", parentId], (d) => d?.filter((x) => x.id !== m.id)); send.mutate({ content: m.content, tmpId: `tmp-${crypto.randomUUID()}` }); }}
      onDismiss={() => qc.setQueryData<Message[]>(["replies", parentId], (d) => d?.filter((x) => x.id !== m.id))} />
  );

  return (
    <aside className="panel thread" aria-label="Thread">
      <header className="panel-head"><h2>Thread</h2><button aria-label="Close thread" onClick={onClose}>✕</button></header>
      <div className="messages">
        {parent && item(parent)}
        <hr />
        {replies.isPending && <p className="muted center">Loading replies…</p>}
        {replies.isError && <p className="error center">Couldn't load replies. <button className="link" onClick={() => replies.refetch()}>Retry</button></p>}
        {replies.data?.map((m) => item(m))}
      </div>
      <Composer channelId={channelId} placeholder="Reply…" typing={false} onSend={(content) => send.mutate({ content, tmpId: `tmp-${crypto.randomUUID()}` })} />
    </aside>
  );
}
