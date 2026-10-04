import { useRealtime } from "../realtime";

export function ConnectionBanner() {
  const { status } = useRealtime();
  if (status === "open") return null;
  return (
    <div className="banner" role="status">
      {status === "connecting" ? "Connecting…" : "Connection lost — reconnecting… messages you send are saved when the server responds."}
    </div>
  );
}
