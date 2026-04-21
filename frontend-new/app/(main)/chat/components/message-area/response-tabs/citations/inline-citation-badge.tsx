'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { CitationNumberCircle } from './citation-number-circle';
import type { CitationData, CitationCallbacks } from './types';

interface InlineCitationBadgeProps {
  /** The `[N]` number from the markdown text */
  chunkIndex: number;
  /** Full citation data — may be undefined while streaming before data arrives */
  citation?: CitationData;
  /** Interaction callbacks */
  callbacks?: CitationCallbacks;
}

/**
 * Small inline citation pill rendered inside the answer markdown.
 */
export function InlineCitationBadge({
  chunkIndex,
  citation,
  callbacks,
}: InlineCitationBadgeProps) {
  const [isHovered, setIsHovered] = useState(false);

  const connector = citation?.connector || '';

  // Strip file extension from recordName for display (e.g. "Report.pdf" → "Report")
  const fileNameWithoutExt = citation?.recordName
    ? citation.recordName.replace(/\.[^/.]+$/, '')
    : '';

  const truncatedName =
    fileNameWithoutExt.length > 24
      ? fileNameWithoutExt.slice(0, 24) + '…'
      : fileNameWithoutExt;

  // ── No citation data yet (early streaming) → simple numbered badge ──
  if (!citation) {
    return (
      <Flex
        as="span"
        align="center"
        justify="center"
        style={{
          display: 'inline-flex',
          backgroundColor: 'var(--slate-a3)',
          border: '1px solid var(--slate-a5)',
          padding: '2px var(--space-1)', /* was: 2px 6px, delta: -2px side */
          borderRadius: 'var(--radius-1)',
          verticalAlign: 'middle',
          marginLeft: 'var(--space-1)',
          marginRight: '2px',
          minWidth: '18px',
          height: 'var(--space-5)', /* was: 20px, delta: +4px */
        }}
      >
        <Text
          size="1"
          weight="medium"
          style={{ color: 'var(--accent-11)', lineHeight: 1, fontSize: 'var(--font-size-1)' /* was: 11px, delta: +1px */ }}
        >
          {chunkIndex}
        </Text>
      </Flex>
    );
  }

  // ── Full pill: connector icon + filename + numbered circle (matches group style) ──
  return (
    <Flex
      as="span"
      align="center"
      gap="1"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        display: 'inline-flex',
        background: isHovered ? 'var(--accent-3)' : 'var(--olive-2)',
        border: `0.667px solid ${isHovered ? 'var(--accent-8)' : 'var(--olive-3)'}`,
        padding: '2px var(--space-1)', /* was: 2px 6px, delta: -2px side */
        borderRadius: 'var(--radius-1)',
        verticalAlign: 'middle',
        marginLeft: 'var(--space-1)',
        marginRight: '2px',
        transition: 'all 0.15s ease',
        height: 'var(--space-5)', /* was: 20px, delta: +4px */
      }}
    >
      <ConnectorIcon type={connector} size={14} />

      {/* Source label — file name without extension, truncated to 24 chars */}
      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--accent-11)',
          lineHeight: 1,
          fontSize: 'var(--font-size-1)', /* was: 11px, delta: +1px */
          whiteSpace: 'nowrap',
        }}
      >
        {truncatedName}
      </Text>

      {/* Citation number in a nested mini-badge */}
      <Flex
        as="span"
        align="center"
        justify="center"
        style={{
          backgroundColor: 'transparent',
          borderRadius: 'var(--radius-1)', /* was: 2px, delta: +1px */
          padding: '0 var(--space-1)', /* was: 0 3px, delta: +1px side */
          minWidth: '14px',
          height: '14px',
        }}
      >
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--slate-10)',
            backgroundColor: 'transparent',
            lineHeight: 1,
            fontSize: 'var(--font-size-1)', /* was: 10px, delta: +2px */
          }}
        >
          +{chunkIndex}
        </Text>
      </Flex>
    </Flex>
  );
}
