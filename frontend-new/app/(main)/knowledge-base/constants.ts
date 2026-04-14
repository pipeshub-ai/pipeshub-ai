import type { CSSProperties } from 'react';

export const DIALOG_STYLES = {
  overlayBg: 'rgba(28, 32, 36, 0.3)' as CSSProperties['backgroundColor'],
  contentBg: 'var(--color-panel-solid)' as CSSProperties['backgroundColor'],
  boxShadow:
    '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)' as CSSProperties['boxShadow'],
} as const;
