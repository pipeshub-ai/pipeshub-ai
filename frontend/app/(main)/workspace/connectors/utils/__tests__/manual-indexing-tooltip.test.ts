import { describe, it, expect } from 'vitest';
import en from '@/lib/i18n/locales/en-US.json';
import de from '@/lib/i18n/locales/de-DE.json';

const MANUAL_INDEXING_TOOLTIP_TEXT = en.workspace.connectors.filters.manualIndexingTooltip;

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

  it('explains both modes in German', () => {
    const text = de.workspace.connectors.filters.manualIndexingTooltip;
    expect(text).toContain('AUS (Standard)');
    expect(text).toContain('EIN:');
    expect(text).toContain('manuell');
  });
});
