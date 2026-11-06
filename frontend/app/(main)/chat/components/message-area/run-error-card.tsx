'use client';

import React from 'react';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface RunErrorCardProps {
  /** `neutral`: the person stopped the run themselves. `error`: the run failed. */
  tone: 'neutral' | 'error';
  icon: string;
  title: string;
  /** Left out when there is nothing true to add. */
  body?: React.ReactNode;
  retryLabel?: string;
  onRetry?: () => void;
  role?: 'status' | 'alert';
  testId?: string;
  retryTestId?: string;
}

/**
 * A run that ended without its answer, in the answer's place: what happened,
 * what is kept, and Try again where it is allowed. A stop the person made is
 * neutral; only a real failure is drawn in red.
 */
export function RunErrorCard({
  tone,
  icon,
  title,
  body,
  retryLabel,
  onRetry,
  role = 'status',
  testId,
  retryTestId,
}: RunErrorCardProps) {
  const isError = tone === 'error';
  const fg = isError ? 'var(--red-11)' : 'var(--text-secondary)';
  return (
    <Flex
      align="start"
      gap="3"
      role={role}
      data-testid={testId}
      className="conversation-fade-in"
      style={{
        padding: 'calc(var(--space-3) + var(--space-1) / 2)',
        borderRadius: 'var(--radius-3)',
        border: `1px solid ${isError ? 'var(--red-6)' : 'var(--border-default)'}`,
        background: isError ? 'var(--red-2)' : 'var(--surface-raised)',
      }}
    >
      <Box aria-hidden style={{ display: 'flex', paddingTop: 'calc(var(--space-1) / 4)', flexShrink: 0 }}>
        <MaterialIcon name={icon} size={18} color={fg} />
      </Box>
      <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
        <Text weight="medium" style={{ fontSize: 'calc(var(--font-size-2) - var(--space-1) / 8)', color: fg }}>
          {title}
        </Text>
        {body ? (
          <Text
            style={{
              fontSize: 'calc(var(--font-size-1) + var(--space-1) / 8)',
              lineHeight: 'calc(var(--line-height-1) + var(--space-1) / 2)',
              color: 'var(--text-secondary)',
              overflowWrap: 'anywhere',
            }}
          >
            {body}
          </Text>
        ) : null}
      </Flex>
      {onRetry && retryLabel ? (
        <Button
          size="1"
          variant="soft"
          color={isError ? 'red' : 'gray'}
          data-testid={retryTestId}
          onClick={onRetry}
          style={{ flexShrink: 0, cursor: 'pointer', alignSelf: 'center' }}
        >
          <MaterialIcon name="refresh" size={14} color="currentColor" />
          {retryLabel}
        </Button>
      ) : null}
    </Flex>
  );
}
