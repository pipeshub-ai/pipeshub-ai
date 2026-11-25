'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ViewActivityButton } from '../activity-panel/activity-panel-host';
import type { ContextCompactedView, DurableAgentRunHandle, MessagePart } from '../../types';

const COMPACT_TOKENS = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 0 });

/** "148k": a token estimate as the row shows it. */
export const compactTokens = (value: number): string => COMPACT_TOKENS.format(value).toLowerCase();

/** What a `/compact` did at the start of a run (SLASH2 C2), as a saved status row on that run's answer. */
export function ContextCompactedNote({ compacted, run }: { compacted: ContextCompactedView; run?: DurableAgentRunHandle | null }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const parts: string[] = [];
  if (compacted.outcome === 'compacted') {
    parts.push(t('slashCommands.builtin.compact.row.compacted'));
    if (compacted.beforeTokens !== null && compacted.afterTokens !== null) {
      parts.push(
        t('slashCommands.builtin.compact.row.tokens', {
          before: compactTokens(compacted.beforeTokens),
          after: compactTokens(compacted.afterTokens),
        }),
      );
    }
    if (compacted.userMessagesKept) parts.push(t('slashCommands.builtin.compact.row.kept'));
  } else if (compacted.outcome === 'nothing_to_compact') {
    parts.push(t('slashCommands.builtin.compact.reason.nothing'));
  } else if (compacted.outcome === 'unsupported') {
    parts.push(t('slashCommands.builtin.compact.row.unsupported'));
  } else {
    parts.push(t('slashCommands.builtin.compact.row.failed'));
  }
  return (
    <Box
      role="status"
      data-testid="context-compacted-row"
      style={{ borderLeft: '2px solid var(--gray-a5)', padding: 'var(--space-1) 0 var(--space-1) var(--space-3)', marginBottom: 'var(--space-3)' }}
    >
      <Flex align="center" gap="2" wrap="wrap">
        <MaterialIcon name="compress" size={16} color="var(--olive-11)" />
        <Text size="1">{parts.join(' · ')}</Text>
        {run ? <ViewActivityButton run={run} isMobile={isMobile} /> : null}
      </Flex>
    </Box>
  );
}

/** The saved `/compact` row of one answer: its `context_compacted` part, stored with the answer and streamed live. */
export function ContextCompactedRow({ parts, run }: { parts: MessagePart[] | undefined; run?: DurableAgentRunHandle | null }) {
  const compacted = parts?.find((part) => part.type === 'context_compacted')?.compacted;
  return compacted ? <ContextCompactedNote compacted={compacted} run={run} /> : null;
}
