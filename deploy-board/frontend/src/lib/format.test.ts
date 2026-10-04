import { describe, expect, it } from "vitest";
import { formatDuration, shortSha, timeAgo } from "./format";
import { mergeLogs } from "./logs";

const line = (id: number) => ({ id, stage: "s", line: `l${id}`, ts: "2026-01-01T00:00:00Z" });

describe("mergeLogs", () => {
  it("drops replayed lines and keeps order", () => {
    const a = mergeLogs([], [line(1), line(2)]);
    const b = mergeLogs(a, [line(1), line(2), line(3)]);
    expect(b.map((l) => l.id)).toEqual([1, 2, 3]);
  });
  it("returns the same array when nothing is new", () => {
    const a = [line(1)];
    expect(mergeLogs(a, [line(1)])).toBe(a);
  });
});

describe("format", () => {
  it("formats durations", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:00:42Z")).toBe("42s");
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:02:05Z")).toBe("2m 5s");
    expect(formatDuration(null, null)).toBe("–");
  });
  it("formats relative time", () => {
    const now = Date.parse("2026-01-01T01:00:00Z");
    expect(timeAgo("2026-01-01T00:59:50Z", now)).toBe("just now");
    expect(timeAgo("2026-01-01T00:30:00Z", now)).toBe("30 min ago");
  });
  it("shortens commits", () => {
    expect(shortSha("a81f92cdeadbeef")).toBe("a81f92c");
    expect(shortSha("")).toBe("–");
  });
});
