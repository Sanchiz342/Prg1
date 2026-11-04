// Property-based convergence test: several replicas edit concurrently through an unreliable network
// (partial, reordered and duplicated delivery). After everything is delivered, all replicas must show the
// same board, and the board must satisfy its invariants.
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import * as Y from 'yjs';

import { Board, LOCAL, type BoardView } from '../src/board/model';
import { compareByKey } from '../src/board/order';

const REMOTE = 'remote';

type Step =
  | { kind: 'op'; replica: number; op: number; a: number; b: number; c: number; text: string }
  | { kind: 'deliver'; count: number; pick: number; duplicate: boolean };

const step: fc.Arbitrary<Step> = fc.oneof(
  {
    weight: 3,
    arbitrary: fc.record({
      kind: fc.constant('op' as const),
      replica: fc.nat(),
      op: fc.nat({ max: 9 }),
      a: fc.nat(),
      b: fc.nat(),
      c: fc.nat(),
      text: fc.string({ maxLength: 6 }),
    }),
  },
  {
    weight: 1,
    arbitrary: fc.record({
      kind: fc.constant('deliver' as const),
      count: fc.nat({ max: 8 }),
      pick: fc.nat(),
      duplicate: fc.boolean(),
    }),
  },
);

function seeded(seed: number) {
  let s = seed >>> 0 || 1;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32);
}

/** Applies operation `op` on `board`, choosing targets from its current local view. */
function apply(board: Board, s: Extract<Step, { kind: 'op' }>): void {
  const v = board.view();
  const cols = v.columns;
  const cards = [...cols.flatMap((c) => c.cards), ...v.orphans];
  const col = cols.length ? cols[s.a % cols.length]! : null;
  const card = cards.length ? cards[s.b % cards.length]! : null;
  switch (s.op) {
    case 0:
    case 1:
      if (col) board.addCard(col.id, `t${s.text}`, s.c % (col.cards.length + 1));
      break;
    case 2:
    case 3:
      if (card && col) board.moveCard(card.id, col.id, s.c % (col.cards.length + 1));
      break;
    case 4:
      if (card) board.deleteCard(card.id);
      break;
    case 5:
      board.addColumn(`c${s.text}`, s.c % (cols.length + 1));
      break;
    case 6:
      if (col) board.moveColumn(col.id, s.c % cols.length);
      break;
    case 7:
      if (col && s.c % 3 === 0) board.deleteColumn(col.id); // rarer: it removes a lot
      else if (col) board.renameColumn(col.id, `r${s.text}`);
      break;
    case 8:
      if (card) board.updateCard(card.id, { title: `u${s.text}`, labels: s.c % 2 ? ['red'] : ['blue', 'green'] });
      break;
    case 9:
      if (card?.description) {
        const t = card.description;
        board.doc.transact(() => {
          if (s.c % 3 === 0 && t.length > 0) t.delete(s.a % t.length, 1);
          else t.insert(s.a % (t.length + 1), s.text || 'x');
        }, LOCAL);
      }
      break;
  }
}

function invariants(v: BoardView): void {
  const ids = [...v.columns.flatMap((c) => c.cards), ...v.orphans].map((c) => c.id);
  expect(new Set(ids).size, 'every card appears exactly once').toBe(ids.length);
  const colIds = v.columns.map((c) => c.id);
  expect(new Set(colIds).size).toBe(colIds.length);
  for (const col of v.columns) {
    expect(col.cards.every((c) => c.pos.col === col.id)).toBe(true);
    const keyed = col.cards.map((c) => ({ key: c.pos.key, id: c.id }));
    expect([...keyed].sort(compareByKey)).toEqual(keyed);
  }
  for (const o of v.orphans) expect(colIds).not.toContain(o.pos.col);
}

/** Comparable snapshot: Y.Text instances are replaced by their content. */
const snapshot = (b: Board) =>
  JSON.parse(JSON.stringify(b.view(), (k, val) => (k === 'description' && val ? val.toString() : val)));

function run(replicaCount: number, steps: Step[], seed: number) {
  const rand = seeded(seed);
  const docs = Array.from({ length: replicaCount }, () => new Y.Doc());
  const boards = docs.map((d) => new Board(d, rand));
  const network: { to: number; update: Uint8Array }[] = [];
  docs.forEach((doc, i) =>
    doc.on('update', (update: Uint8Array, origin: unknown) => {
      if (origin === REMOTE) return; // only forward local edits; each replica sends its own changes once
      docs.forEach((_, j) => j !== i && network.push({ to: j, update }));
    }),
  );
  boards[0]!.init('Board');
  for (const s of steps) {
    if (s.kind === 'op') {
      apply(boards[s.replica % replicaCount]!, s);
    } else {
      for (let n = 0; n < s.count && network.length; n++) {
        const i = (s.pick + n * 7) % network.length;
        const msg = s.duplicate ? network[i]! : network.splice(i, 1)[0]!;
        Y.applyUpdate(docs[msg.to]!, msg.update, REMOTE);
      }
    }
  }
  // Heal the network: deliver everything left, in a scrambled order.
  while (network.length) {
    const msg = network.splice(Math.floor(rand() * network.length), 1)[0]!;
    Y.applyUpdate(docs[msg.to]!, msg.update, REMOTE);
  }
  const first = snapshot(boards[0]!);
  for (const b of boards) {
    invariants(b.view());
    expect(snapshot(b)).toEqual(first);
  }
  return first;
}

describe('convergence under concurrency', () => {
  it('property: all replicas converge to the same valid board', () => {
    fc.assert(
      fc.property(fc.integer({ min: 2, max: 4 }), fc.array(step, { minLength: 1, maxLength: 80 }), fc.integer(), (n, steps, seed) => {
        run(n, steps, seed);
      }),
      { numRuns: 400 },
    );
  }, 120_000);

  it('a long random session with heavy concurrency still converges', () => {
    const rand = seeded(42);
    const steps: Step[] = Array.from({ length: 3000 }, () =>
      rand() < 0.8
        ? { kind: 'op', replica: Math.floor(rand() * 5), op: Math.floor(rand() * 10), a: Math.floor(rand() * 1e6), b: Math.floor(rand() * 1e6), c: Math.floor(rand() * 1e6), text: String(Math.floor(rand() * 100)) }
        : { kind: 'deliver', count: Math.floor(rand() * 20), pick: Math.floor(rand() * 1e6), duplicate: rand() < 0.2 },
    );
    const view = run(5, steps, 7);
    expect(view.columns.length + view.orphans.length).toBeGreaterThan(0);
  }, 120_000);
});
