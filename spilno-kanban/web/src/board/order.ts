// Fractional order keys: strings that sort between their neighbours, so an item can be placed between two others
// by writing ONE value (its own key) instead of re-indexing a list.
//
// A key is a base-62 fraction without the leading "0." and without trailing zeros ("V" = 31/62, "V8" = ...).
// Digits are ASCII-ordered, so plain string comparison orders keys numerically.
//
// Two people inserting at the same spot at the same time would compute the same midpoint; a random suffix makes
// that collision unlikely. If keys still collide, readers break the tie by item id, so every replica agrees.

export const DIGITS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
const ZERO = DIGITS[0]!;
const KEY = /^[0-9A-Za-z]*[1-9A-Za-z]$/;

export function isValidKey(key: unknown): key is string {
  return typeof key === 'string' && key.length <= 256 && KEY.test(key);
}

/** Shortest-ish fraction strictly between a and b ("" = 0, null = 1). Requires a < b. */
function midpoint(a: string, b: string | null): string {
  if (b !== null) {
    let n = 0;
    while ((a[n] ?? ZERO) === b[n]) n++;
    if (n > 0) return b.slice(0, n) + midpoint(a.slice(n), b.slice(n));
  }
  const digitA = a ? DIGITS.indexOf(a[0]!) : 0;
  const digitB = b !== null ? DIGITS.indexOf(b[0]!) : DIGITS.length;
  if (digitB - digitA > 1) return DIGITS[Math.round((digitA + digitB) / 2)]!;
  if (b !== null && b.length > 1) return b.slice(0, 1);
  return DIGITS[digitA]! + midpoint(a.slice(1), null);
}

/**
 * Smallest-step key after `a`: bump the first digit that can be bumped. Appending N items grows keys by about one
 * character per 61 items, instead of one per ~6 with repeated midpoints.
 */
function after(a: string): string {
  if (a === '') return DIGITS[1]!;
  const i = DIGITS.indexOf(a[0]!);
  if (i < DIGITS.length - 1) return DIGITS[i + 1]!;
  return a[0]! + after(a.slice(1));
}

/** Smallest-step key before `b` (mirror of `after`); never produces a trailing zero. */
function before(b: string): string {
  if (b === '') return DIGITS[DIGITS.length - 1]!; // only reached below a leading '0'/'1' prefix
  const i = DIGITS.indexOf(b[0]!);
  if (i > 1) return DIGITS[i - 1]!;
  return DIGITS[0]! + before(i === 1 ? '' : b.slice(1));
}

/**
 * A key strictly between `a` and `b` (null = open end). `rand` adds a two-digit random suffix when that keeps
 * the key in range, so concurrent inserts into the same gap rarely produce identical keys.
 */
export function keyBetween(a: string | null, b: string | null, rand: () => number = Math.random): string {
  if (a !== null && !isValidKey(a)) throw new Error(`invalid key ${JSON.stringify(a)}`);
  if (b !== null && !isValidKey(b)) throw new Error(`invalid key ${JSON.stringify(b)}`);
  if (a !== null && b !== null && a >= b) throw new Error(`keyBetween: ${a} >= ${b}`);
  const m = a !== null && b === null ? after(a) : a === null && b !== null ? before(b) : midpoint(a ?? '', b);
  const d1 = DIGITS[Math.floor(rand() * DIGITS.length)]!;
  const d2 = DIGITS[1 + Math.floor(rand() * (DIGITS.length - 1))]!; // never a trailing zero
  const jittered = m + d1 + d2;
  return b === null || jittered < b ? jittered : m;
}

/** Compares items by key, then id — the total order every replica uses. */
export function compareByKey(a: { key: string; id: string }, b: { key: string; id: string }): number {
  if (a.key !== b.key) return a.key < b.key ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * Key for inserting at `index` into `sorted` (ascending by compareByKey). When neighbours share a key, the upper
 * bound skips forward to the next strictly greater key, so a valid key always exists.
 */
export function keyAt<T extends { key: string }>(sorted: readonly T[], index: number, rand?: () => number): string {
  const i = Math.max(0, Math.min(index, sorted.length));
  const lo = i > 0 ? sorted[i - 1]!.key : null;
  let j = i;
  while (j < sorted.length && lo !== null && sorted[j]!.key <= lo) j++;
  const hi = j < sorted.length ? sorted[j]!.key : null;
  return keyBetween(lo, hi, rand);
}
