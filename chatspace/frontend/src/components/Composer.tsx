import { useRef, useState, type KeyboardEvent } from "react";
import { useRealtime } from "../realtime";

interface Props { channelId: string; placeholder: string; onSend(content: string): void; typing?: boolean }

export function Composer({ channelId, placeholder, onSend, typing = true }: Props) {
  const rt = useRealtime();
  const [text, setText] = useState("");
  const lastSent = useRef(0);
  const idle = useRef<ReturnType<typeof setTimeout>>(undefined);

  function changed(v: string) {
    setText(v);
    if (!typing || !v) return;
    const now = Date.now();
    if (now - lastSent.current > 3000) { rt.sendTyping(channelId, true); lastSent.current = now; }
    clearTimeout(idle.current);
    idle.current = setTimeout(() => { rt.sendTyping(channelId, false); lastSent.current = 0; }, 4000);
  }

  function submit() {
    const content = text.trim();
    if (!content) return;
    if (typing) { clearTimeout(idle.current); rt.sendTyping(channelId, false); lastSent.current = 0; }
    setText("");
    onSend(content);
  }

  function key(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  }

  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <textarea aria-label={placeholder} placeholder={placeholder} rows={1} maxLength={4000} value={text} onChange={(e) => changed(e.target.value)} onKeyDown={key} />
      <button className="primary" disabled={!text.trim()} aria-label="Send">➤</button>
    </form>
  );
}
