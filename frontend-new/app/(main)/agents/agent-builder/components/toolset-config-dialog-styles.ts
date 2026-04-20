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

/** Primary actions: wrap inside the footer toolbar; keep separate from Close for alignment control. */
export const toolsetDialogPrimaryActionsStyle: CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: 'var(--space-2)',
  alignItems: 'center',
};

/** Footer: primary block + Close on one row when space allows; Close stays top-aligned when primaries wrap. */
export const toolsetDialogFooterToolbarStyle: CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'flex-start',
  gap: 'var(--space-3)',
  width: '100%',
  minWidth: 0,
};

export const toolsetDialogFooterPrimaryClusterStyle: CSSProperties = {
  flex: '1 1 auto',
  minWidth: 0,
  maxWidth: '100%',
};
