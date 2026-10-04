import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";
import { useChannels, useWorkspaces } from "../hooks";
import type { Channel, Workspace } from "../types";

interface Props {
  workspace: Workspace | undefined;
  channel: Channel | undefined;
  onWorkspace(w: Workspace): void;
  onChannel(c: Channel): void;
}

export function Sidebar({ workspace, channel, onWorkspace, onChannel }: Props) {
  const { user, logout } = useAuth();
  const qc = useQueryClient();
  const workspaces = useWorkspaces();
  const channels = useChannels(workspace?.id);
  const [error, setError] = useState("");
  const isAdmin = workspace?.role === "OWNER" || workspace?.role === "ADMIN";

  const createWs = useMutation({
    mutationFn: (name: string) => api.createWorkspace(name),
    onSuccess: (w) => { qc.invalidateQueries({ queryKey: ["workspaces"] }); onWorkspace(w); },
    onError: (e: Error) => setError(e.message),
  });
  const createCh = useMutation({
    mutationFn: ({ name, type }: { name: string; type: "PUBLIC" | "PRIVATE" }) => api.createChannel(workspace!.id, name, type),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ["channels", workspace!.id] }); onChannel(c); },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <nav className="sidebar" aria-label="Workspaces and channels">
      <div className="ws-switch">
        <select aria-label="Workspace" value={workspace?.id ?? ""} onChange={(e) => { const w = workspaces.data?.find((x) => x.id === e.target.value); if (w) onWorkspace(w); }}>
          {workspaces.data?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <button title="New workspace" aria-label="New workspace" onClick={() => { const n = prompt("Workspace name"); if (n?.trim()) createWs.mutate(n.trim()); }}>＋</button>
      </div>

      <h2>Channels {isAdmin && (
        <button title="New channel" aria-label="New channel" onClick={() => {
          const n = prompt("Channel name (lowercase, digits, - or _)");
          if (!n) return;
          createCh.mutate({ name: n.trim().toLowerCase(), type: confirm("Make it private?") ? "PRIVATE" : "PUBLIC" });
        }}>＋</button>
      )}</h2>
      {channels.isPending && workspace && <p className="muted">Loading…</p>}
      {channels.isError && <p className="error">Couldn't load channels. <button className="link" onClick={() => channels.refetch()}>Retry</button></p>}
      <ul>
        {channels.data?.map((c) => (
          <li key={c.id}>
            <button className={c.id === channel?.id ? "channel on" : "channel"} aria-current={c.id === channel?.id} onClick={() => onChannel(c)}>
              {c.type === "PRIVATE" ? "🔒" : "#"} {c.name}
            </button>
          </li>
        ))}
      </ul>
      {error && <p role="alert" className="error" onClick={() => setError("")}>{error}</p>}

      <div className="user-panel">
        <span>{user?.username}</span>
        <button className="link" onClick={logout}>Log out</button>
      </div>
    </nav>
  );
}
