'use client';

import React, { useState } from 'react';
import { Flex, Text, Popover } from '@radix-ui/themes';
import { CitationPopoverContent } from './citation-popover';
import type { CitationData, CitationCallbacks } from './types';

interface CitationNumberCircleProps {
  /** The `[N]` number from the markdown text (used as the circle label) */
  chunkIndex: number;
  /** Full citation data — required to open the popover */
  citation: CitationData;
  /** Interaction callbacks (forwarded to popover) */
  callbacks?: CitationCallbacks;
}

/**
 * Compact circular numbered citation badge used inside an InlineCitationGroup.
 * Clicking/hovering opens the same CitationPopoverContent as the full badge.
 */
export function CitationNumberCircle({
  chunkIndex,
  citation,
  callbacks,
}: CitationNumberCircleProps) {
  const [isHovered, setIsHovered] = useState(false);

  const circleElement = (
    <Flex
      as="span"
      align="center"
      justify="center"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        display: 'inline-flex',
        minWidth: '16px',
        height: '16px',
        padding: '0 4px',
        borderRadius: '999px',
        background: isHovered ? 'var(--accent-9)' : 'var(--accent-3)',
        border: `1px solid ${isHovered ? 'var(--accent-9)' : 'var(--accent-a6)'}`,
        color: isHovered ? 'white' : 'var(--accent-11)',
        cursor: 'pointer',
        verticalAlign: 'middle',
        transition: 'all 0.15s ease',
        transform: isHovered ? 'translateY(-1px)' : 'translateY(0)',
        boxShadow: isHovered ? '0 2px 6px rgba(0, 0, 0, 0.12)' : 'none',
        lineHeight: 1,
        flexShrink: 0,
      }}
    >
      <Text
        size="1"
        weight="bold"
        style={{
          color: 'inherit',
          fontSize: '10px',
          lineHeight: 1,
        }}
      >
        {chunkIndex}
      </Text>
    </Flex>
  );

  return (
    <Popover.Root>
      <Popover.Trigger>
        <span>{circleElement}</span>
      </Popover.Trigger>

      <Popover.Content
        side="top"
        sideOffset={8}
        align="start"
        onOpenAutoFocus={(e) => e.preventDefault()}
        style={{
          width: '420px',
          maxWidth: '90vw',
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
