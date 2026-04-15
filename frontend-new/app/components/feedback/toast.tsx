'use client';

import React, { useState } from 'react';
import { Flex, Box, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { LapTimerIcon } from '@/app/components/ui/lap-timer-icon';
import type { Toast as ToastType, ToastVariant } from '@/lib/store/toast-store';

// ========================================
// Toast Icon Configuration
// ========================================

interface VariantConfig {
  icon: string;
  iconColor: string;
  iconBgColor: string;
}

const VARIANT_CONFIG: Record<ToastVariant, VariantConfig> = {
  loading: {
    icon: 'timer',
    iconColor: 'var(--slate-11)',
    iconBgColor: 'rgba(0, 0, 85, 0.02)',
  },
  success: {
    icon: 'check',
    iconColor: 'var(--accent-11)',
    iconBgColor: 'var(--accent-a2)',
  },
  error: {
    icon: 'error_outline',
    iconColor: 'var(--red-10)',
    iconBgColor: 'var(--olive-3)',
  },
  info: {
    icon: 'info',
    iconColor: 'var(--slate-11)',
    iconBgColor: 'var(--olive-3)',
  },
  warning: {
    icon: 'warning',
    iconColor: 'var(--orange-9)',
    iconBgColor: 'var(--olive-3)',
  },
};

// ========================================
// Toast Component Props
// ========================================

interface ToastProps {
  toast: ToastType;
  onDismiss: (id: string) => void;
  style?: React.CSSProperties;
}

export function Toast({ toast, onDismiss, style }: ToastProps) {
  const [isHovered, setIsHovered] = useState(false);
  const config = VARIANT_CONFIG[toast.variant];

  // Use custom icon if provided, otherwise use variant default
  const iconName = toast.icon || config.icon;
  const isLoading = toast.variant === 'loading';

  return (
    <Box
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '340px',
        background: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        boxShadow: '0 12px 32px -16px var(--slate-a5, rgba(217, 237, 254, 0.15)), 0 12px 60px 0 var(--Black--a3, rgba(0, 0, 0, 0.15))',
        padding: 'var(--space-3)',
        opacity: toast.isExiting ? 0 : 1,
        transform: toast.isExiting ? 'translateX(100%)' : 'translateX(0)',
        transition: 'opacity 0.3s ease, transform 0.3s ease',
        ...style,
      }}
    >
      <Flex align="start" gap="2">
        {/* Icon */}
        <Box
          style={{
            width: '24px',
            height: '24px',
            minWidth: '24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: config.iconBgColor,
            borderRadius: 'var(--radius-2)',
          }}
        >
          {isLoading && !toast.icon ? (
            <LottieLoader variant="loader" size={16} />
          ) : toast.icon === 'lap_timer' ? (
            <LapTimerIcon size={20} color={config.iconColor} />
          ) : (
            <MaterialIcon
              name={iconName}
              size={16}
              color={config.iconColor}
            />
          )}
        </Box>

        {/* Content */}
        <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
          <Text
            size="2"
            weight="medium"
            style={{
              color: 'var(--slate-12)',
              lineHeight: '20px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {toast.title}
          </Text>

          {toast.description && (
            <Text
              size="1"
              style={{
                color: 'var(--slate-11)',
                lineHeight: '16px',
                fontWeight: 300,
                letterSpacing: '0.04px',
              }}
            >
              {toast.description}
            </Text>
          )}

          {/* Action Button */}
          {toast.action && (
            <Box style={{ marginTop: '8px' }}>
              <Flex
                align="center"
                justify="center"
                gap="1"
                asChild
              >
                <button
                  onClick={toast.action.onClick}
                  style={{
                    height: '24px',
                    padding: '0 8px',
                    border: '1px solid rgba(0, 6, 46, 0.2)',
                    borderRadius: '3px',
                    backgroundColor: 'transparent',
                    cursor: 'pointer',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                  }}
                >
                  {toast.action.icon && (
                    <MaterialIcon
                      name={toast.action.icon}
                      size={16}
                      color="var(--slate-11)"
                    />
                  )}
                  <Text
                    size="1"
                    weight="medium"
                    style={{
                      color: 'var(--slate-11)',
                      lineHeight: '16px',
                      letterSpacing: '0.04px',
                    }}
                  >
                    {toast.action.label}
                  </Text>
                </button>
              </Flex>
            </Box>
          )}
        </Flex>

        {/* Close Button */}
        {toast.showCloseButton && (
          <IconButton
            variant="ghost"
            color="gray"
            size="1"
            onClick={() => onDismiss(toast.id)}
            style={{
              opacity: isHovered ? 1 : 0.6,
              transition: 'opacity 0.15s ease',
              cursor: 'pointer',
            }}
          >
            <MaterialIcon name="close" size={18} color="var(--slate-11)" />
          </IconButton>
        )}
      </Flex>
    </Box>
  );
}
