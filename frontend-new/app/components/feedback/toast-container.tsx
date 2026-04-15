'use client';

import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom';
import { Box, Text, Theme } from '@radix-ui/themes';
import { useToastStore, selectToasts, selectIsHovered } from '@/lib/store/toast-store';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { Toast } from './toast';

// ========================================
// Stack Configuration
// ========================================

const STACK_CONFIG = {
  maxVisible: 3,              // Max toasts visible in stack
  collapsedOffset: 8,         // Vertical offset between stacked toasts (collapsed)
  expandedGap: 12,            // Gap between toasts when expanded
  scaleDecrement: 0.05,       // Scale reduction per stack level
  opacityDecrement: 0.15,     // Opacity reduction per stack level
  toastHeight: 60,            // Approximate height of a toast
};

// ========================================
// Toast Container
// ========================================

export function ToastContainer() {
  const toasts = useToastStore(selectToasts);
  const isHovered = useToastStore(selectIsHovered);
  const { setHovered, removeToast } = useToastStore();
  // Read the current resolved appearance (light/dark) so the portal's Theme
  // wrapper stays in sync with the rest of the app.
  const { appearance } = useThemeAppearance();

  // Guard against SSR: createPortal needs document.body which only exists on the client.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  if (toasts.length === 0 || !mounted) {
    return null;
  }

  // Only show max visible toasts
  const visibleToasts = toasts.slice(0, STACK_CONFIG.maxVisible);
  const hiddenCount = Math.max(0, toasts.length - STACK_CONFIG.maxVisible);

  // Calculate container height
  const expandedHeight = visibleToasts.length * (STACK_CONFIG.toastHeight + STACK_CONFIG.expandedGap);
  const collapsedHeight = STACK_CONFIG.toastHeight + (visibleToasts.length - 1) * STACK_CONFIG.collapsedOffset;

  // Render into document.body via a portal so the toast escapes any parent
  // stacking context (e.g. the main layout's zIndex:0 Flex). Without this,
  // a fixed zIndex on the toast is capped by its ancestor's stacking context
  // and can't appear above portalled overlays like the share sidebar Dialog.
  //
  // The Theme wrapper re-injects Radix CSS variables (colors, radius, etc.)
  // because the portal renders outside the app's <Theme> tree.
  return ReactDOM.createPortal(
    <Theme accentColor="jade" grayColor="olive" appearance={appearance} radius="medium" data-accent-color="emerald">
      {/* Global keyframes for toast animations */}
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
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          position: 'fixed',
          bottom: '24px',
          right: '24px',
          // High zIndex ensures toasts always render above Radix Dialog overlays
          // (e.g. share sidebar). Effective because this node is in document.body,
          // not inside a capped stacking context.
          zIndex: 9999,
          pointerEvents: 'none',
        }}
      >
        {/* Stack Container */}
        <Box
          style={{
            position: 'relative',
            height: isHovered ? `${expandedHeight}px` : `${collapsedHeight}px`,
            transition: 'height 0.3s ease',
          }}
        >
          {visibleToasts.map((toast, index) => {
            // Calculate transform for stacking effect
            const isCollapsed = !isHovered;

            // In collapsed mode: stack behind with offset
            // In expanded mode: spread out vertically
            const translateY = isCollapsed
              ? index * STACK_CONFIG.collapsedOffset
              : index * (STACK_CONFIG.toastHeight + STACK_CONFIG.expandedGap);

            const scale = isCollapsed
              ? 1 - index * STACK_CONFIG.scaleDecrement
              : 1;

            const opacity = isCollapsed
              ? 1 - index * STACK_CONFIG.opacityDecrement
              : 1;

            return (
              <Box
                key={toast.id}
                style={{
                  position: 'absolute',
                  top: 0,
                  right: 0,
                  transform: `translateY(${translateY}px) scale(${scale})`,
                  transformOrigin: 'top right',
                  opacity,
                  zIndex: STACK_CONFIG.maxVisible - index,
                  pointerEvents: 'auto',
                  transition: 'transform 0.3s ease, opacity 0.3s ease',
                  animation: toast.isExiting ? 'none' : 'toastSlideIn 0.3s ease',
                }}
              >
                <Toast toast={toast} onDismiss={removeToast} />
              </Box>
            );
          })}
        </Box>

        {/* Hidden count indicator (when collapsed and more toasts exist) */}
        {hiddenCount > 0 && !isHovered && (
          <Box
            style={{
              position: 'absolute',
              bottom: '-24px',
              right: '0',
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
        )}
      </Box>
    </Theme>,
    document.body
  );
}
