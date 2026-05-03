'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Button, Badge, IconButton } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { AuthCardState } from '../types';

// ========================================
// Types
// ========================================

interface AuthCardProps {
  state: AuthCardState;
  connectorName: string;
  onAuthenticate: () => void;
  onRetry?: () => void;
  loading?: boolean;
  /** When true, omit outer shell (use inside a parent olive card). */
  embedded?: boolean;
}

// ========================================
// State configuration
// ========================================

const stateConfig: Record<
  AuthCardState,
  { icon: string; iconBg: string; iconColor: string }
> = {
  empty: {
    icon: 'shield',
    iconBg: 'var(--olive-3)',
    iconColor: 'var(--gray-11)',
  },
  success: {
    icon: 'check',
    iconBg: 'var(--green-3)',
    iconColor: 'var(--green-11)',
  },
  failed: {
    icon: 'error_outline',
    iconBg: 'var(--red-3)',
    iconColor: 'var(--red-11)',
  },
};

// ========================================
// Component
// ========================================

export function AuthCard({
  state,
  connectorName,
  onAuthenticate,
  onRetry,
  loading = false,
  embedded = false,
}: AuthCardProps) {
  const { t } = useTranslation();
  const config = stateConfig[state];

  return (
    <Flex
      direction="column"
      gap="4"
      style={{
        width: '100%',
        ...(embedded
          ? {
              padding: 0,
              backgroundColor: 'transparent',
              border: 'none',
            }
          : {
              padding: 16,
              backgroundColor: 'var(--olive-2)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-2)',
            }),
      }}
    >
      {/* Icon + text */}
      <Flex direction="column" gap="3">
        {/* Icon badge */}
        <Flex
          align="center"
          justify="center"
          style={{
            width: 24,
            height: 24,
            borderRadius: 'var(--radius-2)',
            backgroundColor: config.iconBg,
            flexShrink: 0,
          }}
        >
          <MaterialIcon name={config.icon} size={16} color={config.iconColor} />
        </Flex>

        {/* Title + subtitle */}
        <Flex direction="column" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.connectors.authCard.title', { name: connectorName })}
          </Text>
          <Text size="1" style={{ color: 'var(--gray-10)' }}>
            {t('workspace.connectors.authCard.description', { name: connectorName })}
          </Text>
        </Flex>
      </Flex>

      {/* Action area — varies by state */}
      {state === 'empty' && (
        <LoadingButton
          variant="solid"
          size="2"
          onClick={onAuthenticate}
          loading={loading}
          loadingLabel={t('workspace.connectors.authCard.loading')}
          style={{ width: '100%' }}
        >
          {t('workspace.connectors.authCard.title', { name: connectorName })}
        </LoadingButton>
      )}

      {state === 'success' && (
        <Badge
          size="2"
          style={{
            backgroundColor: 'var(--green-a3)',
            color: 'var(--green-a11)',
            width: 'fit-content',
            padding: 'var(--space-1) var(--space-2)',
          }}
        >
          {t('workspace.connectors.authCard.successBadge', { name: connectorName })}
        </Badge>
      )}

      {state === 'failed' && (
        <Flex align="center" gap="2">
          <Badge
            size="2"
            style={{
              backgroundColor: 'var(--red-a3)',
              color: 'var(--red-a11)',
              padding: 'var(--space-1) var(--space-2)',
            }}
          >
            {t('workspace.connectors.authCard.failureBadge', { name: connectorName })}
          </Badge>
          {onRetry && (
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              onClick={onRetry}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="replay" size={16} color="var(--gray-11)" />
            </IconButton>
          )}
        </Flex>
      )}
    </Flex>
  );
}

export type { AuthCardProps };
