'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { connectToolsPresentation, type UnavailableTool } from '../../unavailable-tools';
import { ConnectToolsCard } from './connect-tools-card';

interface UnavailableToolsNoticeProps {
  items: UnavailableTool[];
  /** The answer text: when it names one of the tools, the card opens at once. */
  answer: string;
  question: string;
  agentId?: string;
  /**
   * Set when the viewer did not ask this question: they see which tools the agent could not use, never a way to
   * connect them for someone else's turn.
   */
  readOnlyFor?: { agentName: string | null };
}

/** Under an answer that went on without some tools: a quiet chip, or the Connect card when the answer needed them. */
export function UnavailableToolsNotice({ items, answer, question, agentId, readOnlyFor }: UnavailableToolsNoticeProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(() => connectToolsPresentation(answer, items) === 'card');

  if (items.length === 0) return null;
  const names = items.map((item) => item.displayName).join(', ');

  if (readOnlyFor) {
    return (
      <Flex data-testid="tools-unavailable-line" align="center" gap="1" mt="3">
        <MaterialIcon name="link_off" size={14} color="var(--slate-10)" />
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {t('chat.connectTools.couldNotUse', {
            agent: readOnlyFor.agentName || t('chat.connectTools.defaultAgent'),
            tools: names,
          })}
        </Text>
      </Flex>
    );
  }

  if (!expanded) {
    return (
      <Box mt="3">
        <Button
          data-testid="tools-unavailable-chip"
          size="1"
          variant="soft"
          color="gray"
          onClick={() => setExpanded(true)}
          style={{ minHeight: 32 }}
        >
          <MaterialIcon name="link_off" size={14} color="var(--amber-11)" />
          {t('chat.connectTools.chip')}
          <Text weight="medium" style={{ color: 'var(--accent-11)' }}>
            · {t('chat.connectTools.chipAction')}
          </Text>
        </Button>
      </Box>
    );
  }

  return (
    <ConnectToolsCard items={items} agentId={agentId} serviceAccount={false} question={question} blocked={false} />
  );
}
