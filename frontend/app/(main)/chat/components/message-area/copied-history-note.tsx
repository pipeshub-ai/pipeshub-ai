'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** Where a fork's copied history ends: the pair index after which the note goes, or -1 when nothing was copied. */
export function lastCopiedPairIndex(pairs: ReadonlyArray<{ copied?: boolean }>): number {
  for (let i = pairs.length - 1; i >= 0; i -= 1) {
    if (pairs[i]?.copied === true) return i;
  }
  return -1;
}

const rule: React.CSSProperties = { flex: 1, minWidth: 'var(--space-4)', height: 1, background: 'var(--gray-a5)' };

/**
 * Under the last message a fork copied: those answers keep their text, sources and saved steps, but their runs
 * (activity, follow-ups, sub-task timelines) stay with the original conversation, so the chat offers none of them.
 */
export function CopiedHistoryNote() {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="3" role="note" data-testid="copied-history-note" style={{ marginTop: 'var(--space-5)' }}>
      <Box aria-hidden="true" style={rule} />
      <Flex align="center" gap="1" style={{ maxWidth: '80%' }}>
        <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}>
          <MaterialIcon name="call_split" size={14} color="var(--gray-11)" />
        </span>
        <Text size="1" color="gray" align="center">
          {t('chat.copiedHistory.note')}
        </Text>
      </Flex>
      <Box aria-hidden="true" style={rule} />
    </Flex>
  );
}
