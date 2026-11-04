import { describe, expect, it } from 'vitest';
import * as Y from 'yjs';

import { Board, createUndoManager, LOCAL } from '../src/board/model';

/** View with Y.Text replaced by its content, so views from different docs can be compared. */
const plain = (b: Board) =>
  JSON.parse(JSON.stringify(b.view(), (k, v) => (k === 'description' && v ? v.toString() : v)));

const titles = (b: Board) => b.view().columns.map((c) => [c.title, c.cards.map((x) => x.title)]);

function sync(a: Y.Doc, b: Y.Doc) {
  Y.applyUpdate(b, Y.encodeStateAsUpdate(a, Y.encodeStateVector(b)));
  Y.applyUpdate(a, Y.encodeStateAsUpdate(b, Y.encodeStateVector(a)));
}

describe('board model', () => {
  it('initialises default columns once, even when two clients do it concurrently', () => {
    const a = new Board(new Y.Doc());
    const b = new Board(new Y.Doc());
    a.init('Спринт');
    b.init('Інша назва');
    sync(a.doc, b.doc);
    expect(a.view().columns.map((c) => c.title)).toEqual(['Зробити', 'В роботі', 'Готово']);
    expect(plain(b)).toEqual(plain(a));
  });

  it('adds, moves, edits and deletes cards', () => {
    const b = new Board(new Y.Doc());
    b.init('Board');
    const [todo, doing] = b.view().columns.map((c) => c.id) as [string, string];
    const c1 = b.addCard(todo, 'one')!;
    const c2 = b.addCard(todo, 'two')!;
    const c3 = b.addCard(todo, 'zero', 0)!;
    expect(titles(b)[0]).toEqual(['Зробити', ['zero', 'one', 'two']]);

    b.moveCard(c2, todo, 0);
    expect(titles(b)[0]![1]).toEqual(['two', 'zero', 'one']);
    b.moveCard(c1, doing, 0);
    expect(titles(b)[1]).toEqual(['В роботі', ['one']]);

    b.updateCard(c3, { title: '  renamed  ', assignee: 'Оля', labels: ['green', 'red', 'bogus' as never] });
    const card = b.view().columns[0]!.cards.find((c) => c.id === c3)!;
    expect(card).toMatchObject({ title: 'renamed', assignee: 'Оля', labels: ['red', 'green'] });
    b.updateCard(c3, { title: '   ' }); // blank titles are ignored
    expect(b.view().columns[0]!.cards.find((c) => c.id === c3)!.title).toBe('renamed');

    b.deleteCard(c2);
    expect(titles(b)[0]![1]).toEqual(['renamed']);
  });

  it('moves columns and deletes a column with its cards', () => {
    const b = new Board(new Y.Doc());
    b.init('Board');
    const [todo, , done] = b.view().columns.map((c) => c.id) as [string, string, string];
    b.addCard(todo, 'a');
    b.moveColumn(done, 0);
    expect(b.view().columns.map((c) => c.title)).toEqual(['Готово', 'Зробити', 'В роботі']);
    b.deleteColumn(todo);
    expect(b.view().columns.map((c) => c.title)).toEqual(['Готово', 'В роботі']);
    expect(b.cards.size).toBe(0);
  });

  it('two people moving the same card at once leave it in exactly one place', () => {
    const a = new Board(new Y.Doc());
    a.init('B');
    const [todo, doing, done] = a.view().columns.map((c) => c.id) as [string, string, string];
    const id = a.addCard(todo, 'contested')!;
    const b = new Board(new Y.Doc());
    sync(a.doc, b.doc);
    a.moveCard(id, doing, 0);
    b.moveCard(id, done, 0);
    sync(a.doc, b.doc);
    const where = a.view().columns.filter((c) => c.cards.some((x) => x.id === id)).map((c) => c.id);
    expect(where).toHaveLength(1);
    expect(plain(b)).toEqual(plain(a));
  });

  it('a card moved into a column deleted at the same time becomes an orphan instead of vanishing', () => {
    const a = new Board(new Y.Doc());
    a.init('B');
    const [todo, doing] = a.view().columns.map((c) => c.id) as [string, string];
    const id = a.addCard(todo, 'refugee')!;
    const b = new Board(new Y.Doc());
    sync(a.doc, b.doc);
    a.deleteColumn(doing);
    b.moveCard(id, doing, 0);
    sync(a.doc, b.doc);
    expect(a.view().orphans.map((c) => c.title)).toEqual(['refugee']);
    a.moveCard(id, todo, 0); // can be rescued
    expect(a.view().orphans).toHaveLength(0);
  });

  it('undo reverts only my own edits', () => {
    const mine = new Board(new Y.Doc());
    mine.init('B');
    const todo = mine.view().columns[0]!.id;
    const theirs = new Board(new Y.Doc());
    sync(mine.doc, theirs.doc);
    const undo = createUndoManager(mine);

    mine.addCard(todo, 'my card');
    theirs.addCard(todo, 'their card');
    // Remote changes arrive with a non-local origin.
    Y.applyUpdate(mine.doc, Y.encodeStateAsUpdate(theirs.doc, Y.encodeStateVector(mine.doc)), 'remote');
    expect(mine.view().columns[0]!.cards.map((c) => c.title).sort()).toEqual(['my card', 'their card']);

    undo.undo();
    expect(mine.view().columns[0]!.cards.map((c) => c.title)).toEqual(['their card']);
    undo.redo();
    expect(mine.view().columns[0]!.cards).toHaveLength(2);
  });

  it('description is collaborative text and undoable', () => {
    const b = new Board(new Y.Doc());
    b.init('B');
    const id = b.addCard(b.view().columns[0]!.id, 'x')!;
    const undo = createUndoManager(b);
    const text = b.view().columns[0]!.cards[0]!.description!;
    b.doc.transact(() => text.insert(0, 'Hello'), LOCAL);
    expect(text.toString()).toBe('Hello');
    undo.undo();
    expect(text.toString()).toBe('');
    expect(id).toMatch(/^card-/);
  });

  it('reads malformed data defensively', () => {
    const doc = new Y.Doc();
    const b = new Board(doc);
    b.init('B');
    const todo = b.view().columns[0]!.id;
    doc.transact(() => {
      b.columns.set('col-bad', 'not a map' as never);
      const noKey = new Y.Map<unknown>();
      noKey.set('title', 'no key');
      b.columns.set('col-nokey', noKey);
      const badPos = new Y.Map<unknown>();
      badPos.set('pos', { col: todo, key: '' });
      b.cards.set('card-badpos', badPos);
      const weird = new Y.Map<unknown>();
      weird.set('pos', { col: todo, key: 'V' });
      weird.set('title', 42);
      weird.set('labels', 'red');
      weird.set('description', 'plain string');
      b.cards.set('card-weird', weird);
    });
    const v = b.view();
    expect(v.columns.map((c) => c.id)).toEqual(['col-todo', 'col-doing', 'col-done']);
    expect(v.columns[0]!.cards).toHaveLength(1);
    expect(v.columns[0]!.cards[0]).toMatchObject({ title: 'Без назви', labels: [], description: null });
  });

  it('rejects operations on unknown columns and caps columns', () => {
    const b = new Board(new Y.Doc());
    b.init('B');
    expect(b.addCard('col-missing', 'x')).toBeNull();
    for (let i = 0; i < 40; i++) b.addColumn(`c${i}`);
    expect(b.view().columns.length).toBe(30);
  });
});
