import { useCallback, useState } from "react";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { Session, loadSession, saveSession } from "./api";
import { useLiveTick } from "./hooks";
import Dashboard from "./pages/Dashboard";
import IncidentPage from "./pages/IncidentPage";
import Login from "./pages/Login";

export default function App() {
  const [session, setSession] = useState<Session | null>(loadSession());
  const signIn = (s: Session) => { saveSession(s); setSession(s); };
  const signOut = useCallback(() => { saveSession(null); setSession(null); }, []);

  if (!session) return <Login onLogin={signIn} />;
  return <Shell session={session} onSignOut={signOut} />;
}

function Shell({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const { tick, connected } = useLiveTick(session);
  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">🧠 AI DevMonitor</Link>
        <span className={`live ${connected ? "on" : "off"}`}>{connected ? "● live" : "○ reconnecting"}</span>
        <span className="spacer" />
        <span className="role">{session.role}</span>
        <button className="ghost" onClick={onSignOut}>Sign out</button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard session={session} tick={tick} onUnauthorized={onSignOut} />} />
          <Route path="/incidents/:id" element={<IncidentPage session={session} tick={tick} onUnauthorized={onSignOut} />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </>
  );
}
