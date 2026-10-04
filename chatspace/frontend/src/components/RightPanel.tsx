import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useMembers, useNotifications } from "../hooks";
import type { Channel, Workspace } from "../types";

interface Props { workspace: Workspace; onOpenChannel(channelId: string): void }
type Tab = "members" | "notifications" | "search";

export function RightPanel({ workspace, onOpenChannel }: Props) {
  const [tab, setTab] = useState<Tab>("members");
  const notifications = useNotifications();
  const unread = notifications.data?.filter((n) => !n.read_at).length ?? 0;

  return (
    <aside className="panel" aria-label="Details">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "members"} onClick={() => setTab("members")}>Members</button>
        <button role="tab" aria-selected={tab === "notifications"} onClick={() => setTab("notifications")}>🔔{unread > 0 && <span className="badge">{unread}</span>}</button>
        <button role="tab" aria-selected={tab === "search"} onClick={() => setTab("search")}>Search</button>
      </div>
      {tab === "members" && <Members workspace={workspace} />}
      {tab === "notifications" && <Notifications onOpenChannel={onOpenChannel} />}
      {tab === "search" && <Search workspace={workspace} />}
    </aside>
  );
}

function Members({ workspace }: { workspace: Workspace }) {
  const members = useMembers(workspace.id);
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const add = useMutation({
    mutationFn: () => api.addMember(workspace.id, email),
    onSuccess: () => { setEmail(""); qc.invalidateQueries({ queryKey: ["members", workspace.id] }); },
  });
  const canAdd = workspace.role !== "MEMBER";
  const sorted = [...(members.data ?? [])].sort((a, b) => Number(b.presence !== "OFFLINE") - Number(a.presence !== "OFFLINE") || a.username.localeCompare(b.username));
  return (
    <div className="panel-body">
      {members.isPending && <p className="muted">Loading…</p>}
      {members.isError && <p className="error">Couldn't load members. <button className="link" onClick={() => members.refetch()}>Retry</button></p>}
      <ul>
        {sorted.map((m) => (
          <li key={m.id}><span className={`dot ${m.presence}`} role="img" aria-label={m.presence.toLowerCase()} /> {m.username} {m.role !== "MEMBER" && <small className="muted">{m.role.toLowerCase()}</small>}</li>
        ))}
      </ul>
      {canAdd && (
        <form onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }} className="inline-form">
          <input type="email" placeholder="Add member by email" aria-label="Member email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <button disabled={add.isPending}>Add</button>
          {add.isError && <p role="alert" className="error">{(add.error as Error).message}</p>}
        </form>
      )}
    </div>
  );
}

function Notifications({ onOpenChannel }: { onOpenChannel(id: string): void }) {
  const q = useNotifications();
  const qc = useQueryClient();
  const read = useMutation({
    mutationFn: (id: string) => api.markRead(id),
    onSuccess: (_v, id) => qc.setQueryData<typeof q.data>(["notifications"], (d) => d?.map((n) => (n.id === id ? { ...n, read_at: new Date().toISOString() } : n))),
  });
  return (
    <div className="panel-body">
      {q.isPending && <p className="muted">Loading…</p>}
      {q.isError && <p className="error">Couldn't load notifications. <button className="link" onClick={() => q.refetch()}>Retry</button></p>}
      {q.data?.length === 0 && <p className="muted">Nothing yet.</p>}
      <ul>
        {q.data?.map((n) => (
          <li key={n.id} className={n.read_at ? "notif" : "notif unread"}>
            <button className="link" onClick={() => { if (!n.read_at) read.mutate(n.id); if (n.channel_id) onOpenChannel(n.channel_id); }}>{n.text}</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Search({ workspace }: { workspace: Workspace }) {
  const [input, setInput] = useState("");
  const [term, setTerm] = useState("");
  const results = useQuery({ queryKey: ["search", workspace.id, term], queryFn: () => api.search(workspace.id, term), enabled: term.length > 0 });
  const channels = useQueryClientChannels(workspace.id);
  return (
    <div className="panel-body">
      <form onSubmit={(e) => { e.preventDefault(); setTerm(input.trim()); }} className="inline-form">
        <input type="search" aria-label="Search messages" placeholder="Search messages" value={input} onChange={(e) => setInput(e.target.value)} />
        <button>Go</button>
      </form>
      {results.isFetching && <p className="muted">Searching…</p>}
      {results.isError && <p className="error">Search failed.</p>}
      {results.data?.length === 0 && <p className="muted">No results.</p>}
      <ul>
        {results.data?.map((m) => (
          <li key={m.id} className="result"><small className="muted">#{channels.get(m.channel_id) ?? "?"} · {new Date(m.created_at).toLocaleDateString()}</small><br />{m.content}</li>
        ))}
      </ul>
    </div>
  );
}

function useQueryClientChannels(wid: string) {
  const qc = useQueryClient();
  const list = qc.getQueryData<Channel[]>(["channels", wid]) ?? [];
  return new Map(list.map((c) => [c.id, c.name]));
}
