'use client';

import React from 'react';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { PanelEmpty } from '../../../answer-panel/panel-empty';
import { ReferenceCard } from './citation-card';
import { getCitationCountBySource } from './utils';
import type { CitationMaps, CitationCallbacks } from './types';

interface SourcesTabProps {
  citationMaps: CitationMaps;
  callbacks?: CitationCallbacks;
  /** No space above the first card: the column beside a chat pads its own body. */
  flush?: boolean;
}

/**
 * Content for the "Sources" tab.
 *
 * Iterates over `sourcesOrder` (unique records in order of first appearance),
 * resolves each to its first citation, and renders a `ReferenceCard` with the
 * count of all citations referencing that record.
 */
export function SourcesTab({ citationMaps, callbacks, flush = false }: SourcesTabProps) {
  const { t } = useTranslation();
  const { sourcesOrder, sources, citations } = citationMaps;

  if (sourcesOrder.length === 0) {
    return (
      <PanelEmpty icon="description" title={t('chat.answerPanel.sourcesEmptyTitle')} body={t('chat.answerPanel.sourcesEmptyBody')} />
    );
  }

  // Pre-compute citation counts per source (recordId)
  const countsBySource = getCitationCountBySource(citationMaps);

  return (
    <Flex direction="column" gap="2" style={{ padding: flush ? '0 0 var(--space-4)' : 'var(--space-4) 0' }}>
      {sourcesOrder.map((recordId) => {
        const firstCitationId = sources[recordId];
        const citation = firstCitationId ? citations[firstCitationId] : undefined;
        if (!citation) return null;

        return (
          <ReferenceCard
            key={recordId}
            citation={citation}
            callbacks={callbacks}
            currentTab="sources"
            citationCount={countsBySource[recordId] ?? 0}
          />
        );
      })}
    </Flex>
  );
}
