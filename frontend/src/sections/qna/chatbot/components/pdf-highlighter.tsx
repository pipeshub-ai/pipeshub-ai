import type { CSSProperties } from 'react';
import type { ScaledPosition } from 'react-pdf-highlighter';
import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/display/api';
import type { DocumentContent } from 'src/sections/knowledgebase/types/search-response';
import type {
  Comment,
  Content,
  Position,
  BoundingBox,
  HighlightType,
  ProcessedCitation,
  HighlightPopupProps,
  PdfHighlighterCompProps,
} from 'src/types/pdf-highlighter';

import { Icon } from '@iconify/react';
import * as pdfjsLib from 'pdfjs-dist';
import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Tip, Popup, Highlight, AreaHighlight, PdfHighlighter } from 'react-pdf-highlighter';

import { Box, Slider, CircularProgress } from '@mui/material';

import minusIcon from '@iconify-icons/mdi/minus';
import plusIcon from '@iconify-icons/mdi/plus';
import magnifyMinusIcon from '@iconify-icons/mdi/magnify-minus';
import magnifyPlusIcon from '@iconify-icons/mdi/magnify-plus';
import refreshIcon from '@iconify-icons/mdi/refresh';
import resizeIcon from '@iconify-icons/mdi/resize';
import magnifyIcon from '@iconify-icons/mdi/magnify';
import CitationSidebar from './highlighter-sidebar';

