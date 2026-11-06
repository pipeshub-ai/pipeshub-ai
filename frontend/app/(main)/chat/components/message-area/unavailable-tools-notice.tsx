'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { connectToolsPresentation, type UnavailableTool } from '../../unavailable-tools';
import { LogoStack } from '../answer-panel/logo-stack';
import { ConnectToolsCard } from './connect-tools-card';
import { toolMarks } from './tool-logo';

/** The chip and the read-only line lead with this many of the tools' logos. */
const CHIP_LOGOS = 3;

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
      <Flex data-testid="tools-unavailable-line" align="center" gap="2" mt="3">
        <LogoStack marks={toolMarks(items)} cap={CHIP_LOGOS} />
        <Text size="1" style={{ color: 'var(--text-secondary)' }}>
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
          variant="outline"
          color="gray"
          className="touch-size"
          onClick={() => setExpanded(true)}
          style={{ height: 'calc(var(--space-5) + var(--space-1))', gap: 'var(--space-2)' }}
        >
          <LogoStack marks={toolMarks(items)} cap={CHIP_LOGOS} />
          <Text style={{ color: 'var(--text-secondary)' }}>{t('chat.connectTools.chip')}</Text>
          <Text weight="medium" style={{ color: 'var(--emerald-11)' }}>
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
