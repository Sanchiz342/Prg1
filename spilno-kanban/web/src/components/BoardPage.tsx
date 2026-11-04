import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core';

import type { CardView } from '../board/model';
import { useBoardSession, useSnapshot } from '../session';
import { TopBar } from './TopBar';
import { Column, ORPHANS } from './Column';
import { CardFace } from './CardItem';
import { CardDrawer } from './CardDrawer';
import { boardKeyboardCoordinates } from './keyboard';

type Layout = Record<string, string[]>;

export const dropId = (col: string) => `drop:${col}`;

export function BoardPage({ id, isNew }: { id: string; isNew: boolean }) {
  const session = useBoardSession(id, isNew);
  const snap = useSnapshot(session);
  const [openCard, setOpenCard] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ id: string; layout: Layout } | null>(null);
  // A pointer drag ends with a click on the card; that click must not open it.
  const lastDragEnd = useRef(0);
  const open = useCallback((cardId: string, via: 'pointer' | 'keyboard') => {
    // Only a pointer drag produces a stray click on drop; Enter after a keyboard move is a real request.
    if (via === 'keyboard' || Date.now() - lastDragEnd.current > 250) setOpenCard(cardId);
  }, []);
  const closeCard = useCallback(() => setOpenCard(null), []);

  // "?new=1" only means "initialise this fresh board once"; drop it so a reload never re-initialises.
  useEffect(() => {
    if (isNew) history.replaceState(null, '', `/b/${id}`);
  }, [id, isNew]);

  useEffect(() => {
    document.title = snap ? `${snap.view.title} — Spilno` : 'Spilno';
  }, [snap?.view.title]);

  useEffect(() => {
    session?.setPresence({ editing: openCard });
  }, [session, openCard]);

  // Undo / redo for my own edits, unless the user is typing in a field (fields have their own undo).
  useEffect(() => {
    if (!session) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest('input, textarea, [contenteditable="true"]')) return;
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === 'z' && !e.shiftKey) {
        e.preventDefault();
        session.undo.undo();
      } else if ((k === 'z' && e.shiftKey) || k === 'y') {
        e.preventDefault();
        session.undo.redo();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [session]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: boardKeyboardCoordinates,
      // Enter opens a card; Space picks it up for moving.
      keyboardCodes: { start: ['Space'], cancel: ['Escape'], end: ['Space', 'Enter'] },
    }),
  );

  const cardsById = useMemo(() => {
    const m = new Map<string, CardView>();
    if (snap) for (const c of [...snap.view.columns.flatMap((x) => x.cards), ...snap.view.orphans]) m.set(c.id, c);
    return m;
  }, [snap]);

  const baseLayout = useCallback((): Layout => {
    const l: Layout = {};
    if (!snap) return l;
    for (const c of snap.view.columns) l[c.id] = c.cards.map((x) => x.id);
    l[ORPHANS] = snap.view.orphans.map((x) => x.id);
    return l;
  }, [snap]);

  if (!session || !snap) return <div className="loading">Відкриваю дошку…</div>;
  const { view, peers } = snap;
  const board = session.board;
  const layout = drag?.layout ?? baseLayout();

  const containerOf = (itemId: string, l: Layout): string | null => {
    if (itemId.startsWith('drop:')) return itemId.slice(5);
    for (const [col, ids] of Object.entries(l)) if (ids.includes(itemId)) return col;
    return null;
  };

  const onDragStart = (e: DragStartEvent) => {
    setDrag({ id: String(e.active.id), layout: baseLayout() });
    session.setPresence({ dragging: String(e.active.id) });
  };

  const onDragOver = ({ active, over }: DragOverEvent) => {
    if (!over || !drag) return;
    const from = containerOf(String(active.id), drag.layout);
    const to = containerOf(String(over.id), drag.layout);
    if (!from || !to || from === to || to === ORPHANS) return;
    const next: Layout = { ...drag.layout, [from]: drag.layout[from]!.filter((x) => x !== active.id) };
    const target = [...(drag.layout[to] ?? [])];
    const overIndex = target.indexOf(String(over.id));
    target.splice(overIndex === -1 ? target.length : overIndex, 0, String(active.id));
    next[to] = target;
    setDrag({ ...drag, layout: next });
  };

  const finish = () => {
    lastDragEnd.current = Date.now();
    setDrag(null);
    session.setPresence({ dragging: null });
  };

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    const cardId = String(active.id);
    if (!over || !drag) return finish();
    const col = containerOf(String(over.id), drag.layout) ?? containerOf(cardId, drag.layout);
    if (!col || col === ORPHANS) return finish();
    // Final order in the preview: the card takes the slot of the card it was dropped on (dnd-kit's arrayMove).
    const ids = [...(drag.layout[col] ?? [])];
    if (!ids.includes(cardId)) ids.push(cardId);
    const oldIdx = ids.indexOf(cardId);
    const overIdx = ids.indexOf(String(over.id));
    if (overIdx !== -1 && overIdx !== oldIdx) {
      ids.splice(oldIdx, 1);
      ids.splice(overIdx, 0, cardId);
    }
    // Translate "right after card X" into an index in the CURRENT column: others may have added, moved or deleted
    // cards while we were dragging, so a raw preview index could be off.
    const prev = ids[ids.indexOf(cardId) - 1];
    const current = (view.columns.find((c) => c.id === col)?.cards ?? []).filter((c) => c.id !== cardId);
    let index = 0;
    if (prev !== undefined) {
      const at = current.findIndex((c) => c.id === prev);
      index = at === -1 ? current.length : at + 1;
    }
    const card = cardsById.get(cardId);
    const unchanged =
      card?.pos.col === col &&
      (view.columns.find((c) => c.id === col)?.cards.map((c) => c.id).indexOf(cardId) ?? -1) === index;
    if (!unchanged) board.moveCard(cardId, col, index);
    finish();
  };

  const title = (cid: string) => cardsById.get(cid)?.title ?? 'картку';
  const colTitle = (cid: string | undefined) =>
    view.columns.find((c) => c.id === (cid?.startsWith('drop:') ? cid.slice(5) : containerOf(cid ?? '', layout)))
      ?.title ?? '';
  const announcements: Announcements = {
    onDragStart: ({ active }) => `Взято «${title(String(active.id))}». Стрілками — перемістити, пробіл — покласти.`,
    onDragOver: ({ active, over }) =>
      over ? `«${title(String(active.id))}» над колонкою «${colTitle(String(over.id))}».` : 'Поза колонками.',
    onDragEnd: ({ active, over }) =>
      over ? `«${title(String(active.id))}» покладено в «${colTitle(String(over.id))}».` : 'Переміщення скасовано.',
    onDragCancel: ({ active }) => `Переміщення «${title(String(active.id))}» скасовано.`,
  };

  const opened = openCard ? cardsById.get(openCard) ?? null : null;
  const notYetLoaded = view.columns.length === 0 && !snap.synced && !(snap.loaded && isNew);

  return (
    <div className="board-page">
      <TopBar session={session} snap={snap} />
      {notYetLoaded ? (
        <div className="loading">
          {snap.connection === 'offline'
            ? 'Ця дошка ще не завантажувалась на цьому пристрої. Потрібне з’єднання.'
            : 'Завантажую дошку…'}
        </div>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={onDragStart}
          onDragOver={onDragOver}
          onDragEnd={onDragEnd}
          onDragCancel={finish}
          accessibility={{
            announcements,
            screenReaderInstructions: {
              draggable: 'Щоб перемістити картку, натисніть пробіл, рухайте стрілками й натисніть пробіл ще раз.',
            },
          }}
        >
          <main className="columns" aria-label="Колонки дошки">
            {view.orphans.length > 0 && (
              <Column
                id={ORPHANS}
                title="Без колонки"
                note="Їхню колонку видалили, поки їх переміщували. Перетягніть у потрібну колонку."
                cardIds={layout[ORPHANS] ?? []}
                cardsById={cardsById}
                peers={peers}
                board={board}
                onOpen={open}
              />
            )}
            {view.columns.map((col, i) => (
              <Column
                key={col.id}
                id={col.id}
                title={col.title}
                cardIds={layout[col.id] ?? []}
                cardsById={cardsById}
                peers={peers}
                board={board}
                onOpen={open}
                position={{ index: i, count: view.columns.length }}
              />
            ))}
            <AddColumn onAdd={(t) => board.addColumn(t)} disabled={view.columns.length >= 30} />
          </main>
          <DragOverlay dropAnimation={{ duration: 160, easing: 'ease-out' }}>
            {drag && cardsById.get(drag.id) ? <CardFace card={cardsById.get(drag.id)!} peers={[]} lifted /> : null}
          </DragOverlay>
        </DndContext>
      )}
      {opened && (
        <CardDrawer
          key={opened.id}
          card={opened}
          board={board}
          peers={peers.filter((p) => p.editing === opened.id)}
          columns={view.columns}
          onClose={closeCard}
        />
      )}
      {openCard && !opened && <Gone onClose={closeCard} />}
    </div>
  );
}

function AddColumn({ onAdd, disabled }: { onAdd: (title: string) => void; disabled: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  if (!editing) {
    return (
      <button className="add-column" onClick={() => setEditing(true)} disabled={disabled}>
        + Колонка
      </button>
    );
  }
  const submit = () => {
    if (value.trim()) onAdd(value);
    setValue('');
    setEditing(false);
  };
  return (
    <form
      className="add-column editing"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <input
        autoFocus
        value={value}
        maxLength={200}
        placeholder="Назва колонки"
        aria-label="Назва нової колонки"
        onChange={(e) => setValue(e.target.value)}
        onBlur={submit}
        onKeyDown={(e) => e.key === 'Escape' && (setValue(''), setEditing(false))}
      />
    </form>
  );
}

function Gone({ onClose }: { onClose: () => void }) {
  return (
    <div className="toast" role="status">
      Цю картку щойно видалив хтось інший.
      <button className="btn" onClick={onClose}>
        Зрозуміло
      </button>
    </div>
  );
}
