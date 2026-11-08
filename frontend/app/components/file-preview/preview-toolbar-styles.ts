import type React from 'react';

/**
 * Floating zoom / page toolbar shared by the inline, fullscreen and mobile previews.
 * Every control is 28px tall with a 4px gap and a 4px bar inset; ghost IconButtons
 * drop Radix's negative margin so the gaps are what they say.
 */
const CONTROL = 28;

export const PREVIEW_TOOLBAR_BAR: React.CSSProperties = {
  padding: 'var(--space-1)',
  gap: 'var(--space-1)',
};

export const PREVIEW_TOOLBAR_BUTTON: React.CSSProperties = {
  width: CONTROL,
  height: CONTROL,
  padding: 0,
  margin: 0,
};

/** Zoom percentage between the − and + buttons. */
export const PREVIEW_TOOLBAR_VALUE: React.CSSProperties = {
  minWidth: 44,
  height: CONTROL,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
};

/** Boxed "2/5" page indicator. */
export const PREVIEW_TOOLBAR_PAGE: React.CSSProperties = {
  height: CONTROL,
  display: 'flex',
  alignItems: 'center',
  padding: '0 var(--space-3)',
  boxSizing: 'border-box',
  backgroundColor: 'var(--slate-2)',
  border: '1px solid var(--slate-3)',
  borderRadius: 'var(--radius-1)',
};

export const PREVIEW_TOOLBAR_DIVIDER: React.CSSProperties = {
  width: 1,
  height: 16,
  margin: '0 var(--space-1)',
  backgroundColor: 'var(--slate-4)',
  flexShrink: 0,
};
