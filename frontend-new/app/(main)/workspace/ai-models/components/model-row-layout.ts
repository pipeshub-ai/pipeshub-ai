import type { CSSProperties } from 'react';

const MODEL_ROW_CARD_STATIC: CSSProperties = {
  width: '100%',
  minWidth: 0,
  minHeight: 88,
  padding: '16px 20px',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-2)',
  transition: 'background-color 150ms ease',
  boxSizing: 'border-box',
};

export function modelRowCardStyle(hover: boolean): CSSProperties {
  return {
    ...MODEL_ROW_CARD_STATIC,
    backgroundColor: hover ? 'var(--olive-3)' : 'var(--olive-2)',
  };
}

export const MODEL_ROW_ICON_CONTAINER_STYLE: CSSProperties = {
  width: 44,
  height: 44,
  padding: 6,
  backgroundColor: 'var(--gray-a2)',
  borderRadius: 'var(--radius-2)',
  flexShrink: 0,
};
