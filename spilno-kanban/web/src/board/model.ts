// The board document on top of Yjs.
//
//   meta    : Y.Map   { title }
//   columns : Y.Map   columnId -> Y.Map { title, key }
//   cards   : Y.Map   cardId   -> Y.Map { title, description: Y.Text, pos: { col, key }, labels, assignee, createdAt }
//
// A card's position is ONE value, `pos = { col, key }`. Moving a card writes only that value, so two people moving
// the same card at once resolve last-writer-wins to a single place. (Moving by deleting and re-inserting into a
// Y.Array would leave the card in BOTH places after such a race.)
//
// Anyone with the board link can write anything into the document, so `view()` reads defensively: malformed
// entries are skipped or coerced instead of crashing the UI.
import * as Y from 'yjs';

import { compareByKey, isValidKey, keyAt } from './order';
import { randomId } from './ids';

/** Transaction origin for this user's own edits; the undo manager tracks only these. */
export const LOCAL = Symbol('local');

export const LABELS = ['red', 'orange', 'yellow', 'green', 'blue', 'purple'] as const;
export type Label = (typeof LABELS)[number];

export const MAX_TITLE = 200;
export const MAX_COLUMNS = 30;

export interface Pos {
  col: string;
  key: string;
}

export interface CardView {
  id: string;
  title: string;
  assignee: string;
  labels: Label[];
  pos: Pos;
  description: Y.Text | null;
  createdAt: number;
}

export interface ColumnView {
  id: string;
  title: string;
  key: string;
  cards: CardView[];
}

export interface BoardView {
  title: string;
  columns: ColumnView[];
  /** Cards whose column no longer exists (e.g. moved into a column someone deleted at the same time). */
  orphans: CardView[];
}

const DEFAULT_COLUMNS: [string, string][] = [
  ['col-todo', 'Зробити'],
  ['col-doing', 'В роботі'],
  ['col-done', 'Готово'],
];

const clean = (s: unknown, max = MAX_TITLE) => (typeof s === 'string' ? s.trim().slice(0, max) : '');

function isPos(p: unknown): p is Pos {
  return typeof p === 'object' && p !== null && typeof (p as Pos).col === 'string' && isValidKey((p as Pos).key);
}

export class Board {
  readonly meta: Y.Map<unknown>;
  readonly columns: Y.Map<Y.Map<unknown>>;
  readonly cards: Y.Map<Y.Map<unknown>>;

  constructor(
    readonly doc: Y.Doc,
    private rand: () => number = Math.random,
  ) {
    this.meta = doc.getMap('meta');
    this.columns = doc.getMap('columns');
    this.cards = doc.getMap('cards');
  }

  private tx(fn: () => void): void {
    this.doc.transact(fn, LOCAL);
  }

  /**
   * Fills an empty board with default columns. Uses fixed column ids, so two clients initialising the same new
   * board concurrently write the same keys instead of producing duplicate columns.
   */
  init(title: string): void {
    this.tx(() => {
      if (!this.meta.has('title')) this.meta.set('title', clean(title) || 'Нова дошка');
      if (this.columns.size === 0) {
        const keys = ['V', 'e', 'n'];
        DEFAULT_COLUMNS.forEach(([id, name], i) => this.columns.set(id, colMap(name, keys[i]!)));
      }
    });
  }

  setTitle(title: string): void {
    const t = clean(title);
    if (t) this.tx(() => this.meta.set('title', t));
  }

  // ---- columns ----------------------------------------------------------------------------------------------

  private sortedColumns(): { id: string; key: string; title: string }[] {
    const out: { id: string; key: string; title: string }[] = [];
    this.columns.forEach((m, id) => {
      if (!(m instanceof Y.Map)) return;
      const key = m.get('key');
      if (isValidKey(key)) out.push({ id, key, title: clean(m.get('title')) });
    });
    return out.sort(compareByKey);
  }

  addColumn(title: string, index = Infinity): string | null {
    const cols = this.sortedColumns();
    if (cols.length >= MAX_COLUMNS) return null;
    const id = 'col-' + randomId();
    this.tx(() => this.columns.set(id, colMap(clean(title) || 'Колонка', keyAt(cols, index, this.rand))));
    return id;
  }

  renameColumn(id: string, title: string): void {
    const t = clean(title);
    const col = this.columns.get(id);
    if (t && col instanceof Y.Map) this.tx(() => col.set('title', t));
  }

