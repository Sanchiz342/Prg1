import { FormEvent, useState } from "react";
import { Session, request } from "../api";

export default function AskBox({ session, incidentId }: { session: Session; incidentId?: number }) {
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await request<{ answer: string; provider: string }>("/ask", session, {
        method: "POST", body: JSON.stringify({ question: q, incident_id: incidentId ?? null }),
      });
      setAnswer(`${r.answer}  (via ${r.provider})`);
    } catch (err) { setAnswer(err instanceof Error ? err.message : "Request failed"); }
    finally { setBusy(false); }
  };

  return (
    <form className="card ask" onSubmit={submit}>
      <h3>Ask the monitor</h3>
      <div className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={incidentId ? "Why did this happen?" : "What is broken right now?"} />
        <button disabled={busy || !q.trim()}>Ask</button>
      </div>
      {answer && <p className="answer">{answer}</p>}
    </form>
  );
}