// Initialize PDF worker
pdfjsLib.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.mjs`;

const getNextId = () => String(Math.random()).slice(2);

// Constants for timing and polling
const SCROLL_TO_HIGHLIGHT_DELAY_MS = 1500;
const HIGHLIGHT_POLLING_INTERVAL_MS = 100;
const HIGHLIGHT_POLLING_MAX_ATTEMPTS = 50; // 5 seconds max

// Custom PDF Loader that can work with either URL or buffer
interface EnhancedPdfLoaderProps {
  url?: string | null;
  pdfBuffer?: ArrayBuffer | null;
  beforeLoad?: any;
  children?: any;
  onError?: any;
  setLoading: any;
}

const EnhancedPdfLoader = ({
  url,
  pdfBuffer,
  beforeLoad,
  children,
  onError,
  setLoading,
}: EnhancedPdfLoaderProps) => {
  const [pdfDocument, setPdfDocument] = useState<PDFDocumentProxy>();
  const [error, setError] = useState(null);

  useEffect(() => {
    const loadPdf = async () => {
      try {
        let loadingTask;

        if (pdfBuffer) {
          // Create a copy of the buffer to prevent detachment issues
          const bufferCopy = pdfBuffer.slice(0);

          loadingTask = pdfjsLib.getDocument({
            data: bufferCopy,
            isEvalSupported: false,
            cMapUrl: `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/cmaps/`,
            cMapPacked: true,
          });
        } else if (url) {
          // URL-based loading remains unchanged
          loadingTask = pdfjsLib.getDocument({
            url,
            isEvalSupported: false,
            cMapUrl: `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/cmaps/`,
            cMapPacked: true,
          });
        } else {
          throw new Error('Either url or pdfBuffer must be provided');
        }

        const document = await loadingTask.promise;
        setPdfDocument(document);
        setLoading(false);
      } catch (err) {
        console.error('Error loading PDF:', err);
        setError(err);
        if (onError) onError(err);
      }
    };

    if (url || pdfBuffer) {
      loadPdf();
    }
    // eslint-disable-next-line
  }, [url, pdfBuffer, onError]);

  if (error) {
    return <div>Error loading PDF. Please try again.</div>;
  }

  if (!pdfDocument) {
    return beforeLoad || <CircularProgress />;
  }

  return children(pdfDocument);
};

const HighlightPopup: React.FC<HighlightPopupProps> = ({ comment }) =>
  comment?.text ? (
    <div className="Highlight__popup">
      {comment.emoji} {comment.text}
    </div>
  ) : null;

const processHighlight = (citation: DocumentContent): HighlightType | null => {
  try {
    // Process from metadata format
    const boundingBox: BoundingBox[] = citation.metadata?.bounding_box;

    if (!boundingBox || boundingBox.length !== 4) {
      console.warn('Invalid bounding box:', boundingBox);
      return null;
    }

    // Convert normalized coordinates to absolute positions
    const PAGE_WIDTH = 967;
    const PAGE_HEIGHT = 747.2272727272727;

    const mainRect = {
      x1: boundingBox[0].x * PAGE_WIDTH,
      y1: boundingBox[0].y * PAGE_HEIGHT,
      x2: boundingBox[2].x * PAGE_WIDTH,
      y2: boundingBox[2].y * PAGE_HEIGHT,
      width: PAGE_WIDTH,
      height: PAGE_HEIGHT,
      pageNumber: citation.metadata?.pageNum[0] || 1,
    };

    // Use citationId as the primary ID, fallback to metadata._id, then citation.id, then generate new ID
    const highlightId = citation.citationId || citation.metadata?._id || citation.id || getNextId();

    return {
      content: {
        text: citation.content || '',
      },
      position: {
        boundingRect: mainRect,
        rects: [mainRect],
        pageNumber: mainRect.pageNumber,
      },
      comment: {
        text: '',
        emoji: '',
      },
      id: highlightId,
    };
  } catch (error) {
    console.error('Error processing highlight:', error);
    return null;
  }
};

const PdfHighlighterComp = ({
  pdfUrl = '',
  pdfBuffer = null,
  externalRecordId = '',
  fileName = '',
  initialHighlights = [],
  citations = [],
  highlightCitation = null,
  onClosePdf,
}: PdfHighlighterCompProps) => {
  const [highlights, setHighlights] = useState<HighlightType[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [actualPdfUrl, setActualPdfUrl] = useState<string | null>(pdfUrl || null);
  const [actualPdfBuffer, setActualPdfBuffer] = useState<ArrayBuffer | null>(pdfBuffer || null);
  const scrollViewerTo = useRef<(highlight: HighlightType) => void>(() => {});
  const [processedCitations, setProcessedCitations] = useState<ProcessedCitation[]>([]);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const [pdfWidth, setPdfWidth] = useState<number>(100); // Percentage of screen width
  const [pdfScale, setPdfScale] = useState<number>(1); // Zoom scale
  const [showWidthControls, setShowWidthControls] = useState<boolean>(false);
  const [showZoomControls, setShowZoomControls] = useState<boolean>(false);
  const [showResetControls, setShowResetControls] = useState<boolean>(false);
  const [originalDimensions, setOriginalDimensions] = useState<{ width: number; scale: number }>({
    width: 80,
    scale: 1,
  });
  const containerRef = useRef<HTMLDivElement>(null);
  const controlsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const style = document.createElement('style');
    style.textContent = `
      .Highlight__part {
        cursor: pointer;
        position: absolute;
        background: rgba(0, 226, 143, 0.2);
        transition: background 0.3s;
      }
   
      .Highlight--scrolledTo .Highlight__part {
        background: rgba(0, 226, 143, 0.4);
        position: relative;
      }
      
      .Highlight--scrolledTo .Highlight__part::before {
        content: '[';
        position: absolute;
        top: 0;
        left: -8px;
        height: 100%;
        color: #006400;
        font-size: 20px;
        font-weight: bold;
        display: flex;
        align-items: center;
      }
   
      .Highlight--scrolledTo .Highlight__part::after {
        content: ']';
        position: absolute;
        top: 0;
        right: -8px;
        height: 100%;
        color: #006400;
        font-size: 20px;
        font-weight: bold;
        display: flex;
        align-items: center;
      }

      .fullscreen-controls {
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 9999;
        display: flex;
        flex-direction: column;
        gap: 8px;
        align-items: flex-end;
      }

      .control-button {
        width: 48px;
        height: 48px;
        border-radius: 50%;
        background: rgba(0, 0, 0, 0.75);
        backdrop-filter: blur(8px);
        border: 1px solid rgba(255, 255, 255, 0.15);
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        transition: all 0.2s ease;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.2);
      }

      .control-button:hover {
        background: rgba(0, 0, 0, 0.85);
        transform: scale(1.05);
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
      }

      .control-button.active {
        background: rgba(64, 158, 255, 0.8);
        border-color: rgba(64, 158, 255, 0.4);
        box-shadow: 0 4px 16px rgba(64, 158, 255, 0.3);
      }

      .control-bar {
        background: rgba(0, 0, 0, 0.85);
        backdrop-filter: blur(15px);
        border: 1px solid rgba(255, 255, 255, 0.15);
        border-radius: 28px;
        padding: 12px 20px;
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 280px;
        box-shadow: 0 6px 24px rgba(0, 0, 0, 0.3);
        animation: slideIn 0.25s ease-out;
      }

      .control-bar.reset-bar {
        min-width: 360px;
        gap: 8px;
      }

      @keyframes slideIn {
        from {
          opacity: 0;
          transform: translateX(20px) translateY(5px);
        }
        to {
          opacity: 1;
          transform: translateX(0) translateY(0);
        }
      }

      .control-bar-button {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.1);
        border: 1px solid rgba(255, 255, 255, 0.2);
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        transition: all 0.15s ease;
      }

      .control-bar-button:hover {
        background: rgba(255, 255, 255, 0.2);
        transform: scale(1.05);
      }

      .control-slider {
        flex: 1;
        color: #409eff !important;
      }

      .control-label {
        color: white;
        font-size: 11px;
        font-weight: 500;
        min-width: 55px;
        text-align: center;
      }

      .reset-button {
        padding: 6px 12px;
        border-radius: 16px;
        background: rgba(255, 255, 255, 0.1);
        border: 1px solid rgba(255, 255, 255, 0.2);
        color: white;
        font-size: 10px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.15s ease;
        white-space: nowrap;
      }

      .reset-button:hover {
        background: rgba(255, 255, 255, 0.2);
        transform: translateY(-1px);
      }

      .reset-button.primary {
        background: rgba(64, 158, 255, 0.6);
        border-color: rgba(64, 158, 255, 0.8);
      }

      .reset-button.primary:hover {
        background: rgba(64, 158, 255, 0.8);
      }
    `;
    document.head.appendChild(style);
    // eslint-disable-next-line no-void
    return () => void document.head.removeChild(style);
  }, []);

  useEffect(() => {
    // Store original dimensions when component mounts
    setOriginalDimensions({ width: pdfWidth, scale: pdfScale });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Only run once on mount - we intentionally want the initial values

  useEffect(() => {
    // Click outside to close controls
    const handleClickOutside = (event: MouseEvent) => {
      if (controlsRef.current && !controlsRef.current.contains(event.target as Node)) {
        setShowWidthControls(false);
        setShowZoomControls(false);
        setShowResetControls(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    const processCitationsWithHighlights = () => {
      if (citations?.length > 0) {
        const processed = citations
          .map((citation) => {
            const highlight = processHighlight(citation);
            return {
              ...citation,
              highlight,
            };
          })
          .filter((citation) => citation.highlight);

        // Validate highlights before setting them
        const validHighlights = processed
          .map((c) => c.highlight)
          .filter((highlight): highlight is HighlightType => {
            if (!highlight) return false;

            // Validate required properties
            const isValid = !!(
              highlight.id &&
              highlight.position &&
              highlight.position.boundingRect &&
              highlight.position.pageNumber &&
              highlight.position.pageNumber > 0
            );

            return isValid;
          });

        setProcessedCitations(processed);
        setHighlights(validHighlights);
      } else {
        setProcessedCitations([]);
        setHighlights([]);
      }
    };

    processCitationsWithHighlights();
  }, [actualPdfUrl, actualPdfBuffer, citations]);

  useEffect(() => {
    // Only execute this effect when necessary conditions are met
    if (
      highlights.length > 0 &&
      highlightCitation &&
      scrollViewerTo.current &&
      typeof scrollViewerTo.current === 'function' &&
      !loading
    ) {
      // Try multiple ID matching strategies
      const citationId =
        highlightCitation.citationId || highlightCitation.metadata?._id || highlightCitation.id;

      if (!citationId) {
        return undefined;
      }

      // Find the highlight that corresponds to the highlightCitation
      const targetHighlight = highlights.find((h) => h.id === citationId);

      // Create a function to check if highlight element exists in DOM
      const waitForHighlightElement = (highlight: HighlightType): Promise<boolean> =>
        new Promise((resolve) => {
          let attempts = 0;

          const pollForElement = () => {
            // Look for highlight elements with the specific ID
            const highlightElement =
              document.querySelector(`[data-highlight-id="${highlight.id}"]`) ||
              document.querySelector(`.Highlight[data-highlight-id="${highlight.id}"]`) ||
              document.querySelector(`[id*="${highlight.id}"]`);

            if (highlightElement) {
              resolve(true);
              return;
            }

            attempts += 1;
            if (attempts >= HIGHLIGHT_POLLING_MAX_ATTEMPTS) {
              resolve(false);
              return;
            }

            setTimeout(pollForElement, HIGHLIGHT_POLLING_INTERVAL_MS);
          };

          pollForElement();
        });

      // Create a function to attempt scrolling
      const attemptScroll = async () => {
        if (targetHighlight) {
          // Wait for the highlight element to exist in DOM
          const elementExists = await waitForHighlightElement(targetHighlight);
          if (elementExists) {
            scrollViewerTo.current(targetHighlight);
            return true;
          }
        }

        // Fallback: Find any highlight on the specified page
        if (highlightCitation.metadata?.pageNum && highlightCitation.metadata.pageNum.length > 0) {
          const pageNumber = highlightCitation.metadata.pageNum[0];
          const highlightOnPage = highlights.find((h) => h.position.pageNumber === pageNumber);

          if (highlightOnPage) {
            const elementExists = await waitForHighlightElement(highlightOnPage);
            if (elementExists) {
              scrollViewerTo.current(highlightOnPage);
              return true;
            }
          }
        }

        return false;
      };

      // Set up a timer to try scrolling after initial delay
      const timer = setTimeout(async () => {
        const scrolled = await attemptScroll();

        // If scrolling failed on first attempt, try once more after a bit
        if (!scrolled) {
          setTimeout(async () => {
            await attemptScroll();
          }, SCROLL_TO_HIGHLIGHT_DELAY_MS);
        }
      }, SCROLL_TO_HIGHLIGHT_DELAY_MS);

      // Clean up timer on unmount
      return () => clearTimeout(timer);
    }

    // Return undefined for cases where we don't set up a timer
    return undefined;
  }, [highlights, highlightCitation, loading]);

  useEffect(() => {
    // Only run this once when the PDF document is available and scroll function is set
    if (scrollViewerTo.current && typeof scrollViewerTo.current === 'function') {
      // Create a wrapper for the scrollViewerTo function that includes error handling
      const originalScrollFn = scrollViewerTo.current;

      // Replace the function with an enhanced version
      scrollViewerTo.current = (highlight: HighlightType) => {
        if (!highlight) {
          return;
        }

        // Validate highlight structure before scrolling
        if (
          !highlight.position ||
          !highlight.position.pageNumber ||
          highlight.position.pageNumber <= 0
        ) {
          return;
        }

        try {
          // Call the original function
          originalScrollFn(highlight);
        } catch (err) {
          // Fallback: try to scroll to the page if highlight fails
          if (highlight.position?.pageNumber) {
            try {
              // This is a basic fallback - scroll to the page
              const pageElement = document.querySelector(
                `[data-page-number="${highlight.position.pageNumber}"]`
              );
              if (pageElement) {
                pageElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }
            } catch (fallbackErr) {
              // Silently ignore fallback error
            }
          }
        }
      };
    }
  }, []);

  const addHighlight = useCallback((highlight: Omit<HighlightType, 'id'>): void => {
    setHighlights((prevHighlights) => [
      {
        ...highlight,
        id: getNextId(),
        comment: highlight.comment || { text: '', emoji: '' },
      },
      ...prevHighlights,
    ]);
  }, []);

  const updateHighlight = useCallback(
    (highlightId: string, position: Partial<Position>, content: Partial<Content>) => {
      setHighlights((prevHighlights) =>
        prevHighlights.map((h) => {
          if (h.id !== highlightId) return h;
          return {
            ...h,
            position: { ...h.position, ...position },
            content: { ...h.content, ...content },
          };
        })
      );
    },
    []
  );

  const handleFullscreenChange = useCallback((): void => {
    setIsFullscreen(!!document.fullscreenElement);
  }, []);

  useEffect(() => {
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, [handleFullscreenChange]);

  const toggleFullScreen = useCallback(async (): Promise<void> => {
    try {
      if (!document.fullscreenElement && containerRef.current) {
        await containerRef.current.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch (err) {
      console.error('Error toggling fullscreen:', err);
    }
  }, []);

  const handleWidthChange = useCallback((_: Event, newValue: number | number[]) => {
    setPdfWidth(newValue as number);
  }, []);

  const handleScaleChange = useCallback((_: Event, newValue: number | number[]) => {
    setPdfScale(newValue as number);
  }, []);

  const increaseScale = useCallback(() => {
    setPdfScale((prev) => Math.min(prev + 0.1, 3));
  }, []);

  const decreaseScale = useCallback(() => {
    setPdfScale((prev) => Math.max(prev - 0.1, 0.5));
  }, []);

  const resetToDefaults = useCallback(() => {
    setPdfWidth(80);
    setPdfScale(1);
  }, []);

  const autoFitWidth = useCallback(() => {
    setPdfWidth(100);
  }, []);

  const autoFitZoom = useCallback(() => {
    setPdfScale(1);
  }, []);

  const fitToScreen = useCallback(() => {
    setPdfWidth(100);
    setPdfScale(0.9);
  }, []);

  const resetToOriginal = useCallback(() => {
    setPdfWidth(originalDimensions.width);
    setPdfScale(originalDimensions.scale);
  }, [originalDimensions]);

  if (loading) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100%',
          width: '100%',
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100%',
          width: '100%',
          color: 'error.main',
        }}
      >
        {error}
      </Box>
    );
  }

  return (
    <Box
      ref={containerRef}
      sx={{
        display: 'flex',
        height: '100%',
        width: '100%',
      }}
    >
      {/* Controls - Available in both modes */}
      <div className="fullscreen-controls" ref={controlsRef}>
        {/* Reset Control Bar */}
        {showResetControls && (
          <div className="control-bar reset-bar">
            <button className="reset-button primary" onClick={resetToDefaults} type="button">
              Reset All
            </button>
            <button className="reset-button" onClick={resetToOriginal} type="button">
              Original
            </button>
            <button className="reset-button" onClick={autoFitWidth} type="button">
              Full Width
            </button>
            <button className="reset-button" onClick={autoFitZoom} type="button">
              100% Zoom
            </button>
            <button className="reset-button" onClick={fitToScreen} type="button">
              Fit Screen
            </button>
          </div>
        )}

        {/* Width Control Bar */}
        {showWidthControls && (
          <div className="control-bar">
            <button
              className="control-bar-button"
              onClick={() => setPdfWidth((prev) => Math.max(prev - 5, 30))}
              type="button"
              aria-label="Decrease width"
            >
              <Icon icon={minusIcon} style={{ fontSize: '16px' }} />
            </button>
            <div className="control-label">Width: {pdfWidth}%</div>
            <Slider
              value={pdfWidth}
              onChange={handleWidthChange}
              min={30}
              max={100}
              step={5}
              size="small"
              className="control-slider"
            />
            <button
              className="control-bar-button"
              onClick={() => setPdfWidth((prev) => Math.min(prev + 5, 100))}
              type="button"
              aria-label="Increase width"
            >
              <Icon icon={plusIcon} style={{ fontSize: '16px' }} />
            </button>
          </div>
        )}

        {/* Zoom Control Bar */}
        {showZoomControls && (
          <div className="control-bar">
            <button
              className="control-bar-button"
              onClick={decreaseScale}
              type="button"
              aria-label="Decrease zoom"
            >
              <Icon icon={magnifyMinusIcon} style={{ fontSize: '16px' }} />
            </button>
            <div className="control-label">Zoom: {Math.round(pdfScale * 100)}%</div>
            <Slider
              value={pdfScale}
              onChange={handleScaleChange}
              min={0.5}
              max={3}
              step={0.1}
              size="small"
              className="control-slider"
            />
            <button
              className="control-bar-button"
              onClick={increaseScale}
              type="button"
              aria-label="Increase zoom"
            >
              <Icon icon={magnifyPlusIcon} style={{ fontSize: '16px' }} />
            </button>
          </div>
        )}

        {/* Control Buttons */}
        <button
          className={`control-button ${showResetControls ? 'active' : ''}`}
          onClick={() => {
            setShowResetControls(!showResetControls);
            setShowWidthControls(false);
            setShowZoomControls(false);
          }}
          type="button"
          aria-label="Reset controls"
        >
          <Icon icon={refreshIcon} style={{ fontSize: '20px' }} />
        </button>

        <button
          className={`control-button ${showWidthControls ? 'active' : ''}`}
          onClick={() => {
            setShowWidthControls(!showWidthControls);
            setShowZoomControls(false);
            setShowResetControls(false);
          }}
          type="button"
          aria-label="Width controls"
        >
          <Icon icon={resizeIcon} style={{ fontSize: '20px' }} />
        </button>

        <button
          className={`control-button ${showZoomControls ? 'active' : ''}`}
          onClick={() => {
            setShowZoomControls(!showZoomControls);
            setShowWidthControls(false);
            setShowResetControls(false);
          }}
          type="button"
          aria-label="Zoom controls"
        >
          <Icon icon={magnifyIcon} style={{ fontSize: '20px' }} />
        </button>
      </div>

      <Box
        sx={{
          flex: 1,
          position: 'relative',
          overflow: 'hidden',
          width: '100%',
          height: '100%',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
        }}
      >
        <Box
          sx={{
            width: `${pdfWidth}%`,
            height: `${Math.max(100 / pdfScale, 100)}%`,
            minHeight: '100%',
            transform: `scale(${pdfScale})`,
            transformOrigin: 'center center',
            overflow: 'hidden',
            background: 'white',
            borderRadius: '8px',
            boxShadow: '0 8px 32px rgba(0, 0, 0, 0.3)',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <EnhancedPdfLoader
            url={actualPdfUrl}
            pdfBuffer={actualPdfBuffer || pdfBuffer}
            setLoading={setLoading}
            beforeLoad={
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'center',
                  alignItems: 'center',
                  height: '100%',
                }}
              >
                <CircularProgress />
              </Box>
            }
          >
            {(pdfDocument: any) => (
              <div
                style={
                  {
                    width: '100%',
                    height: '100%',
                    minHeight: '100%',
                    overflow: 'auto',
                    flex: 1,
                  } as CSSProperties
                }
              >
                <PdfHighlighter<HighlightType>
                  pdfDocument={pdfDocument}
                  enableAreaSelection={(event: MouseEvent) => event.altKey}
                  onScrollChange={() => {}}
                  scrollRef={(scrollTo: (highlight: HighlightType) => void) => {
                    scrollViewerTo.current = scrollTo;
                  }}
                  onSelectionFinished={(
                    position: ScaledPosition,
                    content: Content,
                    hideTipAndSelection,
                    transformSelection
                  ) => (
                    <Tip
                      onOpen={transformSelection}
                      onConfirm={(comment: Comment) => {
                        addHighlight({ content, position, comment });
                        hideTipAndSelection();
                      }}
                    />
                  )}
                  highlightTransform={(
                    highlight,
                    index,
                    setTip,
                    hideTip,
                    viewportToScaled,
                    screenshot,
                    isScrolledTo
                  ) => {
                    // Enhanced highlighting logic with multiple ID matching strategies
                    const citationId =
                      highlightCitation?.citationId ||
                      highlightCitation?.metadata?._id ||
                      highlightCitation?.id;
                    const isHighlighted: boolean =
                      Boolean(isScrolledTo) ||
                      Boolean(highlightCitation && citationId === highlight.id);

                    const isTextHighlight = !highlight.content?.image;
                    const component = isTextHighlight ? (
                      <div
                        className="highlight-wrapper"
                        style={
                          {
                            '--highlight-color': isHighlighted ? '#4caf50' : '#e6f4f1',
                            '--highlight-opacity': isHighlighted ? '0.6' : '0.4',
                          } as CSSProperties
                        }
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
                        onChange={(boundingRect) => {
                          updateHighlight(
                            highlight.id,
                            { boundingRect: viewportToScaled(boundingRect) },
                            { image: screenshot(boundingRect) }
                          );
                        }}
                      />
                    );

                    return (
                      <Popup
                        popupContent={<HighlightPopup {...highlight} />}
                        onMouseOver={(popupContent) => setTip(highlight, () => popupContent)}
                        onMouseOut={hideTip}
                        key={index}
                      >
                        {component}
                      </Popup>
                    );
                  }}
                  highlights={highlights}
                />
              </div>
            )}
          </EnhancedPdfLoader>
        </Box>
      </Box>
      {processedCitations.length > 0 && (
        <CitationSidebar
          citations={processedCitations}
          scrollViewerTo={(highlight) => {
            if (scrollViewerTo.current && typeof scrollViewerTo.current === 'function') {
              scrollViewerTo.current(highlight);
            }
          }}
          highlightedCitationId={
            highlightCitation?.citationId ||
            highlightCitation?.metadata?._id ||
            highlightCitation?.id ||
            null
          }
          toggleFullScreen={toggleFullScreen}
          onClosePdf={onClosePdf}
        />
      )}
    </Box>
  );
};

export default PdfHighlighterComp;
