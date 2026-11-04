import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import * as Y from 'yjs';

import { applyEdit, diff, transformPosition, type Delta } from '../src/board/textBinding';

describe('diff', () => {
  it('finds minimal edits', () => {
    expect(diff('hello', 'hello')).toBeNull();
    expect(diff('hello', 'help!')).toEqual({ index: 3, remove: 2, insert: 'p!' });
    expect(diff('', 'abc')).toEqual({ index: 0, remove: 0, insert: 'abc' });
    expect(diff('abc', '')).toEqual({ index: 0, remove: 3, insert: '' });
    expect(diff('Привіт світ', 'Привіт, світ')).toEqual({ index: 6, remove: 0, insert: ',' });
  });

  it('uses the caret to place ambiguous inserts', () => {
    expect(diff('aa', 'aaa', 1)).toEqual({ index: 0, remove: 0, insert: 'a' });
    expect(diff('aa', 'aaa', 3)).toEqual({ index: 2, remove: 0, insert: 'a' });
  });

  it('never splits emoji', () => {
    const e = diff('a😀', 'a😃')!; // same high surrogate, different low surrogate
    expect(e.index).toBe(1);
    expect(e.insert).toBe('😃');
  });

  it('property: applying the diff reproduces the new text', () => {
    fc.assert(
      fc.property(fc.string(), fc.string(), fc.nat(), (a, b, c) => {
        const e = diff(a, b, c % (b.length + 1));
        expect(e ? applyEdit(a, e) : a).toBe(b);
      }),
      { numRuns: 2000 },
    );
  });
});

describe('transformPosition', () => {
  it('moves the caret by remote edits before or at it', () => {
    expect(transformPosition(5, [{ insert: 'abc' }])).toBe(8);
    expect(transformPosition(5, [{ retain: 5 }, { insert: 'x' }])).toBe(6); // insert at caret pushes it, like Yjs
    expect(transformPosition(5, [{ retain: 7 }, { insert: 'x' }])).toBe(5);
    expect(transformPosition(5, [{ retain: 1 }, { delete: 2 }])).toBe(3);
    expect(transformPosition(5, [{ retain: 3 }, { delete: 10 }])).toBe(3); // caret inside deleted range
  });

  it('property: matches what Yjs does to a relative position', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 0, maxLength: 20 }), fc.nat(), fc.nat(), fc.nat({ max: 5 }), fc.string({ maxLength: 4 }), (text, p, at, del, ins) => {
        const doc = new Y.Doc();
        const t = doc.getText('t');
        t.insert(0, text);
        const caret = p % (text.length + 1);
        const rel = Y.createRelativePositionFromTypeIndex(t, caret);
        let delta: Delta = [];
        t.observe((e) => (delta = e.delta as Delta));
        const i = at % (text.length + 1);
        doc.transact(() => {
          if (del) t.delete(i, Math.min(del, text.length - i));
          if (ins) t.insert(i, ins);
        });
        const expected = Y.createAbsolutePositionFromRelativePosition(rel, doc)!.index;
        expect(transformPosition(caret, delta)).toBe(expected);
      }),
      { numRuns: 2000 },
    );
  });
});
