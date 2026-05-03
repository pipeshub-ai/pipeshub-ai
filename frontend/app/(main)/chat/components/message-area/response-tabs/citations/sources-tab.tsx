'use client';

import React from 'react';
import { Flex, Text, Box } from '@radix-ui/themes';
import { ReferenceCard } from './citation-card';
import { getCitationCountBySource } from './utils';
import type { CitationMaps, CitationCallbacks } from './types';

interface SourcesTabProps {
  citationMaps: CitationMaps;
  callbacks?: CitationCallbacks;
}

/**
 * Content for the "Sources" tab.
 *
 * Iterates over `sourcesOrder` (unique records in order of first appearance),
 * resolves each to its first citation, and renders a `ReferenceCard` with the
 * count of all citations referencing that record.
 */
export function SourcesTab({ citationMaps, callbacks }: SourcesTabProps) {
  const { sourcesOrder, sources, citations } = citationMaps;

  if (sourcesOrder.length === 0) {
    return (
      <Box style={{ padding: 'var(--space-4) 0' }}>
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          No sources available.
        </Text>
      </Box>
    );
  }

  // Pre-compute citation counts per source (recordId)
  const countsBySource = getCitationCountBySource(citationMaps);

  return (
    <Flex direction="column" gap="2" style={{ padding: 'var(--space-4) 0' }}>
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
