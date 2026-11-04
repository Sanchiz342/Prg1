import { useState } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable';

import type { Board, CardView } from '../board/model';
import type { Peer } from '../session';
import { SortableCard } from './CardItem';

export const ORPHANS = '__orphans__';

interface Props {
  id: string;
  title: string;
  note?: string;
  cardIds: string[];
  cardsById: Map<string, CardView>;
  peers: Peer[];
  board: Board;
  onOpen: (id: string, via: 'pointer' | 'keyboard') => void;
  position?: { index: number; count: number };
}

export function Column({ id, title, note, cardIds, cardsById, peers, board, onOpen, position }: Props) {
  const isOrphans = id === ORPHANS;
  const { setNodeRef, isOver } = useDroppable({ id: `drop:${id}`, disabled: isOrphans });
  const cards = cardIds.map((cid) => cardsById.get(cid)).filter((c): c is CardView => !!c);
  return (
    <section className={`column${isOrphans ? ' orphans' : ''}${isOver ? ' over' : ''}`} aria-label={`Колонка «${title}»`}>
      <ColumnHeader id={id} title={title} count={cards.length} board={board} position={position} editable={!isOrphans} />
      {note && <p className="column-note">{note}</p>}
      <SortableContext id={id} items={cards.map((c) => c.id)} strategy={verticalListSortingStrategy}>
        <ol className="cards" ref={setNodeRef}>
          {cards.map((card) => (
            <SortableCard
              key={card.id}
              card={card}
              peers={peers.filter((p) => p.editing === card.id || p.dragging === card.id)}
              onOpen={(via) => onOpen(card.id, via)}
            />
          ))}
          {cards.length === 0 && !isOrphans && <li className="empty-slot">Перетягніть картку сюди</li>}
        </ol>
      </SortableContext>
      {!isOrphans && <AddCard onAdd={(t) => board.addCard(id, t)} />}
    </section>
  );
}

function ColumnHeader({
  id,
  title,
  count,
  board,
  position,
  editable,
}: {
  id: string;
  title: string;
  count: number;
  board: Board;
  position?: { index: number; count: number };
  editable: boolean;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const commit = () => {
    if (draft !== null && draft.trim() && draft !== title) board.renameColumn(id, draft);
    setDraft(null);
  };
  return (
    <header className="column-head">
      {draft === null ? (
        <h2>
          {editable ? (
            <button className="title-btn" onClick={() => setDraft(title)} title="Перейменувати колонку">
              {title}
            </button>
          ) : (
            title
          )}
        </h2>
      ) : (
        <input
          autoFocus
          className="column-title-input"
          value={draft}
          maxLength={200}
          aria-label="Назва колонки"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit();
            if (e.key === 'Escape') setDraft(null);
          }}
        />
      )}
      <span className="count" aria-label={`${count} карток`}>
        {count}
      </span>
      {editable && position && (
        <div className="column-menu">
          <button className="icon-btn" aria-label={`Дії з колонкою «${title}»`} aria-expanded={menu} onClick={() => setMenu(!menu)}>
            ⋯
          </button>
          {menu && (
            <div className="popover menu" role="menu" onMouseLeave={() => setMenu(false)}>
              <button
                role="menuitem"
                disabled={position.index === 0}
                onClick={() => {
                  board.moveColumn(id, position.index - 1);
                  setMenu(false);
                }}
              >
                ← Посунути ліворуч
              </button>
              <button
                role="menuitem"
                disabled={position.index === position.count - 1}
                onClick={() => {
                  board.moveColumn(id, position.index + 1);
                  setMenu(false);
                }}
              >
                Посунути праворуч →
              </button>
              <button
                role="menuitem"
                className="danger"
                onClick={() => {
                  setMenu(false);
                  if (count === 0 || confirm(`Видалити колонку «${title}» разом з ${count} картками?`)) board.deleteColumn(id);
                }}
              >
                Видалити колонку
              </button>
            </div>
          )}
        </div>
      )}
    </header>
  );
}

function AddCard({ onAdd }: { onAdd: (title: string) => void }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  if (!open) {
    return (
      <button className="add-card" onClick={() => setOpen(true)}>
        + Картка
      </button>
    );
  }
  return (
    <form
      className="add-card-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onAdd(value);
        setValue(''); // stay open for quick entry of several cards
      }}
    >
      <textarea
        autoFocus
        rows={2}
        value={value}
        maxLength={200}
        placeholder="Що треба зробити?"
        aria-label="Назва нової картки"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            (e.currentTarget.form as HTMLFormElement).requestSubmit();
          }
          if (e.key === 'Escape') {
            setValue('');
            setOpen(false);
          }
        }}
        onBlur={() => !value.trim() && setOpen(false)}
      />
      <div className="row">
        <button className="btn primary" type="submit">
          Додати
        </button>
        <button className="btn ghost" type="button" onClick={() => setOpen(false)}>
          Скасувати
        </button>
      </div>
    </form>
  );
}
