import { useEffect, useRef, useState } from "react";
import type { LogLine } from "../api/types";
import type { StreamState } from "../hooks/useDeploymentStream";

const NOTE: Record<StreamState, string> = {
  connecting: "connecting…",
  live: "● live",
  closed: "finished",
  error: "stream lost — showing stored logs",
};

export function LogViewer({ lines, state }: { lines: LogLine[]; state: StreamState }) {
  const box = useRef<HTMLPreElement>(null);
  const [follow, setFollow] = useState(true);

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines, follow]);

  return (
    <div>
      <div className="row between">
        <span className={`muted stream ${state}`}>{NOTE[state]}</span>
        <label className="muted">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow
        </label>
      </div>
      <pre className="logs" ref={box} aria-label="deployment logs">
        {lines.length === 0 ? "No output yet." : lines.map((l) => l.line).join("\n")}
      </pre>
    </div>
  );
}
