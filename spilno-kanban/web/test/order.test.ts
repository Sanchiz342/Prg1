import { describe, expect, it } from 'vitest';
import fc from 'fast-check';

import { compareByKey, isValidKey, keyAt, keyBetween } from '../src/board/order';

describe('fractional keys', () => {
  it('produces keys strictly between the bounds', () => {
    expect(keyBetween(null, null, () => 0)).toBe('V01');
    const a = keyBetween(null, null);
    const b = keyBetween(a, null);
    const c = keyBetween(a, b);
    expect(a < c && c < b).toBe(true);
    for (const k of [a, b, c]) expect(isValidKey(k)).toBe(true);
  });

  it('rejects inverted or invalid bounds', () => {
    expect(() => keyBetween('b', 'a')).toThrow();
    expect(() => keyBetween('a', 'a')).toThrow();
    expect(() => keyBetween('a0', null)).toThrow(); // trailing zero is not a canonical key
    expect(() => keyBetween('a!', null)).toThrow();
  });

  it('works between adjacent digits and prefixes', () => {
    for (const [a, b] of [['V', 'W'], ['V', 'V1'], ['V01', 'V1'], ['1', '2'], [null, '1'], ['zz', null]] as const) {
      const k = keyBetween(a, b);
      expect(isValidKey(k), `${a}..${b}: ${k}`).toBe(true);
      if (a !== null) expect(k > a, `${k} > ${a}`).toBe(true);
      if (b !== null) expect(k < b, `${k} < ${b}`).toBe(true);
    }
  });

  it('property: repeated inserts anywhere keep a strictly ordered list', () => {
    fc.assert(
      fc.property(fc.array(fc.nat(), { maxLength: 300 }), fc.integer(), (positions, seed) => {
        let s = seed;
        const rand = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x80000000);
        const keys: string[] = [];
        for (const p of positions) {
          const i = p % (keys.length + 1);
          const k = keyBetween(keys[i - 1] ?? null, keys[i] ?? null, rand);
          keys.splice(i, 0, k);
        }
        for (let i = 1; i < keys.length; i++) expect(keys[i - 1]! < keys[i]!).toBe(true);
        for (const k of keys) expect(isValidKey(k)).toBe(true);
      }),
      { numRuns: 300 },
    );
  });

  it('keeps keys short when appending', () => {
    let k: string | null = null;
    for (let i = 0; i < 1000; i++) k = keyBetween(k, null);
    expect(k!.length).toBeLessThan(40);
  });

  it('keyAt handles duplicate neighbour keys', () => {
    const sorted = [
      { key: 'V', id: 'a' },
      { key: 'V', id: 'b' },
      { key: 'X', id: 'c' },
    ];
    const k = keyAt(sorted, 1);
    expect(k > 'V' && k < 'X').toBe(true);
    expect(keyAt([], 5) < 'z').toBe(true);
  });

  it('ties are broken by id', () => {
    const items = [
      { key: 'V', id: 'b' },
      { key: 'V', id: 'a' },
      { key: 'U', id: 'z' },
    ].sort(compareByKey);
    expect(items.map((i) => i.id)).toEqual(['z', 'a', 'b']);
  });
});
