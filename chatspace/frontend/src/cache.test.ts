import { applyReaction, bumpReplyCount, confirmPending, flatten, markDeleted, upsertMessage, type MessagesCache } from "./cache";
import type { Message } from "./types";

const msg = (id: string, over: Partial<Message> = {}): Message => ({
  id, channel_id: "c1", author_id: "u1", content: id, reply_to: null, created_at: "2025-01-01T00:00:00Z",
  updated_at: null, deleted_at: null, reactions: {}, mine: [], reply_count: 0, ...over,
});
const cache = (...pages: Message[][]): MessagesCache => ({
  pages: pages.map((messages) => ({ messages, has_more: false, next_before: null })), pageParams: pages.map(() => null),
});

test("new messages go on top of the newest page; flatten gives oldest→newest", () => {
  const d = upsertMessage(cache([msg("b"), msg("a")], [msg("old")]), msg("c"));
  expect(flatten(d).map((m) => m.id)).toEqual(["old", "a", "b", "c"]);
});

test("upsert replaces an existing message but keeps viewer-specific `mine`", () => {
  const d = upsertMessage(cache([msg("a", { mine: ["👍"], reactions: { "👍": 1 } })]), msg("a", { content: "edited", mine: undefined }));
  expect(flatten(d)[0]).toMatchObject({ content: "edited", mine: ["👍"] });
});

test("optimistic message is replaced by the websocket echo, never duplicated", () => {
  let d = cache([msg("tmp-1", { pending: true, content: "hi" })]);
  d = upsertMessage(d, msg("real", { content: "hi" }));
  expect(flatten(d).map((m) => m.id)).toEqual(["real"]);
  // the POST response arriving afterwards just drops the (already gone) temp id
  expect(flatten(confirmPending(d, "tmp-1", msg("real", { content: "hi" }))).map((m) => m.id)).toEqual(["real"]);
});

test("confirmPending swaps temp for real when the echo has not arrived", () => {
  const d = confirmPending(cache([msg("tmp-1", { pending: true })]), "tmp-1", msg("real"));
  expect(flatten(d).map((m) => m.id)).toEqual(["real"]);
});

test("reactions add/remove counts and track mine", () => {
  let d = applyReaction(cache([msg("a")]), "a", "🔥", 1, true);
  d = applyReaction(d, "a", "🔥", 1, false);
  expect(flatten(d)[0]).toMatchObject({ reactions: { "🔥": 2 }, mine: ["🔥"] });
  d = applyReaction(applyReaction(d, "a", "🔥", -1, true), "a", "🔥", -1, false);
  expect(flatten(d)[0]).toMatchObject({ reactions: {}, mine: [] });
});

test("delete keeps the row as a tombstone; reply count bumps", () => {
  const d = bumpReplyCount(markDeleted(cache([msg("a")]), "a"), "a");
  expect(flatten(d)[0]).toMatchObject({ content: "", reply_count: 1 });
  expect(flatten(d)[0].deleted_at).toBeTruthy();
});
