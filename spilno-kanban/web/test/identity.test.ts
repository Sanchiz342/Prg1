import { describe, expect, it } from 'vitest';

import { INKS, isInkColor } from '../src/identity';

describe('presence colours from other people', () => {
  it('accept only plain hex colours', () => {
    for (const c of INKS) expect(isInkColor(c)).toBe(true);
    for (const bad of ['url(https://evil.example/t.png)', 'red', '#fff', '#12345g', 'var(--x)', '#123456; background: url(x)', 42, null]) {
      expect(isInkColor(bad), String(bad)).toBe(false);
    }
  });
});
