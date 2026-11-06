'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Card, Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { LapTimerIcon } from '@/app/components/ui/lap-timer-icon';
import type { Toast as ToastType, ToastVariant } from '@/lib/store/toast-store';
import { getToastRenderDescription } from '@/lib/store/toast-store';
import { Link } from '@/lib/navigation';

// ========================================
// Toast Icon Configuration
// ========================================

interface VariantConfig {
  icon: string;
  /** The glyph tile is a soft Radix Badge: accent for success, red for an error, gray otherwise. */
  badgeColor: 'gray' | 'red' | 'orange' | undefined;
}

const VARIANT_CONFIG: Record<ToastVariant, VariantConfig> = {
  loading: { icon: 'timer', badgeColor: 'gray' },
  success: { icon: 'check', badgeColor: undefined },
  error: { icon: 'error_outline', badgeColor: 'red' },
  info: { icon: 'info', badgeColor: 'gray' },
  warning: { icon: 'warning', badgeColor: 'orange' },
};

/** 360px: five of the largest space step plus one step-7, the toast width used across the product. */
export const TOAST_WIDTH = 'calc(var(--space-9) * 5 + var(--space-7))';

// ========================================
// Toast Component Props
// ========================================

interface ToastProps {
  toast: ToastType;
  onDismiss: (id: string) => void;
  style?: React.CSSProperties;
}

/** Route wheel events to this pane; the toast host does not scroll. */
function handleDescriptionWheel(event: React.WheelEvent<HTMLDivElement>) {
  event.stopPropagation();
}

export function Toast({ toast, onDismiss, style }: ToastProps) {
  const { t } = useTranslation();
  const config = VARIANT_CONFIG[toast.variant];

  const iconName = toast.icon || config.icon;
  const isLoading = toast.variant === 'loading';
  const renderDescription = getToastRenderDescription(toast.id);
  const hasDescription = !!(renderDescription || toast.description);

  return (
    <Card
      size="1"
      style={{
        width: TOAST_WIDTH,
        maxWidth: 'calc(100vw - 2 * var(--space-4))',
        // Never taller than the screen less --space-9 and --space-6 (96px) of edge.
        maxHeight: 'min(72dvh, calc(100dvh - var(--space-9) - var(--space-6)))',
        boxSizing: 'border-box',
        boxShadow: 'var(--shadow-5)',
        opacity: toast.isExiting ? 0 : 1,
        transform: toast.isExiting ? 'translateX(var(--space-5))' : 'none',
        transition:
          'opacity var(--motion-surface) var(--motion-ease), transform var(--motion-surface) var(--motion-ease)',
        display: 'flex',
        flexDirection: 'column',
        ...style,
      }}
    >
      <Flex align="start" gap="2" style={{ minWidth: 0 }}>
        {/* Glyph tile: decorative, the title says what happened. */}
        <Badge
          size="1"
          variant="soft"
          color={config.badgeColor}
          aria-hidden="true"
          style={{ padding: 'var(--space-1)', flexShrink: 0 }}
        >
          {isLoading && !toast.icon ? (
            <LottieLoader variant="loader" size={16} decorative />
          ) : toast.icon === 'lap_timer' ? (
            <LapTimerIcon size={16} color="currentColor" />
          ) : (
            <MaterialIcon
              name={iconName}
              size={16}
              weight={toast.variant === 'success' ? 700 : 400}
              color="currentColor"
            />
          )}
        </Badge>

        {/* Content */}
        <Flex direction="column" align="start" gap="1" style={{ flex: 1, minWidth: 0 }}>
          <Text
            size="2"
            weight="medium"
            style={{
              color: 'var(--text-primary)',
              whiteSpace: 'normal',
              overflowWrap: 'anywhere',
              wordBreak: 'break-word',
            }}
          >
            {toast.title}
          </Text>

          {hasDescription && (
            <Box
              role="region"
              aria-label={toast.title}
              tabIndex={0}
              onWheel={handleDescriptionWheel}
              style={{
                // A long description scrolls past 224px (three --space-9 plus --space-6).
                maxHeight: 'min(36dvh, calc(var(--space-9) * 3 + var(--space-6)))',
                overflowY: 'auto',
                overflowX: 'hidden',
                width: '100%',
                WebkitOverflowScrolling: 'touch',
                overscrollBehavior: 'contain',
                touchAction: 'pan-y',
              }}
            >
              {renderDescription ? (
                renderDescription()
              ) : (
                // Block-level, so the line box is the size-1 line height (16px), not the body's 24px.
                <Text
                  as="p"
                  size="1"
                  weight="light"
                  style={{
                    color: 'var(--text-secondary)',
                    overflowWrap: 'anywhere',
                    wordBreak: 'break-word',
                    whiteSpace: 'pre-line',
                  }}
                >
                  {toast.description}
                </Text>
              )}
            </Box>
          )}

          {toast.action && (
            toast.action.href ? (
              <Flex align="center" gap="1" mt="1">
                {toast.action.icon && (
                  <MaterialIcon name={toast.action.icon} size={14} color="var(--accent-11)" />
                )}
                <Text size="1" weight="medium" asChild>
                  <Link
                    href={toast.action.href}
                    target={toast.action.openInNewTab ? '_blank' : undefined}
                    rel={toast.action.openInNewTab ? 'noopener noreferrer' : undefined}
                    style={{
                      color: 'var(--accent-11)',
                      textDecoration: 'underline',
                      textUnderlineOffset: 'calc(var(--space-1) / 2)',
                      cursor: 'pointer',
                    }}
                  >
                    {toast.action.label}
                  </Link>
                </Text>
                {toast.action.openInNewTab && (
                  <MaterialIcon name="open_in_new" size={14} color="var(--accent-11)" />
                )}
              </Flex>
            ) : (
              // The toast's one control: the house secondary (bordered gray), not accent text.
              <Button
                type="button"
                size="1"
                variant="outline"
                color="gray"
                mt="2"
                // The action is the toast's last word: run it, then close the toast, so a toast
                // waiting behind it comes up at once. Closing is a no-op when onClick already did.
                onClick={() => {
                  toast.action?.onClick?.();
                  onDismiss(toast.id);
                }}
              >
                {toast.action.icon && <MaterialIcon name={toast.action.icon} size={14} />}
                {toast.action.label}
              </Button>
            )
          )}
        </Flex>

        {toast.showCloseButton && (
          <IconButton
            type="button"
            variant="ghost"
            color="gray"
            size="1"
            // 24px here; a 44px tap area on touch screens, without growing the button.
            className="touch-hit"
            aria-label={t('common.close')}
            onClick={(e) => {
              e.stopPropagation();
              onDismiss(toast.id);
            }}
            style={{ flexShrink: 0 }}
          >
            <span aria-hidden="true" style={{ display: 'contents' }}>
              <MaterialIcon name="close" size={16} />
            </span>
          </IconButton>
        )}
      </Flex>
    </Card>
  );
}
