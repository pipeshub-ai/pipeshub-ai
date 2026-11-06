'use client';

import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom';
import { Theme } from '@radix-ui/themes';
import {
  useToastStore,
  selectToasts,
  selectIsHovered,
  type Toast as ToastType,
  type ToastPlacement,
} from '@/lib/store/toast-store';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { Toast, TOAST_WIDTH } from './toast';
import { TOAST_EDGE, TOAST_RIGHT, toastBottom, useToastCornerStack, type CornerStack } from './toast-corner';

interface ToastRegionProps {
  /** `status` is polite; `alert` interrupts, for errors. */
  role: 'status' | 'alert';
  toast: ToastType | null;
  placement: ToastPlacement;
  /** Toast cards other hosts put in the corner; a bottom toast sits above them. */
  stacked: CornerStack;
  /** The pause is on: pointer or focus is on the toast. */
  hovered: boolean;
  onHoverChange: (hovered: boolean) => void;
  onDismiss: (id: string) => void;
}

/**
 * A live region, mounted for as long as the app is, so a screen reader is
 * already listening when a toast arrives. It is the toast's own size and lets
 * clicks through wherever there is no toast. It stays in its corner whatever
 * the page shows: open panels and the chat composer do not move it.
 */
function ToastRegion({ role, toast, placement, stacked, hovered, onHoverChange, onDismiss }: ToastRegionProps) {
  const isTop = placement === 'top';
  return (
    <div
      role={role}
      aria-live={role === 'alert' ? 'assertive' : 'polite'}
      aria-atomic="true"
      data-ph-toast-region=""
      data-ph-toast-placement={toast ? placement : undefined}
      onMouseEnter={() => onHoverChange(true)}
      // A card that arrived under a still pointer pauses on the pointer's next move.
      onMouseMove={() => {
        if (!hovered) onHoverChange(true);
      }}
      onMouseLeave={() => onHoverChange(false)}
      onFocus={() => onHoverChange(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onHoverChange(false);
      }}
      style={{
        position: 'fixed',
        ...(isTop ? { top: `max(${TOAST_EDGE}, env(safe-area-inset-top, 0px))` } : { bottom: toastBottom(stacked) }),
        right: TOAST_RIGHT,
        width: TOAST_WIDTH,
        maxWidth: `calc(100vw - 2 * ${TOAST_EDGE})`,
        zIndex: 9999,
        pointerEvents: 'none',
        display: 'flex',
        justifyContent: 'flex-end',
      }}
    >
      {toast ? (
        <Toast
          key={toast.id}
          toast={toast}
          onDismiss={onDismiss}
          style={{
            pointerEvents: 'auto',
            animation: toast.isExiting ? 'none' : 'toastSlideIn var(--motion-surface) var(--motion-ease)',
          }}
        />
      ) : null}
    </div>
  );
}

export function ToastContainer() {
  const toasts = useToastStore(selectToasts);
  const hovered = useToastStore(selectIsHovered);
  const { setHovered, removeToast } = useToastStore();
  const { appearance } = useThemeAppearance();

  const stacked = useToastCornerStack();
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  // One toast at a time: the newest that is not waiting. Toasts waiting behind an Undo toast are not drawn yet.
  const current = toasts.find((t) => !t.isWaiting) ?? null;

  if (!mounted) return null;

  // Bottom right on every screen size; only a caller that asks for the top edge gets it.
  const placement: ToastPlacement = current?.placement === 'top' ? 'top' : 'bottom';
  const isError = current?.variant === 'error';
  const shared = {
    placement,
    stacked,
    hovered,
    onHoverChange: setHovered,
    onDismiss: removeToast,
  };

  // The portal sits outside the app's Theme, so it brings one: the same appearance; accent and gray come from the defaults.
  return ReactDOM.createPortal(
    <Theme appearance={appearance} hasBackground={false}>
      <ToastRegion role="status" toast={current && !isError ? current : null} {...shared} />
      <ToastRegion role="alert" toast={current && isError ? current : null} {...shared} />
    </Theme>,
    document.body
  );
}
