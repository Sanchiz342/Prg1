import { useState } from "react";
import type { Message, Role } from "../types";

const EMOJI = ["👍", "🔥", "😂", "🎉", "❤️"];

interface Props {
  message: Message;
  author: string;
  meId: string;
  role: Role | undefined;
  onReact(emoji: string, has: boolean): void;
  onReply?(): void;
  onEdit(content: string): Promise<unknown>;
  onDelete(): void;
  onRetry(): void;
  onDismiss(): void;
}

export function MessageItem({ message: m, author, meId, role, onReact, onReply, onEdit, onDelete, onRetry, onDismiss }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [picker, setPicker] = useState(false);
  const [error, setError] = useState("");
  const own = m.author_id === meId;
  const canDelete = own || role === "OWNER" || role === "ADMIN";
  const settled = !m.pending && !m.failed;

  async function saveEdit() {
    const v = (editing ?? "").trim();
    if (!v || v === m.content) return setEditing(null);
    try { await onEdit(v); setEditing(null); } catch (e) { setError(e instanceof Error ? e.message : "Edit failed"); }
  }

  return (
    <article className={`msg${m.pending ? " pending" : ""}${m.failed ? " failed" : ""}`} aria-label={`Message from ${author}`}>
      <header>
        <b>{author}</b>
        <time dateTime={m.created_at}>{new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
        {m.updated_at && !m.deleted_at && <span className="muted">(edited)</span>}
      </header>

      {m.deleted_at ? <p className="deleted">This message was deleted</p>
        : editing !== null ? (
          <div className="edit">
            <textarea aria-label="Edit message" value={editing} autoFocus onChange={(e) => setEditing(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); saveEdit(); } if (e.key === "Escape") setEditing(null); }} />
            <button className="primary" onClick={saveEdit}>Save</button> <button onClick={() => setEditing(null)}>Cancel</button>
            {error && <span role="alert" className="error"> {error}</span>}
          </div>
        ) : <p className="body">{m.content}</p>}

      {m.failed && <p className="error" role="alert">Failed to send. <button className="link" onClick={onRetry}>Retry</button> · <button className="link" onClick={onDismiss}>Dismiss</button></p>}

      {!m.deleted_at && settled && (
        <div className="meta">
          {Object.entries(m.reactions).map(([emoji, n]) => {
            const has = m.mine?.includes(emoji) ?? false;
            return <button key={emoji} className={has ? "chip on" : "chip"} aria-pressed={has} onClick={() => onReact(emoji, has)}>{emoji} {n}</button>;
          })}
          {onReply && m.reply_count > 0 && <button className="link" onClick={onReply}>{m.reply_count} {m.reply_count === 1 ? "reply" : "replies"} →</button>}
        </div>
      )}

      {!m.deleted_at && settled && editing === null && (
        <div className="actions" role="toolbar" aria-label="Message actions">
          <button aria-label="Add reaction" onClick={() => setPicker((p) => !p)}>😊</button>
          {onReply && <button aria-label="Reply in thread" onClick={onReply}>💬</button>}
          {own && <button aria-label="Edit message" onClick={() => { setEditing(m.content); setError(""); }}>✏️</button>}
          {canDelete && <button aria-label="Delete message" onClick={() => confirm("Delete this message?") && onDelete()}>🗑</button>}
          {picker && <div className="picker">{EMOJI.map((e) => <button key={e} onClick={() => { setPicker(false); onReact(e, m.mine?.includes(e) ?? false); }}>{e}</button>)}</div>}
        </div>
      )}
    </article>
  );
}
