'use client';

import React from 'react';
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
            Authenticate {connectorName} to Proceed
          </Text>
          <Text size="1" style={{ color: 'var(--gray-10)' }}>
            Connect your {connectorName} account to proceed with configuration
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
          loadingLabel="Authenticating..."
          style={{ width: '100%' }}
        >
          {`Authenticate ${connectorName} to Proceed`}
        </LoadingButton>
      )}

      {state === 'success' && (
        <Badge
          size="2"
          style={{
            backgroundColor: 'var(--green-a3)',
            color: 'var(--green-a11)',
            width: 'fit-content',
            padding: '4px 8px',
          }}
        >
          {connectorName} has been Authenticated
        </Badge>
      )}

      {state === 'failed' && (
        <Flex align="center" gap="2">
          <Badge
            size="2"
            style={{
              backgroundColor: 'var(--red-a3)',
              color: 'var(--red-a11)',
              padding: '4px 8px',
            }}
          >
            Failed to Authenticate your {connectorName}
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
