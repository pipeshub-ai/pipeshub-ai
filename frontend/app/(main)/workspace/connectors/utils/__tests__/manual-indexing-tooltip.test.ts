import { describe, it, expect } from 'vitest';
import { MANUAL_INDEXING_TOOLTIP_TEXT } from '../manual-indexing-tooltip';

describe('MANUAL_INDEXING_TOOLTIP_TEXT', () => {
  it('explains both OFF and ON modes', () => {
    expect(MANUAL_INDEXING_TOOLTIP_TEXT).toContain('OFF (default)');
    expect(MANUAL_INDEXING_TOOLTIP_TEXT).toContain('ON:');
    expect(MANUAL_INDEXING_TOOLTIP_TEXT).toContain('automatically indexed');
    expect(MANUAL_INDEXING_TOOLTIP_TEXT).toContain('knowledge base');
  });

  it('stays plain text so Radix tooltip colors remain visible', () => {
    expect(typeof MANUAL_INDEXING_TOOLTIP_TEXT).toBe('string');
    expect(MANUAL_INDEXING_TOOLTIP_TEXT.trim().length).toBeGreaterThan(0);
    expect(MANUAL_INDEXING_TOOLTIP_TEXT).not.toContain('var(--gray-12)');
  });
});
