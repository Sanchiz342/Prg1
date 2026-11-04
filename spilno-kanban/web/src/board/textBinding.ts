// Pure helpers for binding a <textarea> to a Y.Text.
//
// Local typing: the browser gives us the whole new value; we turn it into ONE minimal edit (common prefix and
// suffix are kept), so concurrent edits elsewhere in the text merge character-precisely.
// Remote edits: Yjs gives a delta; we shift the local cursor/selection through it so the caret does not jump
// when someone types above it.

export interface Edit {
  index: number;
  remove: number;
  insert: string;
}

const isHigh = (c: number) => c >= 0xd800 && c <= 0xdbff;
const isLow = (c: number) => c >= 0xdc00 && c <= 0xdfff;

/**
 * Minimal single-range edit turning `before` into `after`, or null if equal. `caret` (the cursor position in
 * `after`) resolves ambiguous cases like "aa" -> "aaa" in favour of where the user actually typed.
 */
export function diff(before: string, after: string, caret?: number): Edit | null {
  if (before === after) return null;
  let start = 0;
  const maxStart = Math.min(before.length, after.length);
  while (start < maxStart && before.charCodeAt(start) === after.charCodeAt(start)) start++;
  let endB = before.length;
  let endA = after.length;
  while (endB > start && endA > start && before.charCodeAt(endB - 1) === after.charCodeAt(endA - 1)) {
    endB--;
    endA--;
  }
  if (caret !== undefined && endA > caret) {
    // Slide the changed range left (towards the caret) while the text allows it.
    // Sliding by s is only valid if the s chars leaving the common prefix equal the s chars entering the common
    // suffix, in BOTH strings (otherwise the edit would no longer reproduce `after`).
    let shift = Math.min(start, endA - caret, endB);
    while (
      shift > 0 &&
      !(
        before.slice(start - shift, start) === before.slice(endB - shift, endB) &&
        after.slice(start - shift, start) === after.slice(endA - shift, endA)
      )
    )
      shift--;
    start -= shift;
    endB -= shift;
    endA -= shift;
  }
  // Never split a UTF-16 surrogate pair (emoji) at either end of the range.
  if (start > 0 && isHigh(after.charCodeAt(start - 1))) start--;
  if (endA < after.length && isLow(after.charCodeAt(endA)) && endB < before.length) {
    endA++;
    endB++;
  }
  return { index: start, remove: endB - start, insert: after.slice(start, endA) };
}

/** Applies an edit to a plain string (used to check diffs, and by tests). */
export const applyEdit = (s: string, e: Edit) => s.slice(0, e.index) + e.insert + s.slice(e.index + e.remove);

export type Delta = { retain?: number; insert?: unknown; delete?: number }[];

/**
 * Where caret position `pos` ends up after a remote Y.Text delta — the same result as a Yjs relative position:
 * text inserted at or before the caret pushes it right; a deleted range containing the caret collapses onto it.
 */
export function transformPosition(pos: number, delta: Delta): number {
  let index = 0; // position in the old text
  let shift = 0;
  for (const op of delta) {
    if (index > pos) break;
    if (op.retain !== undefined) {
      index += op.retain;
    } else if (op.insert !== undefined) {
      shift += typeof op.insert === 'string' ? op.insert.length : 1;
    } else if (op.delete !== undefined) {
      shift -= Math.min(op.delete, pos - index);
      index += op.delete;
    }
  }
  return Math.max(0, pos + shift);
}
