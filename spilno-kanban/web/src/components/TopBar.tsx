import { useEffect, useState } from 'react';

import { getIdentity, initials, INKS, saveIdentity } from '../identity';
import { navigate } from '../router';
import type { BoardSession, Snapshot } from '../session';

export function TopBar({ session, snap }: { session: BoardSession; snap: Snapshot }) {
  return (
    <header className="topbar">
      <a
        className="brand"
        href="/"
        onClick={(e) => {
          e.preventDefault();
          navigate('/');
        }}
      >
        spilno
      </a>
      <BoardTitle title={snap.view.title} onRename={(t) => session.board.setTitle(t)} />
      <div className="topbar-tools">
        <div className="undo-group" role="group" aria-label="Скасування">
          <button
            className="icon-btn"
            onClick={() => session.undo.undo()}
            disabled={!snap.canUndo}
            title="Скасувати мою останню дію (Ctrl+Z)"
            aria-label="Скасувати"
          >
            ↶
          </button>
          <button
            className="icon-btn"
            onClick={() => session.undo.redo()}
            disabled={!snap.canRedo}
            title="Повернути (Ctrl+Shift+Z)"
            aria-label="Повернути"
          >
            ↷
          </button>
        </div>
        <Presence snap={snap} />
        <ConnectionBadge snap={snap} />
        <ShareButton />
      </div>
      <Me session={session} />
    </header>
  );
}

function BoardTitle({ title, onRename }: { title: string; onRename: (t: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <h1 className="board-title">
        <button className="title-btn" onClick={() => setDraft(title)} title="Перейменувати дошку">
          {title}
        </button>
      </h1>
    );
  }
  const commit = () => {
    if (draft.trim() && draft.trim() !== title) onRename(draft);
    setDraft(null);
  };
  return (
    <input
      className="board-title-input"
      autoFocus
      value={draft}
      maxLength={200}
      aria-label="Назва дошки"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit();
        if (e.key === 'Escape') setDraft(null);
      }}
    />
  );
}

function Presence({ snap }: { snap: Snapshot }) {
  const shown = snap.peers.slice(0, 5);
  const extra = snap.peers.length - shown.length;
  const label =
    snap.peers.length === 0
      ? 'Поки що тут лише ви'
      : `Зараз на дошці: ${snap.peers.map((p) => p.name).join(', ')}`;
  return (
    <div className="presence" aria-label={label} title={label}>
      {shown.map((p) => (
        <span key={p.clientId} className="avatar" style={{ '--ink': p.color } as React.CSSProperties}>
          {initials(p.name)}
        </span>
      ))}
      {extra > 0 && <span className="avatar more">+{extra}</span>}
    </div>
  );
}

function ConnectionBadge({ snap }: { snap: Snapshot }) {
  const state =
    snap.connection === 'online'
      ? snap.synced
        ? { cls: 'online', text: 'Збережено', hint: 'Усі зміни на сервері й у всіх учасників' }
        : { cls: 'syncing', text: 'Синхронізую…', hint: 'Обмінююсь змінами із сервером' }
      : snap.connection === 'offline'
        ? { cls: 'offline', text: 'Офлайн', hint: 'Зміни зберігаються на цьому пристрої й надішлються, щойно з’явиться мережа' }
        : { cls: 'syncing', text: 'Підключаюсь…', hint: 'Зміни зберігаються на цьому пристрої' };
  return (
    <span className={`conn ${state.cls}`} title={state.hint} role="status" data-testid="connection">
      <span className="dot" aria-hidden />
      {state.text}
    </span>
  );
}

function ShareButton() {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <button
      className="btn"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(location.href);
          setCopied(true);
        } catch {
          prompt('Скопіюйте посилання на дошку:', location.href);
        }
      }}
      title="Хто має посилання, той може редагувати"
    >
      {copied ? 'Скопійовано ✓' : 'Поділитися'}
    </button>
  );
}

function Me({ session }: { session: BoardSession }) {
  const [me, setMe] = useState(getIdentity);
  const [open, setOpen] = useState(false);
  const update = (patch: Partial<typeof me>) => {
    const next = { ...me, ...patch };
    setMe(next);
    saveIdentity(next);
    session.setPresence({ user: next });
  };
  return (
    <div className="me">
      <button
        className="avatar me-btn"
        style={{ '--ink': me.color } as React.CSSProperties}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label={`Ви: ${me.name}. Змінити ім’я чи колір`}
      >
        {initials(me.name)}
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Як вас бачать інші">
          <label>
            Ваше ім’я для інших
            <input
              autoFocus
              defaultValue={me.name}
              maxLength={40}
              onBlur={(e) => e.target.value.trim() && update({ name: e.target.value.trim() })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  (e.target as HTMLInputElement).blur();
                  setOpen(false);
                }
                if (e.key === 'Escape') setOpen(false);
              }}
            />
          </label>
          <div className="inks" role="radiogroup" aria-label="Колір">
            {INKS.map((c) => (
              <button
                key={c}
                role="radio"
                aria-checked={c === me.color}
                aria-label={c}
                className="ink-swatch"
                style={{ background: c }}
                onClick={() => update({ color: c })}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
