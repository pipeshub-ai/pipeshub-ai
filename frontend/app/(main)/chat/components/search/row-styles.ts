import type React from 'react';

/**
 * The search dialog's rows (New chat, chats, search hits) share one look: a
 * ghost Card on a real button, 36px tall for a one-line row, the muted fill on
 * hover and on keyboard focus. The focus ring is Radix's own on the Card.
 */
export const CMDK_ROW_CLASS = 'cmdk-row';

/**
 * The one scoped rule for the rows. Inline styles cannot express hover or
 * focus-visible, and a button's own ground has to be cleared in the same place
 * so the hover fill can win over it.
 */
export const CMDK_ROW_CSS = `
.${CMDK_ROW_CLASS} {
  border: 0;
  background-color: transparent;
  color: inherit;
  transition: background-color var(--motion-micro) var(--motion-ease);
}
.${CMDK_ROW_CLASS}:hover,
.${CMDK_ROW_CLASS}:focus-visible {
  background-color: var(--fill-muted);
}
`;

export const cmdkRowStyle: React.CSSProperties = {
  margin: 0,
  display: 'flex',
  flexDirection: 'column',
  justifyContent: 'center',
  alignItems: 'stretch',
  gap: 'var(--space-1)',
  width: '100%',
  minWidth: 0,
  minHeight: 'calc(var(--space-6) + var(--space-1))',
  boxSizing: 'border-box',
  padding: 'var(--space-2) var(--space-3)',
  borderRadius: 'var(--radius-1)',
  cursor: 'pointer',
  textAlign: 'left',
  flexShrink: 0,
};

export const cmdkRowTitleStyle: React.CSSProperties = {
  color: 'var(--text-primary)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

export const cmdkRowSubtitleStyle: React.CSSProperties = {
  color: 'var(--text-secondary)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};
