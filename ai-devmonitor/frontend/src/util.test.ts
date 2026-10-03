import { describe, expect, it } from "vitest";
import { metricMax, metricUnit, pct, severityClass } from "./util";

describe("util", () => {
  it("formats confidence", () => expect(pct(0.784)).toBe("78%"));
  it("knows units", () => {
    expect(metricUnit("latency_p95")).toBe(" ms");
    expect(metricUnit("unknown")).toBe("");
  });
  it("scales percentage metrics to 100", () => {
    expect(metricMax("cpu", 40)).toBe(100);
    expect(metricMax("latency_p95", 400)).toBe(600);
  });
  it("maps severity to css", () => expect(severityClass("critical")).toBe("sev-critical"));
});
