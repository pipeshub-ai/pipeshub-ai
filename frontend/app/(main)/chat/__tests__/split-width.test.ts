import { describe, it, expect } from 'vitest';
import { chatContentColumnStyle, clampSplitWidth } from '../constants';

describe('clampSplitWidth', () => {
  it('caps a width saved on a wider screen at 60% of this viewport', () => {
    expect(clampSplitWidth(900, 1000)).toBe(600);
  });

  it('keeps the saved width when the viewport has room for it', () => {
    expect(clampSplitWidth(900, 1920)).toBe(900);
  });
});

describe('chatContentColumnStyle', () => {
  it('always pads the column so a pane narrower than the max width keeps a gutter', () => {
    const s = chatContentColumnStyle();
    expect(s.paddingLeft).toBe('var(--space-4)');
    expect(s.paddingRight).toBe('var(--space-4)');
    // The max grows by the padding, so the text column stays 800px on desktop.
    expect(s.maxWidth).toBe('calc(800px + 2 * var(--space-4))');
  });
});
