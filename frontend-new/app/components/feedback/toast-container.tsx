'use client';

import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom';
import { Box, Text, Theme } from '@radix-ui/themes';
import { useToastStore, selectToasts, selectIsHovered } from '@/lib/store/toast-store';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { Toast } from './toast';

// ========================================
// Stack configuration (visual only; height is not fixed — avoids viewport cutoff)
// ========================================

const GAP_EXPANDED = 12;
const GAP_COLLAPSED = 6;

export function ToastContainer() {
  const toasts = useToastStore(selectToasts);
  const isHovered = useToastStore(selectIsHovered);
  const { setHovered, removeToast } = useToastStore();
  const { appearance } = useThemeAppearance();

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  if (toasts.length === 0 || !mounted) {
    return null;
  }

  const maxVisible = 3;
  const visibleToasts = toasts.slice(0, maxVisible);
  const hiddenCount = Math.max(0, toasts.length - maxVisible);
  const gap = isHovered ? GAP_EXPANDED : GAP_COLLAPSED;

  return ReactDOM.createPortal(
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

      <Box
        data-ph-toast-region=""
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          position: 'fixed',
          bottom: 'max(16px, env(safe-area-inset-bottom, 0px))',
          right: 'max(16px, env(safe-area-inset-right, 0px))',
          left: 'auto',
          maxHeight: 'calc(100dvh - 32px)',
          maxWidth: 'min(380px, calc(100vw - 32px))',
          width: '100%',
          zIndex: 9999,
          pointerEvents: 'none',
          display: 'flex',
          flexDirection: 'column-reverse',
          gap,
          overflowY: 'auto',
          overflowX: 'hidden',
          alignItems: 'stretch',
          boxSizing: 'border-box',
          scrollbarWidth: 'thin',
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
                transformOrigin: 'bottom right',
                opacity,
                transition: 'transform 0.25s ease, opacity 0.25s ease',
                animation: toast.isExiting ? 'none' : 'toastSlideIn 0.3s ease',
              }}
            >
              <Toast toast={toast} onDismiss={removeToast} />
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
    </Theme>,
    document.body
  );
}
