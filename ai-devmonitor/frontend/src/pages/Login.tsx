import { FormEvent, useState } from "react";
import { Session, login } from "../api";

export default function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { onLogin(await login(user, pass)); }
    catch (err) { setError(err instanceof Error ? err.message : "Login failed"); }
    finally { setBusy(false); }
  };

  return (
    <form className="login card" onSubmit={submit}>
      <h1>🧠 AI DevMonitor</h1>
      <label>Username<input value={user} onChange={(e) => setUser(e.target.value)} autoFocus /></label>
      <label>Password<input type="password" value={pass} onChange={(e) => setPass(e.target.value)} /></label>
      {error && <p className="error">{error}</p>}
      <button disabled={busy || !user || !pass}>Sign in</button>
    </form>
  );
}
