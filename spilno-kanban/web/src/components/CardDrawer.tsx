import { useEffect, useRef, useState } from 'react';
import type * as Y from 'yjs';

import { LABELS, LOCAL, type Board, type CardView, type ColumnView, type Label } from '../board/model';
import { diff, transformPosition, type Delta } from '../board/textBinding';
import { initials } from '../identity';
import type { Peer } from '../session';
import { LABEL_NAMES } from './CardItem';

interface Props {
  card: CardView;
  board: Board;
  peers: Peer[];
  columns: ColumnView[];
  onClose: () => void;
}

export function CardDrawer({ card, board, peers, columns, onClose }: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // Runs once per opened card. It must NOT depend on props that change on every board update: re-running it
  // would steal focus from the description while someone else types (a bug the e2e test caught).
  useEffect(() => {
    const returnFocus = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && closeRef.current();
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, []);

  const column = columns.find((c) => c.id === card.pos.col);
  const toggle = (l: Label) =>
    board.updateCard(card.id, { labels: card.labels.includes(l) ? card.labels.filter((x) => x !== l) : [...card.labels, l] });

  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer" role="dialog" aria-modal="true" aria-label={`Картка «${card.title}»`} ref={panel} tabIndex={-1}>
        <div className="drawer-head">
          <span className="drawer-col">{column ? column.title : 'Без колонки'}</span>
          {peers.length > 0 && (
            <span className="drawer-peers">
              {peers.map((p) => (
                <span key={p.clientId} className="avatar small" style={{ '--ink': p.color } as React.CSSProperties} title={`${p.name} теж тут`}>
                  {initials(p.name)}
                </span>
              ))}
              <span className="muted">теж редагує</span>
            </span>
          )}
          <button className="icon-btn" onClick={onClose} aria-label="Закрити картку">
            ×
          </button>
        </div>

        <Field
          className="drawer-title"
          value={card.title}
          label="Назва"
          onCommit={(v) => board.updateCard(card.id, { title: v })}
        />

        <fieldset className="labels-picker">
          <legend>Мітки</legend>
          {LABELS.map((l) => (
            <button
              key={l}
              type="button"
              className={`label-chip label-${l}${card.labels.includes(l) ? ' on' : ''}`}
              aria-pressed={card.labels.includes(l)}
              onClick={() => toggle(l)}
            >
              {LABEL_NAMES[l]}
            </button>
          ))}
        </fieldset>

        <Field
          value={card.assignee}
          label="Хто робить"
          placeholder="Ім’я"
          onCommit={(v) => board.updateCard(card.id, { assignee: v })}
        />

        <label className="field">
          <span>Опис</span>
          {card.description ? (
            <DescriptionEditor text={card.description} />
          ) : (
            <p className="muted">Опис цієї картки пошкоджений і недоступний для редагування.</p>
          )}
        </label>

        <footer className="drawer-foot">
          <span className="muted">
            {card.createdAt ? `Створено ${new Date(card.createdAt).toLocaleString('uk-UA')}` : ''}
          </span>
          <button
            className="btn danger"
            onClick={() => {
              if (confirm(`Видалити картку «${card.title}»?`)) {
                board.deleteCard(card.id);
                onClose();
              }
            }}
          >
            Видалити картку
          </button>
        </footer>
      </div>
    </div>
  );
}

/** Single-line field that commits on blur / Enter (one update per edit, not per keystroke). */
function Field(props: { value: string; label: string; placeholder?: string; className?: string; onCommit: (v: string) => void }) {
  const [v, setV] = useState(props.value);
  const input = useRef<HTMLInputElement>(null);
  // Follow other people's changes, but never overwrite what I am typing right now.
  useEffect(() => {
    if (document.activeElement !== input.current) setV(props.value);
  }, [props.value]);
  const commit = () => {
    if (v !== props.value) props.onCommit(v);
  };
  // Closing the drawer with Escape unmounts the input without a blur event: commit the pending edit then too.
  const commitRef = useRef(commit);
  commitRef.current = commit;
  useEffect(() => () => {
    commitRef.current();
  }, []);
  return (
    <label className={`field ${props.className ?? ''}`}>
      <span>{props.label}</span>
      <input
        ref={input}
        value={v}
        maxLength={200}
        placeholder={props.placeholder}
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
    </label>
  );
}

/**
 * <textarea> bound to a shared Y.Text.
 *
 * The textarea is deliberately UNcontrolled: remote edits are written into the DOM synchronously inside the Yjs
 * observer. That guarantees the textarea always equals the Y.Text when the next input event fires, so the diff
 * of a keystroke is computed against exactly what the user saw. (With React state, a remote edit could arrive
 * between "state set" and "re-render"; the next keystroke would then be diffed against a stale value and erase
 * the remote text — the e2e test caught exactly that.)
 */
export function DescriptionEditor({ text }: { text: Y.Text }) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.value = text.toString();
    const onChange = (event: Y.YTextEvent) => {
      if (event.transaction.origin === LOCAL) return; // our own keystroke: the textarea already shows it
      const focused = document.activeElement === el;
      const delta = event.delta as Delta;
      const start = transformPosition(el.selectionStart, delta);
      const end = transformPosition(el.selectionEnd, delta);
      el.value = text.toString();
      if (focused) el.setSelectionRange(start, end);
    };
    text.observe(onChange);
    return () => text.unobserve(onChange);
  }, [text]);

  return (
    <textarea
      ref={ref}
      className="description"
      rows={8}
      placeholder="Деталі, посилання, чекліст…"
      onInput={(e) => {
        const el = e.currentTarget;
        const edit = diff(text.toString(), el.value, el.selectionEnd);
        if (!edit) return;
        text.doc!.transact(() => {
          if (edit.remove) text.delete(edit.index, edit.remove);
          if (edit.insert) text.insert(edit.index, edit.insert);
        }, LOCAL);
      }}
    />
  );
}
