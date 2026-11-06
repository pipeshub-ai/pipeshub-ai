'use client';

import React from 'react';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { PanelEmpty } from '../../../answer-panel/panel-empty';
import { ReferenceCard } from './citation-card';
import type { CitationMaps, CitationCallbacks } from './types';

interface CitationsTabProps {
  citationMaps: CitationMaps;
  callbacks?: CitationCallbacks;
  /** No space above the first card: the column beside a chat pads its own body. */
  flush?: boolean;
}

/**
 * Content for the "Citation" tab.
 *
 * Iterates over `citationsOrder` (keyed by chunkIndex) sorted numerically,
 * and renders a `ReferenceCard` for every individual citation.
 */
export function CitationsTab({ citationMaps, callbacks, flush = false }: CitationsTabProps) {
  const { t } = useTranslation();
  const { citationsOrder, citations } = citationMaps;
  const sortedEntries = Object.entries(citationsOrder)
    .sort(([a], [b]) => Number(a) - Number(b));

  if (sortedEntries.length === 0) {
    return (
      <PanelEmpty icon="format_quote" title={t('chat.answerPanel.citationsEmptyTitle')} body={t('chat.answerPanel.citationsEmptyBody')} />
    );
  }

  return (
    <Flex direction="column" gap="2" style={{ padding: flush ? '0 0 var(--space-4)' : 'var(--space-4) 0' }}>
      {sortedEntries.map(([_, citationId]) => {
        const citation = citations[citationId];
        if (!citation) return null;

        return (
          <ReferenceCard
            key={citationId}
            citation={citation}
            callbacks={callbacks}
            currentTab="citation"
            // reason is not yet available in data — will be populated when
            // the backend provides referenceData with citation-level reasons
          />
        );
      })}
    </Flex>
  );
}
