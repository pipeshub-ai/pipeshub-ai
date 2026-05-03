'use client';

import React from 'react';
import { Flex, Text, Box } from '@radix-ui/themes';
import { ReferenceCard } from './citation-card';
import type { CitationMaps, CitationCallbacks } from './types';

interface CitationsTabProps {
  citationMaps: CitationMaps;
  callbacks?: CitationCallbacks;
}

/**
 * Content for the "Citation" tab.
 *
 * Iterates over `citationsOrder` (keyed by chunkIndex) sorted numerically,
 * and renders a `ReferenceCard` for every individual citation.
 */
export function CitationsTab({ citationMaps, callbacks }: CitationsTabProps) {
  const { citationsOrder, citations } = citationMaps;
  const sortedEntries = Object.entries(citationsOrder)
    .sort(([a], [b]) => Number(a) - Number(b));

  if (sortedEntries.length === 0) {
    return (
      <Box style={{ padding: 'var(--space-4) 0' }}>
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          No citations available.
        </Text>
      </Box>
    );
  }

  return (
    <Flex direction="column" gap="2" style={{ padding: 'var(--space-4) 0' }}>
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
