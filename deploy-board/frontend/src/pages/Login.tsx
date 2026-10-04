import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth";
import { ErrorBox, Spinner } from "../components/ui";
import { useAsync } from "../hooks/useAsync";

export function Login() {
  const { user, signIn } = useAuth();
  const status = useAsync(() => api.authStatus(), []);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;
  if (status.loading && !status.data) return <Spinner />;
  const firstRun = status.data?.registration_open ?? false;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = firstRun ? await api.register(username, password) : await api.login(username, password);
      signIn(res.token, res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <h1>🚀 DeployBoard</h1>
      {firstRun && <p className="muted">First start: create the administrator account.</p>}
      {status.error && <ErrorBox message={status.error} onRetry={status.reload} />}
      <form onSubmit={(e) => void submit(e)}>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={firstRun ? "new-password" : "current-password"}
            minLength={firstRun ? 8 : undefined}
            required
          />
        </label>
        {error && <ErrorBox message={error} />}
        <button disabled={busy}>{busy ? "…" : firstRun ? "Create account" : "Sign in"}</button>
      </form>
    </div>
  );
}
