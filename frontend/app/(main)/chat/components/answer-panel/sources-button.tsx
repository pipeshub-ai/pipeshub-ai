'use client';

import { Button, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import type { CitationMaps } from '../message-area/response-tabs/citations';
import { getConnectorConfig } from '../message-area/response-tabs/citations/utils';
import { openSources, setReadingAnswer } from './answer-panel-store';
import { LogoStack } from './logo-stack';

/** The places an answer drew on, once each, in the order it first cited them. */
export function sourceConnectors({ sourcesOrder, sources, citations }: CitationMaps): string[] {
  const seen = new Set<string>();
  for (const recordId of sourcesOrder) {
    const connector = citations[sources[recordId]]?.connector;
    if (connector) seen.add(connector);
  }
  return Array.from(seen);
}

/**
 * An answer's Sources: the marks of the places it drew on, then the word, at the end of its actions. The one item
 * there that opens something: the column beside the chat, on its Sources tab. With `answerKey`, the column describes
 * this answer; without, the answer being read.
 */
export function SourcesButton({
  slotId,
  connectors,
  answerKey,
}: {
  slotId: string;
  connectors: readonly string[];
  answerKey?: string;
}) {
  const { t } = useTranslation();
  const names = connectors.map((c) => getConnectorConfig(c).label).join(', ');
  return (
    <Button
      size="1"
      variant="ghost"
      color="gray"
      data-testid="answer-sources"
      aria-label={t('chat.answerPanel.sourcesLabel', { names })}
      onClick={() => {
        if (answerKey) setReadingAnswer(slotId, answerKey);
        openSources(slotId);
      }}
      // In its own 24px box, as the actions beside it.
      className="ghost-box"
      style={{
        paddingInline: 'calc(var(--space-1) * 1.5)',
        gap: 'var(--space-2)',
        borderRadius: 'var(--radius-2)',
        cursor: 'pointer',
      }}
    >
      <LogoStack
        marks={connectors.map((connector) => ({
          key: connector,
          label: getConnectorConfig(connector).label,
          logo: <ConnectorIcon type={connector} size={12} />,
        }))}
      />
      <Text size="1" style={{ color: 'var(--text-secondary)' }}>
        {t('chat.answerPanel.sources')}
      </Text>
    </Button>
  );
}
