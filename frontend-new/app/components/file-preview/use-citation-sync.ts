'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import type { PreviewCitation } from './types';

interface UseCitationSyncOptions {
  /** All citations for the previewed record */
  citations?: PreviewCitation[];
  /** Current page number from the pagination state */
  currentPage: number;
  /** Callback to navigate to a page (sets pagination state) */
  onPageChange: (page: number) => void;
  /** Highlight bounding box from the initial citation click */
  initialHighlightBox?: Array<{ x: number; y: number }>;
  /** Initial page from the citation that opened the preview */
  initialPage?: number;
}

interface UseCitationSyncResult {
  /** Currently active citation ID (highlighted in the panel) */
  activeCitationId: string | null;
  /** Dynamic highlight bounding box (changes on citation click) */
  highlightBox: Array<{ x: number; y: number }> | undefined;
  /** Page number on which to show the highlight overlay */
  highlightPage: number | undefined;
  /** Handler for citation card clicks */
  handleCitationClick: (citation: PreviewCitation) => void;
}

/**
 * Bidirectional sync between the citations panel and the PDF page viewer.
 *
 * 1. **Page scroll → Citation sync** (debounced 300ms):
 *    When the user scrolls / navigates to a new page, finds the first citation
 *    on that page and marks it as active (the panel auto-scrolls to show it).
 *
 * 2. **Citation click → Page navigation + highlight**:
 *    When a citation card is clicked, navigates the PDF to that citation's page,
 *    shows a highlight overlay on the bounding box, and marks the citation active.
 */
export function useCitationSync({
  citations,
  currentPage,
  onPageChange,
  initialHighlightBox,
  initialPage,
}: UseCitationSyncOptions): UseCitationSyncResult {
  const [activeCitationId, setActiveCitationId] = useState<string | null>(null);
  const [activeHighlightBox, setActiveHighlightBox] = useState(initialHighlightBox);
  const [activeHighlightPage, setActiveHighlightPage] = useState(initialPage);

  // Prevents scroll-sync from firing while a citation click is navigating
  const isClickNavigating = useRef(false);

  // ── Page scroll → find matching citation (debounced 300ms) ──────────
  useEffect(() => {
    if (!citations?.length || isClickNavigating.current) return;

    const timer = setTimeout(() => {
      const match = citations.find((c) => c.pageNumbers?.includes(currentPage));
      if (match) {
        setActiveCitationId(match.id);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [currentPage, citations]);

  // ── Citation click → navigate + highlight ───────────────────────────
  const handleCitationClick = useCallback(
    (citation: PreviewCitation) => {
      setActiveCitationId(citation.id);

      const targetPage = citation.pageNumbers?.[0];
      if (targetPage) {
        isClickNavigating.current = true;

        // Spread to create a new array reference so React detects the change
        // even when clicking the same citation twice (after highlight faded)
        setActiveHighlightBox(
          citation.boundingBox ? [...citation.boundingBox] : undefined,
        );
        setActiveHighlightPage(targetPage);
        onPageChange(targetPage);

        // Allow the scroll animation to finish before re-enabling scroll sync
        setTimeout(() => {
          isClickNavigating.current = false;
        }, 600);
      }
      // For non-PDF citations (no pageNumbers), activeCitationId change alone
      // drives the text-based highlighting via useTextHighlighter in renderers
    },
    [onPageChange],
  );

  return {
    activeCitationId,
    highlightBox: activeHighlightBox,
    highlightPage: activeHighlightPage,
    handleCitationClick,
  };
}
