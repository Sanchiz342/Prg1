import type { ServerEvent } from "./types";

export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "closed";

interface Options {
  url: () => string;
  onEvent: (e: ServerEvent) => void;
  onStatus: (s: ConnectionStatus) => void;
  /** Called after every (re)connect once the server said "ready" so caches can catch up. */
  onReady?: (isReconnect: boolean) => void;
  onAuthFailure?: () => void;
  createSocket?: (url: string) => WebSocket;
  heartbeatMs?: number;
}

const AUTH_FAILED = 4401;

/** WebSocket wrapper: exponential-backoff reconnect, channel resubscription, heartbeat. */
export class RealtimeClient {
  private socket: WebSocket | null = null;
  private attempts = 0;
  private everOpened = false;
  private stopped = false;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private beat: ReturnType<typeof setInterval> | undefined;
  private channels = new Set<string>();

  constructor(private opts: Options) {}

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.retryTimer);
    clearInterval(this.beat);
    this.socket?.close();
    this.socket = null;
    this.opts.onStatus("closed");
  }

  subscribe(channelId: string) {
    this.channels.add(channelId);
    this.send({ type: "subscribe", channel_id: channelId });
  }

  unsubscribe(channelId: string) {
    this.channels.delete(channelId); // server has no unsubscribe; we just stop resubscribing
  }

  typing(channelId: string, started: boolean) {
    this.send({ type: started ? "typing.started" : "typing.stopped", channel_id: channelId });
  }

  setAway(away: boolean) {
    this.send({ type: "heartbeat", status: away ? "AWAY" : "ONLINE" });
  }

  get isOpen() {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  private send(frame: object) {
    if (this.isOpen) this.socket!.send(JSON.stringify(frame));
  }

  private delay() {
    const base = Math.min(30_000, 1000 * 2 ** this.attempts);
    return base / 2 + Math.random() * (base / 2);
  }

  private connect() {
    this.opts.onStatus(this.everOpened ? "reconnecting" : "connecting");
    const ws = (this.opts.createSocket ?? ((u) => new WebSocket(u)))(this.opts.url());
    this.socket = ws;
    ws.onmessage = (ev) => {
      let frame: ServerEvent;
      try { frame = JSON.parse(ev.data); } catch { return; }
      if (frame.type === "ready") {
        this.attempts = 0;
        const isReconnect = this.everOpened;
        this.everOpened = true;
        this.opts.onStatus("open");
        this.channels.forEach((c) => this.send({ type: "subscribe", channel_id: c }));
        clearInterval(this.beat);
        this.beat = setInterval(() => this.send({ type: "heartbeat" }), this.opts.heartbeatMs ?? 20_000);
        this.opts.onReady?.(isReconnect);
      }
      this.opts.onEvent(frame);
    };
    ws.onclose = (ev) => {
      if (this.socket !== ws) return; // superseded
      clearInterval(this.beat);
      if (this.stopped) return;
      if (ev.code === AUTH_FAILED) {
        this.stopped = true;
        this.opts.onStatus("closed");
        this.opts.onAuthFailure?.();
        return;
      }
      this.opts.onStatus("reconnecting");
      this.retryTimer = setTimeout(() => this.connect(), this.delay());
      this.attempts++;
    };
    ws.onerror = () => ws.close();
  }
}
