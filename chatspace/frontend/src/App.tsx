import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "./auth";
import { Chat } from "./components/Chat";
import { ConnectionBanner } from "./components/ConnectionBanner";
import { Login } from "./components/Login";
import { RightPanel } from "./components/RightPanel";
import { Sidebar } from "./components/Sidebar";
import { Thread } from "./components/Thread";
import { useChannels, useUnread, useWorkspaces } from "./hooks";
import { totalUnread } from "./unread";
import { RealtimeProvider } from "./realtime";

export function App() {
  const { token } = useAuth();
  return token ? <RealtimeProvider><Shell /></RealtimeProvider> : <Login />;
}

function Shell() {
  const qc = useQueryClient();
  const workspaces = useWorkspaces();
  const [wsId, setWsId] = useState<string | undefined>();
  const [chId, setChId] = useState<string | undefined>();
  const [threadId, setThreadId] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const workspace = workspaces.data?.find((w) => w.id === wsId) ?? workspaces.data?.[0];
  const channels = useChannels(workspace?.id);
  const channel = channels.data?.find((c) => c.id === chId) ?? channels.data?.[0];

  useEffect(() => { setThreadId(null); }, [channel?.id]);

  const unread = useUnread(workspace?.id).data;
  const total = totalUnread(unread);
  useEffect(() => {
    document.title = total > 0 ? `(${total > 99 ? "99+" : total}) ChatSpace` : "ChatSpace";
    return () => { document.title = "ChatSpace"; };
  }, [total]);

  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <ConnectionBanner />
      <header className="topbar">
        <button aria-label="Toggle navigation" onClick={() => setNavOpen((o) => !o)}>☰</button>
        <span>ChatSpace</span>
        <button aria-label="Toggle details" onClick={() => setDetailsOpen((o) => !o)}>👥</button>
      </header>
      <div className="layout" onClick={() => navOpen && setNavOpen(false)}>
        <Sidebar workspace={workspace} channel={channel}
          onWorkspace={(w) => { setWsId(w.id); setChId(undefined); }}
          onChannel={(c) => { setChId(c.id); setNavOpen(false); }} />
        <main className="main">
          {workspaces.isPending && <p className="muted center">Loading…</p>}
          {workspaces.isError && <p className="error center">Couldn't load workspaces. <button className="link" onClick={() => workspaces.refetch()}>Retry</button></p>}
          {workspaces.data?.length === 0 && <p className="muted center">You're not in any workspace yet — create one with ＋ in the sidebar.</p>}
          {workspace && channel && <Chat key={channel.id} workspace={workspace} channel={channel} onThread={setThreadId} />}
        </main>
        {workspace && channel && (threadId
          ? <Thread key={threadId} workspace={workspace} channelId={channel.id} parentId={threadId} onClose={() => setThreadId(null)} />
          : <div className={detailsOpen ? "details open" : "details"}><RightPanel workspace={workspace} onOpenChannel={(id) => { qc.invalidateQueries({ queryKey: ["channels"] }); setChId(id); setDetailsOpen(false); }} /></div>)}
      </div>
    </div>
  );
}
