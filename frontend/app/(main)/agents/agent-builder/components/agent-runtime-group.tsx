'use client';

import React, { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text } from '@radix-ui/themes';

/**
 * "How it runs" on the agent card (UX-07): the agent's model, coding engine, budget, websites and schedules,
 * together. Each part keeps its own save and its own server check; the group only labels them as one.
 */
export function AgentRuntimeGroup({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const ids = useId();
  return (
    <Box
      role="group"
      aria-labelledby={`${ids}-title`}
      aria-describedby={`${ids}-hint`}
      data-testid="agent-runtime-group"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        width: '100%',
        minWidth: 0,
        boxSizing: 'border-box',
        padding: 'var(--space-2)',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--agent-flow-node-border)',
        background: 'var(--gray-a2)',
      }}
    >
      <Flex direction="column" gap="1">
        <Text id={`${ids}-title`} size="2" weight="bold" style={{ color: 'var(--agent-flow-text)' }}>
          {t('agentBuilder.runtime.title')}
        </Text>
        <Text id={`${ids}-hint`} size="1" style={{ color: 'var(--agent-flow-text-muted)' }}>
          {t('agentBuilder.runtime.hint')}
        </Text>
      </Flex>
      {children}
    </Box>
  );
}
