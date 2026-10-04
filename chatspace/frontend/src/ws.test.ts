import { RealtimeClient, type ConnectionStatus } from "./ws";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 1;
  sent: string[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeSocket.instances.push(this); }
  send(d: string) { this.sent.push(d); }
  close() { this.readyState = 3; }
  serverSays(frame: object) { this.onmessage?.({ data: JSON.stringify(frame) }); }
  drop(code = 1006) { this.readyState = 3; this.onclose?.({ code }); }
}

function setup() {
  FakeSocket.instances = [];
  const statuses: ConnectionStatus[] = [];
  const events: unknown[] = [];
  const ready: boolean[] = [];
  const onAuthFailure = vi.fn();
  const client = new RealtimeClient({
    url: () => "ws://x/ws?token=t", onEvent: (e) => events.push(e), onStatus: (s) => statuses.push(s),
    onReady: (r) => ready.push(r), onAuthFailure, createSocket: (u) => new FakeSocket(u) as unknown as WebSocket,
  });
  return { client, statuses, events, ready, onAuthFailure, last: () => FakeSocket.instances.at(-1)! };
}

beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal("WebSocket", { OPEN: 1 }); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

test("connects, reports open on ready and forwards events", () => {
  const t = setup();
  t.client.start();
  t.last().serverSays({ type: "ready", user_id: "u" });
  t.last().serverSays({ type: "typing.started", channel_id: "c", user_id: "x" });
  expect(t.statuses).toEqual(["connecting", "open"]);
  expect(t.events).toHaveLength(2);
  expect(t.ready).toEqual([false]);
});

test("reconnects with backoff, resubscribes channels and flags the reconnect", () => {
  const t = setup();
  t.client.start();
  t.last().serverSays({ type: "ready", user_id: "u" });
  t.client.subscribe("c1");
  t.last().drop();
  expect(t.statuses.at(-1)).toBe("reconnecting");
  expect(FakeSocket.instances).toHaveLength(1);
  vi.advanceTimersByTime(1000); // first retry is within [0.5s, 1s]
  expect(FakeSocket.instances).toHaveLength(2);
  t.last().serverSays({ type: "ready", user_id: "u" });
  expect(t.last().sent.map((s) => JSON.parse(s))).toContainEqual({ type: "subscribe", channel_id: "c1" });
  expect(t.ready).toEqual([false, true]);
  expect(t.statuses.at(-1)).toBe("open");
});

test("backoff doubles while the server stays down (jitter pinned to the max)", () => {
  vi.spyOn(Math, "random").mockReturnValue(1);
  const t = setup();
  t.client.start();
  for (const wait of [1000, 2000, 4000]) {
    const n = FakeSocket.instances.length;
    t.last().drop();
    vi.advanceTimersByTime(wait - 1);
    expect(FakeSocket.instances).toHaveLength(n);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(n + 1);
  }
});

test("auth failure (4401) stops retrying and logs out", () => {
  const t = setup();
  t.client.start();
  t.last().drop(4401);
  vi.advanceTimersByTime(60_000);
  expect(FakeSocket.instances).toHaveLength(1);
  expect(t.onAuthFailure).toHaveBeenCalledOnce();
});

test("stop() does not reconnect; heartbeat is sent while open", () => {
  const t = setup();
  t.client.start();
  t.last().serverSays({ type: "ready", user_id: "u" });
  vi.advanceTimersByTime(20_000);
  expect(t.last().sent.map((s) => JSON.parse(s).type)).toContain("heartbeat");
  t.client.stop();
  vi.advanceTimersByTime(60_000);
  expect(FakeSocket.instances).toHaveLength(1);
});
