import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, setToken, setUnauthorizedHandler } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  token: string | null;
  login(email: string, password: string): Promise<void>;
  register(username: string, email: string, password: string): Promise<void>;
  logout(): void;
}

const Ctx = createContext<AuthState | null>(null);
const KEY = "chatspace.session";

function load(): { token: string; user: User } | null {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "null"); } catch { return null; }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [session, setSession] = useState(() => {
    const s = load();
    if (s) setToken(s.token);
    return s;
  });

  const logout = useCallback(() => {
    setToken(null);
    try { localStorage.removeItem(KEY); } catch { /* storage unavailable */ }
    setSession(null);
    qc.clear();
  }, [qc]);

  useEffect(() => setUnauthorizedHandler(logout), [logout]);

  const accept = useCallback((d: { access_token: string; user: User }) => {
    setToken(d.access_token);
    try { localStorage.setItem(KEY, JSON.stringify({ token: d.access_token, user: d.user })); } catch { /* ignore */ }
    setSession({ token: d.access_token, user: d.user });
  }, []);

  const value = useMemo<AuthState>(() => ({
    user: session?.user ?? null,
    token: session?.token ?? null,
    login: async (e, p) => accept(await api.login(e, p)),
    register: async (u, e, p) => accept(await api.register(u, e, p)),
    logout,
  }), [session, accept, logout]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
