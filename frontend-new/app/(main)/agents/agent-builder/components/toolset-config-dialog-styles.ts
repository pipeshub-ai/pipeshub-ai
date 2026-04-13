import type { CSSProperties } from 'react';

/** Shared panel + overlay for user and agent toolset credential modals. */
export const toolsetDialogPanelStyle: CSSProperties = {
  maxWidth: 'min(36rem, calc(100vw - 2rem))',
  width: '100%',
  padding: 'var(--space-5)',
  zIndex: 1000,
  backgroundColor: 'var(--color-panel-solid)',
  borderRadius: 'var(--radius-5)',
  border: '1px solid var(--olive-a3)',
  boxShadow:
    '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
};

export const toolsetDialogBackdropStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
  backgroundColor: 'rgba(28, 32, 36, 0.1)',
  zIndex: 999,
};

/** Equal-width action buttons that span the dialog content without a dead zone on the right. */
export const toolsetDialogActionGridStyle: CSSProperties = {
  display: 'grid',
  width: '100%',
  gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 10.5rem), 1fr))',
  gap: 'var(--space-2)',
};
