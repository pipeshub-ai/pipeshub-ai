'use client';

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import ReactDOM from 'react-dom';
import { Theme } from '@radix-ui/themes';
import {
  useToastStore,
  selectToasts,
  type Toast as ToastType,
  type ToastPlacement,
} from '@/lib/store/toast-store';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { Toast, TOAST_WIDTH } from './toast';
import {
  TOAST_EDGE,
  TOAST_RIGHT,
  toastBottom,
  useToastCornerStack,
  type CornerStack,
} from './toast-corner';

/** Where the pointer last was on the page: null before it has moved there, and once it has left. */
type PointerAt = React.MutableRefObject<{ x: number; y: number } | null>;

/** A press on one of these does its own job; a press anywhere else on a card closes it. */
const TOAST_CONTROLS = 'button, a, input, textarea, select, [role="button"], [role="link"]';

interface ToastRegionProps {
  /** `status` is polite; `alert` interrupts, for errors. */
  role: 'status' | 'alert';
  toast: ToastType | null;
  placement: ToastPlacement;
  /** Toast cards other hosts put in the corner; a bottom toast sits above them. */
  stacked: CornerStack;
  pointerAt: PointerAt;
  onHoverChange: (hovered: boolean) => void;
  onDismiss: (id: string) => void;
}

/**
 * A live region, mounted for as long as the app is, so a screen reader is
 * already listening when a toast arrives. It is the toast's own size and lets
 * clicks through wherever there is no toast. It stays in its corner whatever
 * the page shows: open panels and the chat composer do not move it.
 *
 * Pointing at a card or focusing it pauses its clock, so Undo and View can be
 * reached. A card that comes up under the resting pointer, as over the button
 * the hand goes to next, does not pause while the pointer stays on it, or it
 * would never leave that button: it pauses once the pointer has left it and
 * come back. A press on a card's text closes it, as its close button does.
 */
function ToastRegion({ role, toast, placement, stacked, pointerAt, onHoverChange, onDismiss }: ToastRegionProps) {
  const isTop = placement === 'top';
  const regionRef = useRef<HTMLDivElement>(null);
  const cameUpUnderPointer = useRef(false);
  const toastId = toast?.id;
  useLayoutEffect(() => {
    const at = pointerAt.current;
    const box = toastId ? regionRef.current?.getBoundingClientRect() : undefined;
    cameUpUnderPointer.current =
      !!at && !!box && at.x >= box.left && at.x <= box.right && at.y >= box.top && at.y <= box.bottom;
  }, [toastId, pointerAt]);
  return (
    <div
      ref={regionRef}
      role={role}
      aria-live={role === 'alert' ? 'assertive' : 'polite'}
      aria-atomic="true"
      data-ph-toast-region=""
      data-ph-toast-placement={toast ? placement : undefined}
      onMouseEnter={() => {
        if (!cameUpUnderPointer.current) onHoverChange(true);
      }}
      onMouseLeave={() => {
        cameUpUnderPointer.current = false;
        onHoverChange(false);
      }}
      onFocus={() => onHoverChange(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onHoverChange(false);
      }}
      onClick={(e) => {
        if (!toast?.showCloseButton || (e.target as Element).closest(TOAST_CONTROLS)) return;
        // A press that ends a text selection is someone copying the text.
        if (window.getSelection()?.toString()) return;
        onDismiss(toast.id);
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

/** Follows the pointer over the page, so a card can tell whether it came up under it. */
function usePointerAt(): PointerAt {
  const at = useRef<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const follow = (e: PointerEvent) => {
      at.current = { x: e.clientX, y: e.clientY };
    };
    // Off the page, or a finger lifted, the pointer is nowhere a card could come up under.
    const leave = (e: PointerEvent) => {
      if (!e.relatedTarget) at.current = null;
    };
    const options = { capture: true, passive: true } as const;
    window.addEventListener('pointermove', follow, options);
    window.addEventListener('pointerdown', follow, options);
    window.addEventListener('pointerout', leave, options);
    return () => {
      window.removeEventListener('pointermove', follow, options);
      window.removeEventListener('pointerdown', follow, options);
      window.removeEventListener('pointerout', leave, options);
    };
  }, []);
  return at;
}

export function ToastContainer() {
  const toasts = useToastStore(selectToasts);
  const { setHovered, removeToast } = useToastStore();
  const { appearance } = useThemeAppearance();
  const isMobile = useIsMobile();
  const pointerAt = usePointerAt();

  const stacked = useToastCornerStack();
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  // One toast at a time: the newest that is not waiting. Toasts waiting behind an Undo toast are not drawn yet.
  const current = toasts.find((t) => !t.isWaiting) ?? null;

  if (!mounted) return null;

  // Bottom right; the top edge for a caller that asks, and on a phone, where side panels fill the screen with their
  // actions in the footer.
  const placement: ToastPlacement = isMobile || current?.placement === 'top' ? 'top' : 'bottom';
  const isError = current?.variant === 'error';
  const shared = {
    placement,
    stacked,
    pointerAt,
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
