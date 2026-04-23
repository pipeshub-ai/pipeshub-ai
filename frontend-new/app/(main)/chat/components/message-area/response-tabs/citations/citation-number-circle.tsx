'use client';

import React, { useState } from 'react';
import { Flex, Text, Popover } from '@radix-ui/themes';
import { CitationPopoverContent } from './citation-popover';
import { CITATION_POPOVER_WIDTH, CITATION_POPOVER_MAX_WIDTH } from './constants';
import type { CitationData, CitationCallbacks } from './types';
import {
  useCitationMessageRowKeyForInline,
  buildInlineCitationInstanceKey,
} from './citation-popover-control';
import { useInlineCitationPopoverStore } from './citation-popover-store';

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
  const messageRowKey = useCitationMessageRowKeyForInline();
  const useListControlled = Boolean(
    messageRowKey != null && String(messageRowKey).length > 0,
  );
  const instanceKey = useListControlled
    ? buildInlineCitationInstanceKey(messageRowKey!, chunkIndex)
    : null;
  // Subscribes to `activeKey` only; each badge re-renders when *its* open state flips.
  const isOpen = useInlineCitationPopoverStore(
    (s) =>
      useListControlled && instanceKey ? s.activeKey === instanceKey : false,
  );
  const setActiveKey = useInlineCitationPopoverStore((s) => s.setActiveKey);
  const handleListOpenChange = (open: boolean) => {
    if (!useListControlled || !instanceKey) return;
    if (open) {
      setActiveKey(instanceKey);
    } else if (useInlineCitationPopoverStore.getState().activeKey === instanceKey) {
      setActiveKey(null);
    }
  };

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
        // Short transition — long transitions feel laggy on first click
        transition:
          'background 0.08s ease, border-color 0.08s ease, color 0.08s ease, box-shadow 0.08s ease, transform 0.08s ease',
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
    <Popover.Root
      modal={false}
      {...(useListControlled && instanceKey
        ? { open: isOpen, onOpenChange: handleListOpenChange }
        : {})}
    >
      <Popover.Trigger
        type="button"
        style={{
          border: 'none',
          background: 'none',
          padding: 0,
          margin: 0,
          cursor: 'pointer',
          font: 'inherit',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          lineHeight: 0,
          verticalAlign: 'middle',
        }}
      >
        {circleElement}
      </Popover.Trigger>

      <Popover.Content
        side="top"
        sideOffset={6}
        align="start"
        onOpenAutoFocus={(e) => e.preventDefault()}
        style={{
          zIndex: 10_000,
          width: CITATION_POPOVER_WIDTH,
          maxWidth: CITATION_POPOVER_MAX_WIDTH,
          backgroundColor: 'var(--effects-translucent)',
          // Lighter blur than 16px — much cheaper to composite on open
          backdropFilter: 'blur(8px)',
          WebkitBackdropFilter: 'blur(8px)',
          border: '1px solid var(--olive-3)',
          boxShadow: '0 24px 52px 0 rgba(0, 0, 0, 0.12)',
          borderRadius: 'var(--radius-1)',
          animation: 'none',
          transition: 'none',
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
