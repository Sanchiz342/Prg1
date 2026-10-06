import { badge, bumpUnread, clearUnread, totalUnread } from "./unread";

test("bump creates and increments per channel, mentions separately", () => {
  let m = bumpUnread(undefined, "c1");
  m = bumpUnread(bumpUnread(m, "c1"), "c2");
  m = bumpUnread(m, "c1", "mentions");
  expect(m).toEqual({ c1: { unread: 2, mentions: 1 }, c2: { unread: 1, mentions: 0 } });
  expect(totalUnread(m)).toBe(3);
});

test("clear removes only that channel and tolerates unknown ids", () => {
  const m = { c1: { unread: 2, mentions: 1 }, c2: { unread: 1, mentions: 0 } };
  expect(clearUnread(m, "c1")).toEqual({ c2: { unread: 1, mentions: 0 } });
  expect(clearUnread(m, "zzz")).toBe(m);
  expect(clearUnread(undefined, "c1")).toEqual({});
});

test("badge caps at 99+", () => {
  expect([badge(1), badge(99), badge(100), badge(5000)]).toEqual(["1", "99", "99+", "99+"]);
});

import { singleFlight } from "./unread";

function deferred() {
  let resolve!: () => void, reject!: (e: Error) => void;
  const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test("singleFlight sends immediately, coalesces calls made in flight into one trailing repeat", async () => {
  const gates = [deferred(), deferred(), deferred()];
  const send = vi.fn((_k: string) => gates[send.mock.calls.length - 1].promise);
  const call = singleFlight(send, () => true);
  call("c1");                                   // sent right away (no timer)
  expect(send).toHaveBeenCalledTimes(1);
  call("c1"); call("c1"); call("c1");           // burst while in flight
  expect(send).toHaveBeenCalledTimes(1);
  call("c2");                                   // other keys are independent
  expect(send).toHaveBeenCalledTimes(2);
  gates[0].resolve();
  await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(3));   // exactly one trailing repeat for c1
  gates[2].resolve(); gates[1].resolve();
  await Promise.resolve();
  expect(send).toHaveBeenCalledTimes(3);
});

test("singleFlight drops the trailing repeat once the user has left the channel", async () => {
  const gate = deferred();
  let viewing = true;
  const send = vi.fn(() => gate.promise);
  const call = singleFlight(send, () => viewing);
  call("c1"); call("c1");
  viewing = false;                               // switched channel while the first request was in flight
  gate.resolve();
  await gate.promise; await Promise.resolve();
  expect(send).toHaveBeenCalledTimes(1);         // never mark as read what is no longer being looked at
});

test("singleFlight is not wedged by a failed request", async () => {
  const gate = deferred();
  const send = vi.fn().mockReturnValueOnce(gate.promise).mockResolvedValue(undefined);
  const call = singleFlight(send, () => true);
  call("c1");
  gate.reject(new Error("offline"));
  await gate.promise.catch(() => {}); await new Promise((r) => setTimeout(r, 0));
  call("c1");
  expect(send).toHaveBeenCalledTimes(2);
});
