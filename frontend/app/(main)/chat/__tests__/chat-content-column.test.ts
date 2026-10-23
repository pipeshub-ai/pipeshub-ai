import { describe, it, expect } from 'vitest';
import {
  CHAT_CONTENT_MAX_WIDTH,
  CHAT_CONTENT_MIN_GUTTER_DESKTOP,
  CHAT_CONTENT_PADDING_X,
  chatContentColumnStyle,
} from '../constants';

describe('chatContentColumnStyle', () => {
  it('keeps the 800px column on a wide pane, and a gutter from the pane edge on a narrow one (a side panel open)', () => {
    const style = chatContentColumnStyle(false);
    expect(style.maxWidth).toBe(CHAT_CONTENT_MAX_WIDTH);
    expect(style.width).toBe(`calc(100% - 2 * ${CHAT_CONTENT_MIN_GUTTER_DESKTOP})`);
    expect(CHAT_CONTENT_MIN_GUTTER_DESKTOP).toBe('var(--space-5)');
    expect(style.paddingLeft).toBe(CHAT_CONTENT_PADDING_X.desktop);
  });

  it('fills a phone, inset by its own padding', () => {
    const style = chatContentColumnStyle(true);
    expect(style.width).toBe('100%');
    expect(style.paddingLeft).toBe(CHAT_CONTENT_PADDING_X.mobile);
    expect(style.paddingRight).toBe(CHAT_CONTENT_PADDING_X.mobile);
  });
});
