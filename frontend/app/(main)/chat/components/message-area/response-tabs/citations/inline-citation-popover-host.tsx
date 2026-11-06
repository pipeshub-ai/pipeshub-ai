'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Popover } from '@radix-ui/themes';
import { CitationPopoverContent } from './citation-popover';
import {
  CITATION_POPOVER_WIDTH,
  CITATION_POPOVER_MAX_WIDTH,
  CITATION_POPOVER_Z_INDEX,
} from './constants';
import { useInlineCitationPopoverStore } from './citation-popover-store';
import type { CitationData, CitationCallbacks } from './types';

/**
 * Renders the **single** `Popover.Root` for every inline citation marker on
 * the page. It's driven purely by the Zustand store — there's exactly one
 * popover in the DOM at any time, regardless of how many citation circles the
 * conversation contains.
 *
 * Before this host existed, each citation trigger rendered its own
 * `Popover.Root`. With `modal={false}` Radix's dismiss layer could
 * occasionally leave the previous popover visible while a new one opened,
 * producing two stacked cards.
 */
/** The last citation shown, kept while the popover plays its closing motion. */
interface ShownCitation {
  key: string;
  anchor: HTMLElement;
  citation: CitationData;
  callbacks: CitationCallbacks | null;
}

export function InlineCitationPopoverHost() {
  const activeKey = useInlineCitationPopoverStore((s) => s.activeKey);
  const activeAnchor = useInlineCitationPopoverStore((s) => s.activeAnchor);
  const activeCitation = useInlineCitationPopoverStore((s) => s.activeCitation);
  const activeCallbacks = useInlineCitationPopoverStore((s) => s.activeCallbacks);
  const close = useInlineCitationPopoverStore((s) => s.close);
  const contentRef = useRef<HTMLDivElement | null>(null);

  const open = activeKey != null && activeAnchor != null && activeCitation != null;
  const [shown, setShown] = useState<ShownCitation | null>(null);
  useEffect(() => {
    if (open) setShown({ key: activeKey, anchor: activeAnchor, citation: activeCitation, callbacks: activeCallbacks });
  }, [open, activeKey, activeAnchor, activeCitation, activeCallbacks]);

  // Escape closes through Radix (`onOpenChange`); the popover then hands focus back to the
  // citation that opened it as it unmounts (`onCloseAutoFocus`), after its exit has played.
  const returnFocusRef = useRef(false);

  // The popover is portalled to the end of the page, so Tab from its citation
  // goes into it rather than past the rest of the answer; Tab out of its last
  // control comes back to the citation.
  useEffect(() => {
    if (!open || !activeAnchor) return undefined;
    const focusables = () =>
      Array.from(contentRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])') ?? []);
    const onAnchorKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || e.shiftKey) return;
      const first = focusables()[0];
      if (!first) return;
      e.preventDefault();
      first.focus();
    };
    const onContentKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const items = focusables();
      const index = items.indexOf(document.activeElement as HTMLElement);
      if ((e.shiftKey && index === 0) || (!e.shiftKey && index === items.length - 1)) {
        e.preventDefault();
        activeAnchor.focus();
      }
    };
    const content = contentRef.current;
    activeAnchor.addEventListener('keydown', onAnchorKey);
    content?.addEventListener('keydown', onContentKey);
    return () => {
      activeAnchor.removeEventListener('keydown', onAnchorKey);
      content?.removeEventListener('keydown', onContentKey);
    };
  }, [open, activeAnchor, shown]);

  // Close on outside click / tap. Using `pointerdown` so we dismiss before
  // the next trigger's click fires — avoids a double-toggle race.
  useEffect(() => {
    if (!open || !activeAnchor) return undefined;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (activeAnchor.contains(target)) return;
      let el: Node | null = target;
      while (el && el instanceof Element) {
        if (el.hasAttribute('data-radix-popper-content-wrapper')) return;
        el = el.parentNode;
      }
      close(activeKey ?? undefined);
    };
    window.addEventListener('pointerdown', onPointerDown, true);
    return () => window.removeEventListener('pointerdown', onPointerDown, true);
  }, [open, activeAnchor, activeKey, close]);

  // While open the store's values are current; once closed the last ones stay
  // so the popover can play its exit.
  const current: ShownCitation | null = open
    ? { key: activeKey, anchor: activeAnchor, citation: activeCitation, callbacks: activeCallbacks }
    : shown;
  if (!current || !current.anchor.isConnected) return null;

  const handleOpenChange = (next: boolean) => {
    if (!next) close(activeKey ?? undefined);
  };

  const callbacks = current.callbacks;
  const handlePreview = callbacks?.onPreview
    ? () => {
        callbacks.onPreview?.(current.citation);
        // Dismiss the popover so it doesn't stay open over the preview panel.
        close(current.key);
      }
    : undefined;
  const handleOpenInCollection = callbacks?.onOpenInCollection
    ? () => {
        callbacks.onOpenInCollection?.(current.citation);
        close(current.key);
      }
    : undefined;

  return (
    <Popover.Root open={open} modal={false} onOpenChange={handleOpenChange}>
      <Popover.Anchor virtualRef={{ current: current.anchor }} />
      <Popover.Content
        ref={contentRef}
        side="top"
        sideOffset={8}
        align="center"
        collisionPadding={16}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onEscapeKeyDown={() => {
          returnFocusRef.current = true;
        }}
        onCloseAutoFocus={(e) => {
          // There is no Radix trigger (the anchor is virtual), so the citation is focused by hand,
          // and only when the person closed it with Escape: a click elsewhere keeps its own focus.
          e.preventDefault();
          if (!returnFocusRef.current) return;
          returnFocusRef.current = false;
          if (current.anchor.isConnected) current.anchor.focus();
        }}
        onPointerDownOutside={(e) => {
          // We handle outside-click ourselves above — prevent Radix's
          // default dismiss so it doesn't interfere with our store-driven
          // single-popover invariant.
          e.preventDefault();
        }}
        style={{
          zIndex: CITATION_POPOVER_Z_INDEX,
          width: CITATION_POPOVER_WIDTH,
          maxWidth: CITATION_POPOVER_MAX_WIDTH,
          padding: 'var(--space-4)',
          backgroundColor: 'var(--effects-translucent)',
          backdropFilter: 'var(--frost-filter-subtle)',
          WebkitBackdropFilter: 'var(--frost-filter-subtle)',
          border: '1px solid var(--border-subtle)',
          boxShadow: 'var(--shadow-popover)',
          borderRadius: 'var(--radius-1)',
        }}
      >
        <CitationPopoverContent
          citation={current.citation}
          onPreview={handlePreview}
          onOpenInCollection={handleOpenInCollection}
        />
      </Popover.Content>
    </Popover.Root>
  );
}
