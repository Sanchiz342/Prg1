import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, Session, request, wsUrl } from "./api";

/** Fetch + refetch on live events. Returns [data, error, reload]. */
export function useApi<T>(path: string | null, session: Session, onUnauthorized: () => void, refreshKey = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    if (!path) return;
    request<T>(path, session)
      .then((d) => { setData(d); setError(null); })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) onUnauthorized();
        else setError(e instanceof Error ? e.message : "request failed");
      });
  }, [path, session, onUnauthorized]);
  useEffect(reload, [reload, refreshKey]);
  return [data, error, reload] as const;
}

/** Subscribes to the backend WebSocket; bumps a counter on every event so views refetch. */
export function useLiveTick(session: Session) {
  const [tick, setTick] = useState(0);
  const [connected, setConnected] = useState(false);
  const retry = useRef(0);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;
    const connect = () => {
      ws = new WebSocket(wsUrl(session.token));
      ws.onopen = () => { setConnected(true); retry.current = 0; };
      ws.onmessage = () => setTick((t) => t + 1);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = window.setTimeout(connect, Math.min(1000 * 2 ** retry.current++, 15000));
      };
    };
    connect();
    const ping = window.setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("ping"), 25000);
    const poll = window.setInterval(() => setTick((t) => t + 1), 30000); // safety net if WS is down
    return () => { closed = true; clearTimeout(timer); clearInterval(ping); clearInterval(poll); ws?.close(); };
  }, [session.token]);
  return { tick, connected };
}
