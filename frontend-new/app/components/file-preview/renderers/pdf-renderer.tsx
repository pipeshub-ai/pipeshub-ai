'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  PdfLoader,
  PdfHighlighter,
  Highlight,
  AreaHighlight,
  Popup,
} from 'react-pdf-highlighter';
import type { IHighlight } from 'react-pdf-highlighter';
import type { PDFRendererProps, PreviewCitation } from '../types';

// Constants matching the demo
const PDF_PAGE_WIDTH = 967;
const PDF_PAGE_HEIGHT = 747.2272727272727;
const SCROLL_DELAY_MS = 300;

/**
 * Small component that reports page count via useEffect,
 * avoiding the "setState during render" error that occurs
 * when calling onTotalPagesDetected inside PdfLoader's render callback.
 */
function PageCountReporter({
  numPages,
  onReport,
}: {
  numPages: number;
  onReport: (n: number) => void;
}) {
  useEffect(() => {
    onReport(numPages);
  }, [numPages, onReport]);
  return null;
}

/**
 * Convert a PreviewCitation into an IHighlight for react-pdf-highlighter.
 * Follows the demo's processHighlight logic.
 */
function citationToHighlight(citation: PreviewCitation): IHighlight | null {
  const pageNumber = citation.pageNumbers?.[0];
  if (!pageNumber || pageNumber <= 0) return null;

  const boundingBox = citation.boundingBox;

  // If no valid bounding box, create a page-level highlight
  if (!boundingBox || boundingBox.length < 4) {
    const pageTopRect = {
      x1: 0,
      y1: 0,
      x2: PDF_PAGE_WIDTH,
      y2: PDF_PAGE_HEIGHT,
      width: PDF_PAGE_WIDTH,
      height: PDF_PAGE_HEIGHT,
      pageNumber,
    };
    return {
      id: citation.id,
      content: { text: citation.content || '' },
      position: {
        boundingRect: pageTopRect,
        rects: [pageTopRect],
        pageNumber,
      },
      comment: { text: '', emoji: '' },
    };
  }

  // Convert normalized 0-1 coordinates to absolute positions
  const mainRect = {
    x1: boundingBox[0].x * PDF_PAGE_WIDTH,
    y1: boundingBox[0].y * PDF_PAGE_HEIGHT,
    x2: boundingBox[2].x * PDF_PAGE_WIDTH,
    y2: boundingBox[2].y * PDF_PAGE_HEIGHT,
    width: PDF_PAGE_WIDTH,
    height: PDF_PAGE_HEIGHT,
    pageNumber,
  };

  return {
    id: citation.id,
    content: { text: citation.content || '' },
    position: {
      boundingRect: mainRect,
      rects: [mainRect],
      pageNumber,
    },
    comment: { text: '', emoji: '' },
  };
}

/**
 * PDF renderer using react-pdf-highlighter for native highlight support.
 *
 * Renders the PDF via pdfjs with text layer and highlight overlays.
 * Citation highlights are positioned using bounding box coordinates,
 * and the active citation is scrolled into view.
 *
 * Page tracking and navigation work by accessing the internal PDFViewer
 * exposed by react-pdf-highlighter via window.PdfViewer.
 */
