export type Role = "OWNER" | "ADMIN" | "MEMBER";
export type Presence = "ONLINE" | "AWAY" | "OFFLINE";

export interface User { id: string; username: string; email: string }
export interface Workspace { id: string; name: string; role: Role }
export interface Channel { id: string; workspace_id: string; name: string; type: "PUBLIC" | "PRIVATE" }
export interface Member { id: string; username: string; role: Role; presence: Presence }

export interface Message {
  id: string;
  channel_id: string;
  author_id: string;
  content: string;
  reply_to: string | null;
  created_at: string;
  updated_at: string | null;
  deleted_at: string | null;
  reactions: Record<string, number>;
  /** emojis the current viewer has used; only present on REST responses */
  mine?: string[];
  reply_count: number;
  /** client-only: optimistic message awaiting server confirmation */
  pending?: boolean;
  failed?: boolean;
}

export interface MessagePage { messages: Message[]; has_more: boolean; next_before: string | null }

export interface AppNotification {
  id: string; kind: "mention" | "reply" | "invitation";
  message_id: string | null; channel_id: string | null;
  text: string; read_at: string | null; created_at: string;
}

export type ServerEvent =
  | { type: "ready"; user_id: string }
  | { type: "subscribed"; channel_id: string }
  | { type: "error"; code: string; channel_id?: string }
  | { type: "message.created" | "message.updated"; message: Message }
  | { type: "message.deleted"; message_id: string; channel_id: string }
  | { type: "reaction.added" | "reaction.removed"; message_id: string; user_id: string; reaction: string }
  | { type: "user.online" | "user.offline"; user_id: string }
  | { type: "typing.started" | "typing.stopped"; channel_id: string; user_id: string }
  | { type: "channel.created"; channel: Channel }
  | { type: "notification.created"; notification: AppNotification };
