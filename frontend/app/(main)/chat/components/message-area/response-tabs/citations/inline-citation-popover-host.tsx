'use client';

import React, { useEffect } from 'react';
import { Popover } from '@radix-ui/themes';
import { CitationPopoverContent } from './citation-popover';
import {
  CITATION_POPOVER_WIDTH,
  CITATION_POPOVER_MAX_WIDTH,
  CITATION_POPOVER_Z_INDEX,
} from './constants';
import { useInlineCitationPopoverStore } from './citation-popover-store';

/**
 * Renders the **single** `Popover.Root` for every inline citation marker on
 * the page. It's driven purely by the Zustand store — there's exactly one
 * popover in the DOM at any time, regardless of how many citation circles the
 * conversation contains.
 *
 * Before this host existed, each `CitationNumberCircle` rendered its own
 * `Popover.Root`. With `modal={false}` Radix's dismiss layer could
 * occasionally leave the previous popover visible while a new one opened,
 * producing two stacked cards.
 */
export function InlineCitationPopoverHost() {
  const activeKey = useInlineCitationPopoverStore((s) => s.activeKey);
  const activeAnchor = useInlineCitationPopoverStore((s) => s.activeAnchor);
  const activeCitation = useInlineCitationPopoverStore((s) => s.activeCitation);
  const activeCallbacks = useInlineCitationPopoverStore((s) => s.activeCallbacks);
  const close = useInlineCitationPopoverStore((s) => s.close);

  const open = activeKey != null && activeAnchor != null && activeCitation != null;

  // Close on ESC.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(activeKey ?? undefined);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, activeKey, close]);

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

  if (!open || !activeCitation || !activeAnchor) return null;

  const handleOpenChange = (next: boolean) => {
    if (!next) close(activeKey ?? undefined);
  };

  const handlePreview = activeCallbacks?.onPreview
    ? () => activeCallbacks.onPreview?.(activeCitation)
    : undefined;
  const handleOpenInCollection = activeCallbacks?.onOpenInCollection
    ? () => activeCallbacks.onOpenInCollection?.(activeCitation)
    : undefined;

  return (
    <Popover.Root open modal={false} onOpenChange={handleOpenChange}>
      <Popover.Anchor virtualRef={{ current: activeAnchor }} />
      <Popover.Content
        side="top"
        sideOffset={6}
        align="start"
        onOpenAutoFocus={(e) => e.preventDefault()}
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
          backgroundColor: 'var(--effects-translucent)',
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
          citation={activeCitation}
          onPreview={handlePreview}
          onOpenInCollection={handleOpenInCollection}
        />
      </Popover.Content>
    </Popover.Root>
  );
}
