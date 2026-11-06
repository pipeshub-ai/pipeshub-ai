'use client';

import React, { useRef, useState, useCallback, useLayoutEffect } from 'react';
import { useTranslation } from 'react-i18next';
import type { CitationData, CitationCallbacks } from './types';
import {
  useCitationMessageRowKeyForInline,
  buildInlineCitationInstanceKey,
} from './citation-popover-control';
import { useInlineCitationPopoverStore } from './citation-popover-store';

/** The citation pill's type: 12/16 medium, in the brand's text green. */
export const CITATION_CHIP_TEXT: React.CSSProperties = {
  fontSize: 'var(--font-size-1)',
  lineHeight: 'var(--line-height-1)',
  fontWeight: 500,
  color: 'var(--accent-11)',
};

/** A number chip: the marker's number, small and bold, in a round light brand chip with a hairline edge (16px tall). */
export const CITATION_NUMBER_CHIP: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  boxSizing: 'border-box',
  minWidth: 'var(--space-4)',
  height: 'var(--space-4)',
  paddingInline: 'var(--space-1)',
  borderRadius: 'var(--radius-thumb)',
  border: '1px solid var(--accent-a6)',
  background: 'var(--accent-3)',
  color: 'var(--accent-11)',
  fontSize: 'calc(var(--font-size-1) * 5 / 6)',
  fontWeight: 'var(--font-weight-bold)',
  lineHeight: 1,
  flexShrink: 0,
};

/** Chips in a group are at least 20 wide with a 4px gap, so their 24px hit areas never overlap. */
export const GROUP_CHIP_MIN_WIDTH = 'calc(var(--space-5) - var(--space-1))';

/**
 * Opens the one citation popover (`InlineCitationPopoverHost`, mounted once per
 * `MessageList`) anchored to this trigger. Rendering the popover outside the
 * inline markdown tree guarantees at most one is ever visible.
 */
export function useCitationPopoverTrigger({
  chunkIndex,
  occurrenceKey,
  citation,
  callbacks,
}: {
  chunkIndex: number;
  occurrenceKey?: string;
  citation: CitationData;
  callbacks?: CitationCallbacks;
}) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const assignTriggerRef = useCallback((el: HTMLButtonElement | null) => {
    triggerRef.current = el;
  }, []);

  // Refs so the re-anchor effect always has the latest values without
  // re-running on every prop change (citation/callbacks can change refs
  // frequently during streaming without changing their logical content).
  const citationRef = useRef(citation);
  citationRef.current = citation;
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const messageRowKey = useCitationMessageRowKeyForInline();
  // Always produce a stable instance key so read-only / archived views still
  // toggle the popover.
  const instanceKey = buildInlineCitationInstanceKey(
    messageRowKey && messageRowKey.length > 0 ? messageRowKey : 'orphan',
    chunkIndex,
    occurrenceKey,
  );

  const isOpen = useInlineCitationPopoverStore((s) => s.activeKey === instanceKey);
  const openPopover = useInlineCitationPopoverStore((s) => s.open);
  const closePopover = useInlineCitationPopoverStore((s) => s.close);

  // When this trigger (re)mounts — e.g. because react-markdown recreated its
  // component tree after streamingCitationMaps updated — and it is still the
  // active popover target, update the store anchor to the fresh DOM element.
  // Without this, Floating UI positions against a detached element and places
  // the popover at (0,0).
  useLayoutEffect(() => {
    const el = triggerRef.current;
    if (!el) return;
    const state = useInlineCitationPopoverStore.getState();
    if (state.activeKey === instanceKey && state.activeAnchor !== el) {
      state.open({
        key: instanceKey,
        anchor: el,
        citation: citationRef.current,
        callbacks: callbacksRef.current,
      });
    }
  }, [instanceKey]);

  const onClick = useCallback(
    (e: React.MouseEvent<HTMLButtonElement>) => {
      // Keep the host's outside-click handler from closing it in the same tick.
      e.preventDefault();
      e.stopPropagation();
      if (!triggerRef.current) return;
      const state = useInlineCitationPopoverStore.getState();
      if (state.activeKey === instanceKey) {
        closePopover(instanceKey);
      } else {
        openPopover({ key: instanceKey, anchor: triggerRef.current, citation, callbacks });
      }
    },
    [instanceKey, citation, callbacks, openPopover, closePopover],
  );

  return { ref: assignTriggerRef, isOpen, onClick };
}

interface CitationNumberCircleProps {
  /** The `[N]` number from the markdown text (the chip's label). */
  chunkIndex: number;
  /**
   * Stable id for this marker instance in the message (e.g. parser `${indexInText}-${chunkIndex}`).
   * Required for correct popover state when the same number appears more than once.
   */
  occurrenceKey?: string;
  /** Full citation data — required to open the popover */
  citation: CitationData;
  /** Interaction callbacks (forwarded to popover) */
  callbacks?: CitationCallbacks;
}

/**
 * One number in a group of citations to the same record: its own button,
 * named for the citation, opening that citation's preview.
 */
function CitationNumberCircleImpl({ chunkIndex, occurrenceKey, citation, callbacks }: CitationNumberCircleProps) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState(false);
  const { ref, isOpen, onClick } = useCitationPopoverTrigger({ chunkIndex, occurrenceKey, citation, callbacks });
  const name = citation.recordName ? citation.recordName.replace(/\.[^/.]+$/, '') : '';
  return (
    <button
      ref={ref}
      type="button"
      // `citation-pill` for its 24px hit area and focus ring; the chip paints itself.
      className="citation-pill citation-number-circle"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      aria-haspopup="dialog"
      aria-expanded={isOpen}
      aria-label={t('chat.citationPill.number', { number: chunkIndex, name })}
      style={{
        ...CITATION_NUMBER_CHIP,
        minWidth: GROUP_CHIP_MIN_WIDTH,
        margin: 0,
        cursor: 'pointer',
        fontFamily: 'inherit',
        background: isOpen || hovered ? 'var(--accent-4)' : CITATION_NUMBER_CHIP.background,
        // The open chip's edge, drawn inside it so the chip keeps its size.
        boxShadow: isOpen ? 'inset 0 0 0 1px var(--accent-8)' : 'none',
      }}
    >
      {chunkIndex}
    </button>
  );
}

/** Compares a citation's identity fields rather than object identity: the SSE
 * pipeline can hand back a differently-referenced but logically identical
 * `CitationData` across flushes. */
function citationNumberCirclePropsEqual(
  prev: CitationNumberCircleProps,
  next: CitationNumberCircleProps,
): boolean {
  return (
    prev.chunkIndex === next.chunkIndex &&
    prev.occurrenceKey === next.occurrenceKey &&
    prev.callbacks === next.callbacks &&
    prev.citation.citationId === next.citation.citationId &&
    prev.citation.recordName === next.citation.recordName &&
    prev.citation.webUrl === next.citation.webUrl
  );
}

export const CitationNumberCircle = React.memo(CitationNumberCircleImpl, citationNumberCirclePropsEqual);