export function PDFRenderer({
  fileUrl,
  fileName,
  pagination,
  citations,
  activeCitationId,
  onHighlightClick,
}: PDFRendererProps) {
  const scrollViewerTo = useRef<(highlight: IHighlight) => void>(() => {});
  const [selectedHighlightId, setSelectedHighlightId] = useState<string | null>(null);

  // Stable ref to latest pagination callbacks — avoids effects re-running on every render
  const paginationRef = useRef(pagination);
  useEffect(() => { paginationRef.current = pagination; });

  // Page tracking refs
  const lastReportedPage = useRef<number>(pagination?.currentPage ?? 1);
  const isNavigating = useRef(false);
  const citationScrollPending = useRef(false);
  // Set to true when scrollRef fires (i.e. PDFViewer "pagesinit" is done).
  const isViewerReady = useRef(false);
  // Holds a citation highlight to scroll to once the viewer becomes ready.
  const pendingCitationScroll = useRef<IHighlight | null>(null);

  // Convert citations to highlights
  const highlights = useMemo(() => {
    if (!citations?.length) return [];
    return citations
      .map(citationToHighlight)
      .filter((h): h is IHighlight => h !== null);
  }, [citations]);

  // Build a synthetic highlight for jumping to the top of a given page.
  // PdfHighlighter's scrollTo() uses destArray-based navigation which works
  // even for lazy-rendered pages (unlike scrollPageIntoView with no destArray).
  const pageJumpHighlight = useCallback((pageNumber: number): IHighlight => ({
    id: `__pg_${pageNumber}`,
    content: { text: '' },
    position: {
      boundingRect: { x1: 0, y1: 0, x2: 100, y2: 20, width: PDF_PAGE_WIDTH, height: PDF_PAGE_HEIGHT, pageNumber },
      rects: [],
      pageNumber,
    },
    comment: { text: '', emoji: '' },
  }), []);

  // Inject custom highlight CSS (matching demo styling)
  useEffect(() => {
    const style = document.createElement('style');
    style.textContent = `
      .Highlight__part {
        cursor: pointer;
        position: absolute;
        background: rgba(139, 250, 209, 0.2);
        transition: background 0.3s;
      }

      .Highlight--scrolledTo .Highlight__part {
        background: rgba(139, 250, 209, 0.4);
        position: relative;
      }

      .Highlight--scrolledTo .Highlight__part::before {
        content: '';
        position: absolute;
        top: -2px;
        left: -16px;
        bottom: -2px;
        height: calc(100% + 4px);
        width: 8px;
        border-left: 3px solid #006400;
        border-top: 3px solid #006400;
        border-bottom: 3px solid #006400;
        border-top-left-radius: 2px;
        border-bottom-left-radius: 2px;
        box-sizing: border-box;
        z-index: 10;
        pointer-events: none;
      }

      .Highlight--scrolledTo .Highlight__part::after {
        content: '';
        position: absolute;
        top: -2px;
        right: -16px;
        bottom: -2px;
        height: calc(100% + 4px);
        width: 8px;
        border-right: 3px solid #006400;
        border-top: 3px solid #006400;
        border-bottom: 3px solid #006400;
        border-top-right-radius: 2px;
        border-bottom-right-radius: 2px;
        box-sizing: border-box;
        z-index: 10;
        pointer-events: none;
      }
    `;
    document.head.appendChild(style);
    return () => {
      document.head.removeChild(style);
    };
  }, []);

  // Stable scroll-tracking effect: runs once, uses paginationRef for callbacks.
  // Attaches to the PDF viewer container and syncs currentPage as the user scrolls.
  useEffect(() => {
    let scrollEl: HTMLElement | null = null;
    let rafId: number | null = null;

    const detectCurrentPage = () => {
      if (isNavigating.current) return;
      const viewer = (window as unknown as { PdfViewer?: { viewer?: { currentPageNumber?: number } } }).PdfViewer?.viewer;
      if (!viewer) return;
      const pageNum: number | undefined = viewer.currentPageNumber;
      if (pageNum && pageNum !== lastReportedPage.current) {
        lastReportedPage.current = pageNum;
        paginationRef.current?.onPageChange?.(pageNum);
      }
    };

    const handleScroll = () => {
      if (rafId !== null) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(detectCurrentPage);
    };

    // Poll until the viewer container is available (created after pagesinit)
    const timer = setInterval(() => {
      const container = (window as unknown as { PdfViewer?: { viewer?: { container?: HTMLElement } } }).PdfViewer?.viewer?.container as HTMLElement | undefined;
      if (container) {
        clearInterval(timer);
        scrollEl = container;
        scrollEl.addEventListener('scroll', handleScroll, { passive: true });
      }
    }, 200);

    return () => {
      clearInterval(timer);
      if (rafId !== null) cancelAnimationFrame(rafId);
      if (scrollEl) scrollEl.removeEventListener('scroll', handleScroll);
    };
  }, []);

  // Navigate to page when prev/next buttons are clicked.
  // Uses scrollViewerTo with a synthetic page-jump highlight so PdfHighlighter's
  // destArray-based path handles the navigation (works for all pages, including
  // those not yet rendered in the DOM).
  useEffect(() => {
    const targetPage = pagination?.currentPage;
    if (!targetPage || targetPage === lastReportedPage.current) return;
    if (!isViewerReady.current) return;

    // A citation scroll is about to handle navigation — just sync the ref.
    if (citationScrollPending.current) {
      lastReportedPage.current = targetPage;
      return;
    }

    isNavigating.current = true;
    lastReportedPage.current = targetPage;
    scrollViewerTo.current(pageJumpHighlight(targetPage));

    setTimeout(() => { isNavigating.current = false; }, 500);
  }, [pagination?.currentPage, pageJumpHighlight]);

  // Sync selected highlight with activeCitationId from external citation panel
  useEffect(() => {
    setSelectedHighlightId(activeCitationId ?? null);
    if (activeCitationId) {
      citationScrollPending.current = true;
    }
  }, [activeCitationId]);

  // Scroll to a citation when activeCitationId changes or when highlights load.
  // If the viewer isn't ready yet (PDF still loading), park the scroll in
  // pendingCitationScroll and execute it from the scrollRef callback below.
  useEffect(() => {
    if (!activeCitationId || highlights.length === 0) return;

    const targetHighlight = highlights.find((h) => h.id === activeCitationId);
    if (!targetHighlight) return;

    if (!isViewerReady.current) {
      // Defer: execute once viewer signals ready via scrollRef
      pendingCitationScroll.current = targetHighlight;
      return;
    }

    const timer = setTimeout(() => {
      citationScrollPending.current = false;
      isNavigating.current = true;
      scrollViewerTo.current(targetHighlight);
      lastReportedPage.current = targetHighlight.position.pageNumber;
      paginationRef.current?.onPageChange?.(targetHighlight.position.pageNumber);
      setTimeout(() => { isNavigating.current = false; }, 500);
    }, SCROLL_DELAY_MS);

    return () => clearTimeout(timer);
  }, [activeCitationId, highlights]);

  // Detect page count for pagination
  const handleDocumentLoaded = useCallback(
    (numPages: number) => {
      pagination?.onTotalPagesDetected?.(numPages);
    },
    [pagination],
  );

  if (!fileUrl) {
    return (
      <Flex
        align="center"
        justify="center"
        direction="column"
        gap="3"
        style={{
          width: '100%',
          height: '100%',
          padding: 'var(--space-5)',
        }}
      >
        <MaterialIcon name="description" size={48} color="var(--olive-9)" />
        <Text size="2" color="gray" align="center">
          PDF file URL not available
        </Text>
        <Text size="1" color="gray" align="center">
          Unable to load preview for {fileName}
        </Text>
      </Flex>
    );
  }

  return (
    <Box
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <PdfLoader
        url={fileUrl}
        beforeLoad={
          <Flex
            align="center"
            justify="center"
            style={{
              width: '100%',
              minHeight: '300px',
              padding: 'var(--space-5)',
            }}
          >
            <Text size="2" color="gray">
              Loading PDF...
            </Text>
          </Flex>
        }
        errorMessage={
          <Flex
            align="center"
            justify="center"
            direction="column"
            gap="3"
            style={{
              width: '100%',
              height: '100%',
              padding: 'var(--space-5)',
            }}
          >
            <MaterialIcon name="error_outline" size={48} color="var(--olive-9)" />
            <Text size="2" color="gray" align="center">
              Failed to load PDF file
            </Text>
            <Text size="1" color="gray" align="center">
              Unable to load preview for {fileName}
            </Text>
          </Flex>
        }
      >
        {(pdfDocument) => (
          <>
            <PageCountReporter
              numPages={pdfDocument.numPages}
              onReport={handleDocumentLoaded}
            />
            <PdfHighlighter<IHighlight>
              pdfDocument={pdfDocument}
              enableAreaSelection={(event: MouseEvent) => event.altKey}
              onScrollChange={() => {}}
              scrollRef={(scrollTo) => {
                scrollViewerTo.current = scrollTo;
                isViewerReady.current = true;

                // Execute any citation scroll that was requested before the viewer was ready
                if (pendingCitationScroll.current) {
                  const highlight = pendingCitationScroll.current;
                  pendingCitationScroll.current = null;
                  setTimeout(() => {
                    isNavigating.current = true;
                    citationScrollPending.current = false;
                    scrollTo(highlight);
                    lastReportedPage.current = highlight.position.pageNumber;
                    paginationRef.current?.onPageChange?.(highlight.position.pageNumber);
                    setTimeout(() => { isNavigating.current = false; }, 500);
                  }, 100);
                }
              }}
              onSelectionFinished={() => null}
              highlightTransform={(
                highlight,
                index,
                setTip,
                hideTip,
                _viewportToScaled,
                _screenshot,
                _isScrolledTo,
              ) => {
                const isHighlighted =
                  selectedHighlightId !== null &&
                  selectedHighlightId === highlight.id;

                const isTextHighlight = !highlight.content?.image;
                const component = isTextHighlight ? (
                  <div
                    onClick={() => {
                      setSelectedHighlightId(highlight.id);
                      onHighlightClick?.(highlight.id);
                    }}
                    style={{
                      cursor: 'pointer',
                    }}
                  >
                    <Highlight
                      isScrolledTo={isHighlighted}
                      position={highlight.position}
                      comment={highlight.comment}
                    />
                  </div>
                ) : (
                  <AreaHighlight
                    isScrolledTo={isHighlighted}
                    highlight={highlight}
                    onChange={() => {}}
                  />
                );

                return (
                  <Popup
                    popupContent={<div />}
                    onMouseOver={() => {}}
                    onMouseOut={hideTip}
                    key={index}
                  >
                    {component}
                  </Popup>
                );
              }}
              highlights={
                selectedHighlightId
                  ? highlights.filter((h) => h.id === selectedHighlightId)
                  : []
              }
            />
          </>
        )}
      </PdfLoader>
    </Box>
  );
}

