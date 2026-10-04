import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { applyReaction, confirmPending, markDeleted, patchMessage, removeMessage, type MessagesCache } from "./cache";
import type { Message, User } from "./types";

export const useWorkspaces = () => useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
export const useChannels = (wid: string | undefined) =>
  useQuery({ queryKey: ["channels", wid], queryFn: () => api.channels(wid!), enabled: !!wid });
export const useMembers = (wid: string | undefined) =>
  useQuery({ queryKey: ["members", wid], queryFn: () => api.members(wid!), enabled: !!wid, refetchInterval: 60_000 });
export const useNotifications = () => useQuery({ queryKey: ["notifications"], queryFn: api.notifications });

export const useMessages = (cid: string | undefined) =>
  useInfiniteQuery({
    queryKey: ["messages", cid],
    queryFn: ({ pageParam }) => api.messages(cid!, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.has_more ? last.next_before : undefined),
    enabled: !!cid,
  });

export const useReplies = (mid: string | undefined) =>
  useQuery({ queryKey: ["replies", mid], queryFn: () => api.replies(mid!), enabled: !!mid });

/** Optimistic send: message shows instantly as pending, is confirmed by the response, or marked failed. */
export function useSendMessage(channelId: string, me: User, replyTo?: string | null) {
  const qc = useQueryClient();
  const key = replyTo ? ["replies", replyTo] : ["messages", channelId];
  return useMutation({
    mutationFn: ({ content }: { content: string; tmpId: string }) => api.send(channelId, content, replyTo),
    onMutate: ({ content, tmpId }) => {
      const tmp: Message = {
        id: tmpId, channel_id: channelId, author_id: me.id, content, reply_to: replyTo ?? null,
        created_at: new Date().toISOString(), updated_at: null, deleted_at: null, reactions: {}, mine: [], reply_count: 0, pending: true,
      };
      if (replyTo) qc.setQueryData<Message[]>(key, (d) => [...(d ?? []), tmp]);
      else qc.setQueryData<MessagesCache>(key, (d) => d && { ...d, pages: [{ ...d.pages[0], messages: [tmp, ...d.pages[0].messages] }, ...d.pages.slice(1)] });
    },
    onSuccess: (real, { tmpId }) => {
      if (replyTo) qc.setQueryData<Message[]>(key, (d) => d && (d.some((m) => m.id === real.id) ? d.filter((m) => m.id !== tmpId) : d.map((m) => (m.id === tmpId ? real : m))));
      else qc.setQueryData<MessagesCache>(key, (d) => d && confirmPending(d, tmpId, real));
    },
    onError: (_e, { tmpId }) => {
      if (replyTo) qc.setQueryData<Message[]>(key, (d) => d?.map((m) => (m.id === tmpId ? { ...m, pending: false, failed: true } : m)));
      else qc.setQueryData<MessagesCache>(key, (d) => d && patchMessage(d, tmpId, (m) => ({ ...m, pending: false, failed: true })));
    },
  });
}

export function useMessageActions(channelId: string) {
  const qc = useQueryClient();
  const key = ["messages", channelId];
  const edit = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) => api.edit(id, content),
    onSuccess: (m) => {
      qc.setQueryData<MessagesCache>(key, (d) => d && patchMessage(d, m.id, () => m));
      qc.invalidateQueries({ queryKey: ["replies"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.remove(id),
    onSuccess: (_v, id) => {
      qc.setQueryData<MessagesCache>(key, (d) => d && markDeleted(d, id));
      qc.invalidateQueries({ queryKey: ["replies"] });
    },
  });
  /** Optimistic toggle with rollback; the websocket echo for our own reaction is a no-op because we skip re-applying it. */
  const toggleReaction = useMutation({
    mutationFn: ({ id, emoji, has }: { id: string; emoji: string; has: boolean }) => (has ? api.unreact(id, emoji) : api.react(id, emoji)),
    onMutate: async ({ id, emoji, has }) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<MessagesCache>(key);
      qc.setQueryData<MessagesCache>(key, (d) => d && applyReaction(d, id, emoji, has ? -1 : 1, true));
      return { prev };
    },
    onError: (_e, _v, ctx) => ctx?.prev && qc.setQueryData(key, ctx.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: ["replies"] }),
  });
  const dismiss = (id: string) => qc.setQueryData<MessagesCache>(key, (d) => d && removeMessage(d, id));
  return { edit, remove, toggleReaction, dismiss };
}
