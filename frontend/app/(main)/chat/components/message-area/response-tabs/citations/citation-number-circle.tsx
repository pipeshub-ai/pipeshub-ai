'use client';

import React, { useRef, useState, useCallback, useLayoutEffect } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import type { CitationData, CitationCallbacks } from './types';
import {
  useCitationMessageRowKeyForInline,
  buildInlineCitationInstanceKey,
} from './citation-popover-control';
import { useInlineCitationPopoverStore } from './citation-popover-store';
import { getCitationCopyHref } from './utils';

interface CitationNumberCircleProps {
  /** The `[N]` number from the markdown text (used as the circle label) */
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
 * Compact circular numbered citation badge used inside an `InlineCitationBadge`
 * or an `InlineCitationGroup`.
 *
 * The circle is a `<button>` or, when `webUrl` is available, an `<a href>`
 * so the browser can offer **Copy link** on the context menu. A plain click
 * still toggles the single
 * `InlineCitationPopoverHost` (mounted once per `MessageList`), which anchors
 * to this circle via a Zustand store. Rendering the popover outside the
 * inline markdown tree guarantees at most one popover is ever visible, even
 * when multiple citation circles are on the page (this used to produce two
 * overlapping cards because each circle rendered its own `Popover.Root` and
 * Radix's `modal={false}` dismiss-layer treated other Radix triggers as safe
 * targets, leaving the previous popover briefly open).
 */
export function CitationNumberCircle({
  chunkIndex,
  occurrenceKey,
  citation,
  callbacks,
}: CitationNumberCircleProps) {
  const [isHovered, setIsHovered] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | HTMLAnchorElement | null>(null);

  const assignTriggerRef = useCallback((el: HTMLButtonElement | HTMLAnchorElement | null) => {
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
  // toggle the popover. The store's single-active-key invariant is what
  // prevents duplicate cards — not the row scoping itself.
  const instanceKey = buildInlineCitationInstanceKey(
    messageRowKey && messageRowKey.length > 0 ? messageRowKey : 'orphan',
    chunkIndex,
    occurrenceKey,
  );

  const isOpen = useInlineCitationPopoverStore((s) => s.activeKey === instanceKey);
  const openPopover = useInlineCitationPopoverStore((s) => s.open);
  const closePopover = useInlineCitationPopoverStore((s) => s.close);

  const copyHref = getCitationCopyHref(citation);

  // When this badge (re)mounts — e.g. because react-markdown recreated its
  // component tree after streamingCitationMaps updated — and it is still the
  // active popover target, update the store anchor to the fresh DOM element.
  // Without this, Floating UI positions against the old detached button whose
  // getBoundingClientRect() returns all zeros, placing the popover at (0,0).
  // copyHref is included because a change in its value swaps <button> for <a>
  // (or vice versa), replacing the DOM element triggerRef points at.
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
  }, [instanceKey, copyHref]);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>) => {
      // Modifier / non-primary clicks: keep native navigation for real links
      if (
        copyHref &&
        (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)
      ) {
        return;
      }
      // Prevent bubbling so we don't accidentally trigger the host's
      // outside-click handler in the same tick.
      e.preventDefault();
      e.stopPropagation();

      if (!triggerRef.current) return;
      const state = useInlineCitationPopoverStore.getState();
      if (state.activeKey === instanceKey) {
        closePopover(instanceKey);
      } else {
        openPopover({
          key: instanceKey,
          anchor: triggerRef.current,
          citation,
          callbacks,
        });
      }
    },
    [instanceKey, citation, callbacks, openPopover, closePopover, copyHref],
  );

  const circle = (
    <Flex
      as="span"
      align="center"
      justify="center"
      style={{
        display: 'inline-flex',
        minWidth: '16px',
        height: '16px',
        padding: '0 4px',
        borderRadius: '999px',
        background: isHovered || isOpen ? 'var(--accent-9)' : 'var(--accent-3)',
        border: `1px solid ${isHovered || isOpen ? 'var(--accent-9)' : 'var(--accent-a6)'}`,
        color: isHovered || isOpen ? 'white' : 'var(--accent-11)',
        cursor: 'pointer',
        verticalAlign: 'middle',
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

  const triggerStyle: React.CSSProperties = {
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
    textDecoration: 'none',
    color: 'inherit',
  };

  if (copyHref) {
    return (
      <a
        ref={assignTriggerRef}
        href={copyHref}
        rel="noopener noreferrer"
        onClick={handleClick}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        style={triggerStyle}
      >
        {circle}
      </a>
    );
  }

  return (
    <button
      ref={assignTriggerRef}
      type="button"
      onClick={handleClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      aria-haspopup="dialog"
      aria-expanded={isOpen}
      style={triggerStyle}
    >
      {circle}
    </button>
  );
}
