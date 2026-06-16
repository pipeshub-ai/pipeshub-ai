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

// Constants matching the demo (scaled "paper" space for citation rects)
const PDF_PAGE_WIDTH = 967;
const PDF_PAGE_HEIGHT = 747.2272727272727;
const SCROLL_DELAY_MS = 300;
const NAVIGATION_SETTLE_MS = 500;

type PdfJsViewer = {
  currentPageNumber: number;
  currentScaleValue: string;
  pagesCount?: number;
  container?: HTMLElement;
};

function toValidPageNumber(value: unknown): number | null {
  const normalized =
    typeof value === 'number'
      ? value
      : typeof value === 'string'
        ? Number.parseInt(value, 10)
        : NaN;
  if (!Number.isFinite(normalized) || normalized < 1) return null;
  return Math.trunc(normalized);
}

/** `window.PdfViewer` is the PdfHighlighter instance; `.viewer` is the pdf.js `PDFViewer`. */
function getPdfJsViewer() {
  return (window as unknown as { PdfViewer?: { viewer?: PdfJsViewer } }).PdfViewer?.viewer;
}

function clampToPageBounds(
  targetPage: number,
  knownTotalPages: number | null | undefined,
  viewerPagesCount?: number,
): number {
  const upperBound = knownTotalPages ?? viewerPagesCount;
  if (!upperBound || upperBound < 1) return targetPage;
  return Math.max(1, Math.min(targetPage, upperBound));
}

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
  const pageNumber = toValidPageNumber(citation.pageNumbers?.[0]);
  if (!pageNumber) return null;

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
  const [viewerReadyEpoch, setViewerReadyEpoch] = useState(0);

  // Stable ref to latest pagination callbacks — avoids effects re-running on every render
  const paginationRef = useRef(pagination);
  useEffect(() => { paginationRef.current = pagination; });

  // 0 = “not yet synchronized” with the pdf.js viewer; never a real page.
  const lastReportedPage = useRef<number>(0);
  const isNavigating = useRef(false);
  // Set to true when scrollRef fires (i.e. PDFViewer "pagesinit" is done).
  const isViewerReady = useRef(false);
  // Holds a citation highlight to scroll to once the viewer becomes ready.
  const pendingCitationScroll = useRef<IHighlight | null>(null);
  const navigatingTimeoutRef = useRef<number | null>(null);

  const clearNavigatingSoon = useCallback(() => {
    if (navigatingTimeoutRef.current !== null) {
      window.clearTimeout(navigatingTimeoutRef.current);
    }
    navigatingTimeoutRef.current = window.setTimeout(() => {
      isNavigating.current = false;
      navigatingTimeoutRef.current = null;
    }, NAVIGATION_SETTLE_MS);
  }, []);

  useEffect(
    () => () => {
      if (navigatingTimeoutRef.current !== null) {
        window.clearTimeout(navigatingTimeoutRef.current);
      }
    },
    [],
  );

  const navigateToHighlightSafely = useCallback(
    (scrollTo: (highlight: IHighlight) => void, highlight: IHighlight) => {
      const pageNumber = toValidPageNumber(highlight.position.pageNumber);
      if (!pageNumber) return;

      const viewer = getPdfJsViewer();
      const safePageNumber = clampToPageBounds(
        pageNumber,
        paginationRef.current?.totalPages,
        viewer?.pagesCount,
      );
      isNavigating.current = true;

      // Ensure pdf.js page state is synchronized even if highlight scrolling fails.
      if (viewer) {
        try {
          viewer.currentPageNumber = safePageNumber;
          lastReportedPage.current = safePageNumber;
          paginationRef.current?.onPageChange?.(safePageNumber);
        } catch {
          // Ignore invalid-page throws during document/viewer transitions.
        }
      } else {
        lastReportedPage.current = safePageNumber;
        paginationRef.current?.onPageChange?.(safePageNumber);
      }

      try {
        scrollTo(highlight);
      } catch {
        // pdf-highlighter can throw while page views are still being built.
        // We already synced page number above, so keep non-fatal.
      }

      clearNavigatingSoon();
    },
    [clearNavigatingSoon],
  );

  // Convert citations to highlights
  const highlights = useMemo(() => {
    if (!citations?.length) return [];
    return citations
      .map(citationToHighlight)
      .filter((h): h is IHighlight => h !== null);
  }, [citations]);

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
      const viewer = getPdfJsViewer();
      if (!viewer) return;
      const pageNum = toValidPageNumber(viewer.currentPageNumber);
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
      const container = getPdfJsViewer()?.container;
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

  // New document: reset the viewer; keep lastReported at 0 so the page sync effect
  // runs and applies `currentPage` (e.g. citation initial page) once `pagesinit` is done.
  useEffect(() => {
    lastReportedPage.current = 0;
    isViewerReady.current = false;
    setViewerReadyEpoch((n) => n + 1);
  }, [fileUrl]);

  // When toolbar prev/next (or any host-driven page change) updates `currentPage`,
  // use pdf.js `currentPageNumber` so navigation works for pages that are not yet
  // laid out the way `PdfHighlighter.scrollTo` expects (it calls `getPageView` for the dest).
  useEffect(() => {
    const targetPage = toValidPageNumber(pagination?.currentPage);
    if (!targetPage) return;
    if (!isViewerReady.current) return;

    const viewer = getPdfJsViewer();
    if (!viewer) return;
    const safeTargetPage = clampToPageBounds(
      targetPage,
      pagination?.totalPages,
      viewer.pagesCount,
    );
    if (safeTargetPage === lastReportedPage.current) return;

    isNavigating.current = true;
    try {
      viewer.currentPageNumber = safeTargetPage;
      lastReportedPage.current = safeTargetPage;
    } catch {
      // Ignore invalid-page throws during document/viewer transitions.
    }

    clearNavigatingSoon();
  }, [pagination?.currentPage, pagination?.totalPages, viewerReadyEpoch, clearNavigatingSoon]);

  // react-pdf-highlighter only applies `pdfScaleValue` on `pagesinit`; re-apply when
  // scale (or a new document) changes once the global pdf.js viewer is present.
  const scale = pagination?.scale ?? 1;
  useEffect(() => {
    const viewer = getPdfJsViewer();
    if (!viewer) return;
    const container = viewer.container;
    if (container) {
      const isLaidOut =
        container.isConnected &&
        (container.offsetParent !== null || container.getClientRects().length > 0);
      if (!isLaidOut) return;
    }
    try {
      viewer.currentScaleValue = String(scale);
    } catch {
      // Ignore transient pdf.js layout errors while the viewer is mounting/unmounting.
    }
  }, [scale, fileUrl]);

  // Sync selected highlight with activeCitationId from external citation panel
  useEffect(() => {
    setSelectedHighlightId(activeCitationId ?? null);
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
      navigateToHighlightSafely(scrollViewerTo.current, targetHighlight);
    }, SCROLL_DELAY_MS);

    return () => clearTimeout(timer);
  }, [activeCitationId, highlights, navigateToHighlightSafely]);

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
      className="file-preview-pdf-root"
      style={{
        width: '100%',
        minHeight: '100%',
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
                if (!isViewerReady.current) {
                  isViewerReady.current = true;
                  setViewerReadyEpoch((n) => n + 1);
                }

                // Execute any citation scroll that was requested before the viewer was ready
                if (pendingCitationScroll.current) {
                  const highlight = pendingCitationScroll.current;
                  pendingCitationScroll.current = null;
                  setTimeout(() => {
                    navigateToHighlightSafely(scrollTo, highlight);
                  }, 100);
                }
              }}
              pdfScaleValue={String(pagination?.scale ?? 1)}
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

