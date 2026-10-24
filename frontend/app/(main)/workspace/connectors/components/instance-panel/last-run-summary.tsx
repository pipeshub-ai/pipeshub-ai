'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import type { ConnectorInstance, LastSyncSummary } from '../../types';

/** Counts worth a line, in the order they are shown; zero and missing counts are left out. */
const COUNT_KEYS = [
  'pagesSaved',
  'failed',
  'robotsSkipped',
  'noindexSkipped',
  'skippedMaxPages',
  'pruned',
  'sitemapUrls',
] as const satisfies readonly (keyof LastSyncSummary)[];

export type LastRunLine = { key: string; count?: number };

export function lastRunLines(summary: LastSyncSummary | null | undefined): LastRunLine[] {
  if (!summary) return [];
  const lines: LastRunLine[] = [];
  if (summary.stopReason) lines.push({ key: `stopReason.${summary.stopReason}` });
  if (summary.resumed) lines.push({ key: 'resumed' });
  for (const key of COUNT_KEYS) {
    const count = summary[key];
    if (typeof count === 'number' && count > 0) lines.push({ key, count });
  }
  return lines;
}

/** What the connector's last sync did, shown on its overview. */
export function LastRunSummary({ instance }: { instance: ConnectorInstance }) {
  const { t } = useTranslation();
  if (!instance.lastSyncOutcome) return null;
  const lines = lastRunLines(instance.lastSyncSummary);
  const failed = instance.lastSyncOutcome === 'failed';
  return (
    <Flex direction="column" gap="1">
      <Flex align="center" justify="between">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.connectors.overview.lastRun.title')}
        </Text>
        <Text size="1" style={{ color: failed ? 'var(--red-11)' : 'var(--gray-11)' }}>
          {t(`workspace.connectors.overview.lastRun.outcome.${instance.lastSyncOutcome}`)}
        </Text>
      </Flex>
      {lines.map((line) => (
        <Text key={line.key} size="1" style={{ color: 'var(--gray-11)' }}>
          {t(`workspace.connectors.overview.lastRun.${line.key}`, { count: line.count })}
        </Text>
      ))}
    </Flex>
  );
}
