'use client';

import React, { useEffect, useRef, useState } from 'react';
import ReactDOM from 'react-dom';
import { Box, Text, Theme, VisuallyHidden } from '@radix-ui/themes';
import {
  useToastStore,
  selectToasts,
  selectIsHovered,
  type ToastPlacement,
  type Toast as ToastData,
} from '@/lib/store/toast-store';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { Toast } from './toast';

// ========================================
// Stack configuration (visual only; height is not fixed — avoids viewport cutoff)
// ========================================

const GAP_EXPANDED = 12;
const GAP_COLLAPSED = 6;

interface ToastStackProps {
  placement: ToastPlacement;
  toasts: ReturnType<typeof selectToasts>;
  isHovered: boolean;
  onHoverChange: (hovered: boolean) => void;
  onDismiss: (id: string) => void;
}

function ToastStack({
  placement,
  toasts,
  isHovered,
  onHoverChange,
  onDismiss,
}: ToastStackProps) {
  if (toasts.length === 0) return null;

  const maxVisible = 3;
  const visibleToasts = toasts.slice(0, maxVisible);
  const hiddenCount = Math.max(0, toasts.length - maxVisible);
  const gap = isHovered ? GAP_EXPANDED : GAP_COLLAPSED;
  const isTop = placement === 'top';

  return (
    <Box
      data-ph-toast-region=""
      data-ph-toast-placement={placement}
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
      style={{
        position: 'fixed',
        ...(isTop
          ? { top: 'max(16px, env(safe-area-inset-top, 0px))' }
          : { bottom: 'max(16px, env(safe-area-inset-bottom, 0px))' }),
        right: 'max(16px, env(safe-area-inset-right, 0px))',
        left: 'auto',
        maxHeight: 'calc(100dvh - 32px)',
        maxWidth: 'min(420px, calc(100vw - 32px))',
        width: '100%',
        zIndex: 9999,
        pointerEvents: 'none',
        display: 'flex',
        flexDirection: isTop ? 'column' : 'column-reverse',
        gap,
        overflowY: 'hidden',
        overflowX: 'hidden',
        alignItems: 'stretch',
        boxSizing: 'border-box',
      }}
    >
      {visibleToasts.map((toast, index) => {
        const depth = visibleToasts.length - 1 - index;
        const isCollapsed = !isHovered && visibleToasts.length > 1;
        const scale = isCollapsed ? Math.max(0.92, 1 - depth * 0.03) : 1;
        const opacity = isCollapsed ? Math.max(0.55, 1 - depth * 0.18) : 1;

        return (
          <Box
            key={toast.id}
            style={{
              flexShrink: 0,
              pointerEvents: 'auto',
              transform: `scale(${scale})`,
              transformOrigin: isTop ? 'top right' : 'bottom right',
              opacity,
              transition: 'transform 0.25s ease, opacity 0.25s ease',
              animation: toast.isExiting ? 'none' : 'toastSlideIn 0.3s ease',
            }}
          >
            <Toast toast={toast} onDismiss={onDismiss} />
          </Box>
        );
      })}

      {hiddenCount > 0 ? (
        <Box
          style={{
            flexShrink: 0,
            alignSelf: 'flex-end',
            padding: '4px 8px',
            backgroundColor: 'var(--slate-3)',
            borderRadius: 'var(--radius-2)',
            pointerEvents: 'auto',
          }}
        >
          <Text size="1" style={{ color: 'var(--slate-11)', fontWeight: 500 }}>
            +{hiddenCount} more
          </Text>
        </Box>
      ) : null}
    </Box>
  );
}

/** How long the region stays empty before the same words are put back, so they are heard again. */
const REPEAT_ANNOUNCEMENT_GAP_MS = 100;

/**
 * Screen readers hear toasts through this region, which stays mounted with or without toasts:
 * a live region inserted together with its text is often not read at all. It carries the toast
 * that changed last, whether it was just added or just updated (a progress toast turning into
 * its result), so two requests that finish in any order are both heard. A dismissal says nothing.
 */
function ToastAnnouncer({ toasts }: { toasts: ToastData[] }) {
  const [text, setText] = useState('');
  const seen = useRef(new Map<string, string>());
  const shown = useRef<{ id: string; text: string }>({ id: '', text: '' });
  // Owned by the change that set it: a re-render with no toast change must not cancel it.
  const restore = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (restore.current) clearTimeout(restore.current);
  }, []);

  useEffect(() => {
    const words = (t: ToastData) => [t.title, t.description].filter(Boolean).join('. ');
    const signature = (t: ToastData) => `${t.variant}\u0000${words(t)}`;
    // Newest first, as the store keeps them, so a burst of changes reads the newest.
    const changed = toasts.find((t) => seen.current.get(t.id) !== signature(t));
    seen.current = new Map(toasts.map((t) => [t.id, signature(t)]));
    if (!changed) return;

    if (restore.current) clearTimeout(restore.current);
    restore.current = null;
    const next = words(changed);
    const previous = shown.current;
    shown.current = { id: changed.id, text: next };
    if (next !== previous.text) {
      setText(next);
      return;
    }
    // A change with the same words leaves the region unchanged, which is not announced.
    // Empty it, then put the words back in a later task; a newer change cancels the restore.
    setText('');
    restore.current = setTimeout(() => {
      restore.current = null;
      setText(next);
    }, REPEAT_ANNOUNCEMENT_GAP_MS);
  }, [toasts]);

  return (
    <VisuallyHidden>
      <div role="status" aria-live="polite" aria-atomic="true" data-ph-toast-announcer="">
        {text}
      </div>
    </VisuallyHidden>
  );
}

export function ToastContainer() {
  const toasts = useToastStore(selectToasts);
  const isHovered = useToastStore(selectIsHovered);
  const { setHovered, removeToast } = useToastStore();
  const { appearance } = useThemeAppearance();
  const isMobile = useIsMobile();

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const announcer = <ToastAnnouncer toasts={toasts.filter((t) => !t.isExiting)} />;

  if (toasts.length === 0 || !mounted) {
    return announcer;
  }

  // On a phone, side panels fill the screen with their actions in the footer; keep toasts off them.
  const topToasts = isMobile ? toasts : toasts.filter((t) => t.placement === 'top');
  const bottomToasts = isMobile ? [] : toasts.filter((t) => t.placement !== 'top');

  return (
    <>
      {announcer}
      {ReactDOM.createPortal(
    <Theme accentColor="jade" grayColor="olive" appearance={appearance} radius="medium" data-accent-color="emerald">
      <style jsx global>{`
        @keyframes toastSlideIn {
          from {
            transform: translateX(100%);
            opacity: 0;
          }
          to {
            transform: translateX(0);
            opacity: 1;
          }
        }

        @keyframes spin {
          from {
            transform: rotate(0deg);
          }
          to {
            transform: rotate(360deg);
          }
        }
      `}</style>

      <ToastStack
        placement="top"
        toasts={topToasts}
        isHovered={isHovered}
        onHoverChange={setHovered}
        onDismiss={removeToast}
      />
      <ToastStack
        placement="bottom"
        toasts={bottomToasts}
        isHovered={isHovered}
        onHoverChange={setHovered}
        onDismiss={removeToast}
      />
    </Theme>,
    document.body
      )}
    </>
  );
}
