import { describe, it, expect } from 'vitest';
import { parseCsvCellContent } from '../csv-utils';

describe('parseCsvCellContent', () => {
  it('shows only the badge for a citation whose URL has parentheses', () => {
    const parts = parseCsvCellContent('"[1](https://en.wikipedia.org/wiki/Python_(programming_language))"');
    expect(parts.filter((p) => typeof p === 'string')).toEqual([]);
    expect(parts).toHaveLength(1);
  });

  it('keeps text between badges', () => {
    const parts = parseCsvCellContent('see [1](https://x.test/a_(b)) and [2](https://x.test/c)');
    expect(parts.filter((p) => typeof p === 'string')).toEqual(['see ', ' and ']);
  });
});
