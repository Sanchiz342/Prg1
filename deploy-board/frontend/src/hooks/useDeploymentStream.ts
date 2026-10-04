import { useEffect, useRef, useState } from "react";
import { streamUrl, tokenStore } from "../api/client";
import type { LogLine, StreamEvent } from "../api/types";
import { mergeLogs } from "../lib/logs";

export type StreamState = "connecting" | "live" | "closed" | "error";

/** Live logs of one deployment over WebSocket. The server replays stored history first, so reconnecting
 *  is safe (lines are de-duplicated by id). `onChange` fires when stage/deployment state changes, so the
 *  page can re-fetch the authoritative state from the REST API instead of duplicating backend logic. */
export function useDeploymentStream(deploymentId: number, active: boolean, onChange: () => void) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    setLines([]);
    setState("connecting");
  }, [deploymentId]);

  useEffect(() => {
    const token = tokenStore.get();
    if (!active || !token) {
      setState("closed");
      return;
    }
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let finished = false;
    let attempts = 0;
    let pending: LogLine[] = [];
    let flush: ReturnType<typeof setTimeout> | undefined;

    const flushLines = () => {
      flush = undefined;
      const batch = pending;
      pending = [];
      setLines((cur) => mergeLogs(cur, batch));
    };

    const connect = () => {
      ws = new WebSocket(streamUrl(deploymentId, token));
      ws.onopen = () => {
        attempts = 0;
        setState("live");
      };
      ws.onmessage = (m: MessageEvent<string>) => {
        const ev = JSON.parse(m.data) as StreamEvent;
        if (ev.type === "log") {
          pending.push({ id: ev.id, stage: ev.stage, line: ev.line, ts: ev.ts });
          flush ??= setTimeout(flushLines, 50); // batch bursts into one render
        } else {
          if (ev.type === "deployment" && (ev.status === "SUCCESS" || ev.status === "FAILED")) finished = true;
          onChangeRef.current();
        }
      };
      ws.onclose = () => {
        if (stopped) return;
        if (finished || attempts >= 5) {
          setState(finished ? "closed" : "error");
          return;
        }
        attempts += 1;
        setState("connecting");
        retry = setTimeout(connect, Math.min(1000 * attempts, 5000));
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(flush);
      ws?.close();
    };
  }, [deploymentId, active]);

  const addLines = (extra: LogLine[]) => setLines((cur) => mergeLogs(cur, extra));
  return { lines, state, addLines };
}
