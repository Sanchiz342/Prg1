import type { AppNotification, Channel, Member, Message, MessagePage, UnreadMap, User, Workspace } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

let token: string | null = null;
let onUnauthorized: () => void = () => {};
export const setToken = (t: string | null) => { token = t; };
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch("/api" + url, {
    method,
    headers: { ...(body !== undefined && { "Content-Type": "application/json" }), ...(token && { Authorization: `Bearer ${token}` }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    if (res.status === 401 && token) onUnauthorized();
    let detail = res.statusText;
    try {
      const d = await res.json();
      detail = typeof d.detail === "string" ? d.detail : "Request failed";
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const enc = encodeURIComponent;

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("POST", "/auth/login", { email, password }),
  register: (username: string, email: string, password: string) =>
    request<{ access_token: string; user: User }>("POST", "/auth/register", { username, email, password }),
  me: () => request<User>("GET", "/auth/me"),

  workspaces: () => request<Workspace[]>("GET", "/workspaces"),
  createWorkspace: (name: string) => request<Workspace>("POST", "/workspaces", { name }),
  members: (wid: string) => request<Member[]>("GET", `/workspaces/${wid}/members`),
  addMember: (wid: string, email: string) => request("POST", `/workspaces/${wid}/members`, { email }),
  search: (wid: string, q: string) => request<Message[]>("GET", `/workspaces/${wid}/search?q=${enc(q)}`),

  channels: (wid: string) => request<Channel[]>("GET", `/workspaces/${wid}/channels`),
  createChannel: (wid: string, name: string, type: "PUBLIC" | "PRIVATE") =>
    request<Channel>("POST", `/workspaces/${wid}/channels`, { name, type }),

  messages: (cid: string, before?: string | null) =>
    request<MessagePage>("GET", `/channels/${cid}/messages?limit=50${before ? `&before=${before}` : ""}`),
  send: (cid: string, content: string, replyTo?: string | null) =>
    request<Message>("POST", `/channels/${cid}/messages`, { content, reply_to: replyTo ?? null }),
  replies: (mid: string) => request<Message[]>("GET", `/messages/${mid}/replies`),
  edit: (mid: string, content: string) => request<Message>("PATCH", `/messages/${mid}`, { content }),
  remove: (mid: string) => request<void>("DELETE", `/messages/${mid}`),
  react: (mid: string, emoji: string) => request<void>("PUT", `/messages/${mid}/reactions/${enc(emoji)}`),
  unreact: (mid: string, emoji: string) => request<void>("DELETE", `/messages/${mid}/reactions/${enc(emoji)}`),

  unread: (wid: string) => request<{ channels: UnreadMap }>("GET", `/workspaces/${wid}/unread`).then((d) => d.channels),
  markChannelRead: (cid: string) => request<void>("POST", `/channels/${cid}/read`),

  notifications: () => request<AppNotification[]>("GET", "/notifications"),
  markRead: (id: string) => request<void>("POST", `/notifications/${id}/read`),
};
