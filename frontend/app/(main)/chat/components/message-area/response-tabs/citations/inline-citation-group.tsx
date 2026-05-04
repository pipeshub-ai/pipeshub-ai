'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { CitationNumberCircle } from './citation-number-circle';
import type { CitationData, CitationCallbacks } from './types';

export interface InlineCitationGroupItem {
  /** The `[N]` number from the markdown text */
  chunkIndex: number;
  /** Per-occurrence key when the same number appears multiple times */
  occurrenceKey?: string;
  /** Full citation data for this marker */
  citation: CitationData;
}

interface InlineCitationGroupProps {
  /** Consecutive citations pointing at the same record (length >= 2) */
  items: InlineCitationGroupItem[];
  /** Interaction callbacks forwarded to each circle's popover */
  callbacks?: CitationCallbacks;
}

/**
 * Inline pill used when consecutive `[N]` markers all point at the same record:
 * shows the connector icon + filename once, followed by one compact numbered
 * circle per citation.
 */
export function InlineCitationGroup({ items, callbacks }: InlineCitationGroupProps) {
  const [isHovered, setIsHovered] = useState(false);

  const first = items[0]?.citation;
  if (!first) return null;

  const connector = first.connector || '';
  const fileNameWithoutExt = first.recordName
    ? first.recordName.replace(/\.[^/.]+$/, '')
    : '';
  
  const truncatedName =
  fileNameWithoutExt.length > 24
    ? fileNameWithoutExt.slice(0, 24) + '…'
    : fileNameWithoutExt;

  return (
    <Flex
      as="span"
      align="center"
      gap="1"
      wrap="wrap"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        display: 'inline-flex',
        background: isHovered ? 'var(--accent-3)' : 'var(--olive-2)',
        border: `0.667px solid ${isHovered ? 'var(--accent-8)' : 'var(--olive-3)'}`,
        padding: '2px var(--space-1)',
        borderRadius: 'var(--radius-1)',
        verticalAlign: 'middle',
        marginLeft: 'var(--space-1)',
        marginRight: '2px',
        transition: 'all 0.15s ease',
        minHeight: 'var(--space-5)',
        rowGap: '2px',
        columnGap: 'var(--space-1)',
      }}
    >
      <ConnectorIcon type={connector} size={14} />

      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--accent-11)',
          lineHeight: 1.25,
          fontSize: 'var(--font-size-1)',
          maxWidth: 'min(240px, 100%)',
          minWidth: 0,
          overflowWrap: 'anywhere',
          wordBreak: 'break-word',
        }}
      >
        {truncatedName}
      </Text>

      <Flex
        as="span"
        align="center"
        gap="1"
        wrap="wrap"
        style={{
          display: 'inline-flex',
          gap: '3px',
        }}
      >
        {items.map((item, idx) => (
          <CitationNumberCircle
            key={item.occurrenceKey ?? `cite-circle-${item.chunkIndex}-${idx}`}
            chunkIndex={item.chunkIndex}
            occurrenceKey={item.occurrenceKey}
            citation={item.citation}
            callbacks={callbacks}
          />
        ))}
      </Flex>
    </Flex>
  );
}
