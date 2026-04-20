'use client';

import React, { useState } from 'react';
import { Flex, Text, Popover } from '@radix-ui/themes';
import { CitationPopoverContent } from './citation-popover';
import { CITATION_POPOVER_WIDTH, CITATION_POPOVER_MAX_WIDTH } from './constants';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
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
          padding: '2px 6px',
          borderRadius: 'var(--radius-1)',
          verticalAlign: 'middle',
          marginLeft: '4px',
          marginRight: '2px',
          minWidth: '18px',
          height: '20px',
        }}
      >
        <Text
          size="1"
          weight="medium"
          style={{ color: 'var(--accent-11)', lineHeight: 1, fontSize: '11px' }}
        >
          {chunkIndex}
        </Text>
      </Flex>
    );
  }

  // ── Full badge with HoverCard ──
  const badgeElement = (
    <Flex
      as="span"
      align="center"
      gap="1"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        display: 'inline-flex',
        background : isHovered ? 'var(--accent-3)' : 'var(--olive-2)',
        border: `0.667px solid ${isHovered ? 'var(--accent-8)' : 'var(--olive-3)'}`,
        padding: '2px 6px',
        borderRadius: 'var(--radius-1)',
        cursor: 'pointer',
        verticalAlign: 'middle',
        marginLeft: '4px',
        marginRight: '2px',
        transition: 'all 0.15s ease',
        height: '20px',
      }}
    >
      {/* Connector icon */}
      <ConnectorIcon type={connector} size={14} />

      {/* Source label — file name without extension */}
      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--accent-11)',
          lineHeight: 2,
          fontSize: '11px',
          whiteSpace: 'nowrap',
          maxWidth: '300px',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {fileNameWithoutExt}
      </Text>

      {/* Citation number in a nested mini-badge */}
      <Flex
        as="span"
        align="center"
        justify="center"
        style={{
          backgroundColor: 'transparent',
          borderRadius: '2px',
          padding: '0 3px',
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
            fontSize: '10px',
          }}
        >
          +{chunkIndex}
        </Text>
      </Flex>
    </Flex>
  );

  return (
    <Popover.Root>
      <Popover.Trigger>
        <span>{badgeElement}</span>
      </Popover.Trigger>

      <Popover.Content
        side="top"
        sideOffset={8}
        align="start"
        onOpenAutoFocus={(e) => e.preventDefault()}
        style={{
          width: CITATION_POPOVER_WIDTH,
          maxWidth: CITATION_POPOVER_MAX_WIDTH,
          backgroundColor: 'var(--effects-translucent)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          border: '1px solid var(--olive-3)',
          boxShadow: '0 24px 52px 0 rgba(0, 0, 0, 0.12)',
          borderRadius: 'var(--radius-1)',
        }}
      >
        <CitationPopoverContent
          citation={citation}
          onPreview={callbacks?.onPreview}
          onOpenInCollection={callbacks?.onOpenInCollection}
        />
      </Popover.Content>
    </Popover.Root>
  );
}