  /** Moves a column so it ends up at `index` among the other columns. */
  moveColumn(id: string, index: number): void {
    const col = this.columns.get(id);
    if (!(col instanceof Y.Map)) return;
    const others = this.sortedColumns().filter((c) => c.id !== id);
    this.tx(() => col.set('key', keyAt(others, index, this.rand)));
  }

  /** Deletes a column together with its cards. */
  deleteColumn(id: string): void {
    this.tx(() => {
      this.columns.delete(id);
      for (const card of this.cardsIn(id)) this.cards.delete(card.id);
    });
  }

  // ---- cards ------------------------------------------------------------------------------------------------

  private cardsIn(col: string): { id: string; key: string }[] {
    const out: { id: string; key: string }[] = [];
    this.cards.forEach((m, id) => {
      if (!(m instanceof Y.Map)) return;
      const pos = m.get('pos');
      if (isPos(pos) && pos.col === col) out.push({ id, key: pos.key });
    });
    return out.sort(compareByKey);
  }

  addCard(col: string, title: string, index = Infinity): string | null {
    if (!(this.columns.get(col) instanceof Y.Map)) return null;
    const id = 'card-' + randomId();
    const key = keyAt(this.cardsIn(col), index, this.rand);
    this.tx(() => {
      const card = new Y.Map<unknown>();
      card.set('title', clean(title) || 'Без назви');
      card.set('description', new Y.Text());
      card.set('pos', { col, key } satisfies Pos);
      card.set('labels', []);
      card.set('assignee', '');
      card.set('createdAt', Date.now());
      this.cards.set(id, card);
    });
    return id;
  }

  updateCard(id: string, patch: { title?: string; assignee?: string; labels?: Label[] }): void {
    const card = this.cards.get(id);
    if (!(card instanceof Y.Map)) return;
    this.tx(() => {
      if (patch.title !== undefined && clean(patch.title)) card.set('title', clean(patch.title));
      if (patch.assignee !== undefined) card.set('assignee', clean(patch.assignee, 80));
      if (patch.labels !== undefined) {
        card.set('labels', LABELS.filter((l) => patch.labels!.includes(l)));
      }
    });
  }

  /** Moves a card to `index` within column `col` (index among the column's other cards). */
  moveCard(id: string, col: string, index: number): void {
    const card = this.cards.get(id);
    if (!(card instanceof Y.Map) || !(this.columns.get(col) instanceof Y.Map)) return;
    const others = this.cardsIn(col).filter((c) => c.id !== id);
    const pos: Pos = { col, key: keyAt(others, index, this.rand) };
    this.tx(() => card.set('pos', pos));
  }

  deleteCard(id: string): void {
    if (this.cards.has(id)) this.tx(() => this.cards.delete(id));
  }

  // ---- reading ----------------------------------------------------------------------------------------------

  view(): BoardView {
    const columns: ColumnView[] = this.sortedColumns().map((c) => ({ ...c, cards: [] }));
    const byId = new Map(columns.map((c) => [c.id, c]));
    const orphans: CardView[] = [];
    this.cards.forEach((m, id) => {
      if (!(m instanceof Y.Map)) return;
      const pos = m.get('pos');
      if (!isPos(pos)) return;
      const labels = m.get('labels');
      const description = m.get('description');
      const createdAt = m.get('createdAt');
      const card: CardView = {
        id,
        title: clean(m.get('title')) || 'Без назви',
        assignee: clean(m.get('assignee'), 80),
        labels: Array.isArray(labels) ? LABELS.filter((l) => labels.includes(l)) : [],
        pos,
        description: description instanceof Y.Text ? description : null,
        createdAt: typeof createdAt === 'number' ? createdAt : 0,
      };
      (byId.get(pos.col)?.cards ?? orphans).push(card);
    });
    const order = (a: CardView, b: CardView) => compareByKey({ key: a.pos.key, id: a.id }, { key: b.pos.key, id: b.id });
    for (const c of columns) c.cards.sort(order);
    orphans.sort(order);
    return { title: clean(this.meta.get('title')) || 'Нова дошка', columns, orphans };
  }
}

function colMap(title: string, key: string): Y.Map<unknown> {
  const m = new Y.Map<unknown>();
  m.set('title', title);
  m.set('key', key);
  return m;
}

/** Undo/redo for this user's own edits only: other people's changes are never undone by our Ctrl+Z. */
export function createUndoManager(board: Board): Y.UndoManager {
  return new Y.UndoManager([board.meta, board.columns, board.cards], {
    trackedOrigins: new Set([LOCAL]),
    captureTimeout: 400,
  });
}
