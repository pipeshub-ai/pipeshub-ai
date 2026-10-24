import type React from 'react';

export const PAGE_PADDING_X = 'clamp(var(--space-4), 4vw, 100px)';
export const CATALOG_MAX_WIDTH = 1280;
export const SETTINGS_MAX_WIDTH = 960;
export const TOUCH_TARGET_PX = 44;

/** Overlay width that never exceeds the viewport minus 16px gutters. */
export const overlayWidth = (px: number): string => `min(${px}px, calc(100vw - 32px))`;

export const touchTarget = (enabled: boolean): React.CSSProperties | undefined =>
  enabled ? { minWidth: TOUCH_TARGET_PX, minHeight: TOUCH_TARGET_PX } : undefined;

/** Right-docked side panel: inset 0 and square on phones, 10px-inset card (never wider than the viewport) elsewhere. */
export const sidePanelFrame = (isMobile: boolean): React.CSSProperties =>
  isMobile
    ? { position: 'fixed', inset: 0, width: '100%', maxWidth: 'none', maxHeight: 'none', borderRadius: 0 }
    : {
        position: 'fixed',
        top: 10,
        right: 10,
        bottom: 10,
        width: '37.5rem',
        maxWidth: 'calc(100vw - 20px)',
        maxHeight: 'calc(100vh - 20px)',
      };
