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

import * as pdfjsLib from 'pdfjs-dist';
import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Tip, Popup, Highlight, AreaHighlight, PdfHighlighter } from 'react-pdf-highlighter';

import { Box, CircularProgress } from '@mui/material';

import CitationSidebar from './highlighter-sidebar';

// Initialize PDF worker
pdfjsLib.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.mjs`;

const getNextId = () => String(Math.random()).slice(2);

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
      id: citation.metadata._id || citation.metadata._id || getNextId(),
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
}: PdfHighlighterCompProps) => {
  const [highlights, setHighlights] = useState<HighlightType[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [actualPdfUrl, setActualPdfUrl] = useState<string | null>(pdfUrl || null);
  const [actualPdfBuffer, setActualPdfBuffer] = useState<ArrayBuffer | null>(pdfBuffer || null);
  const scrollViewerTo = useRef<(highlight: HighlightType) => void>(() => {});
  const [processedCitations, setProcessedCitations] = useState<ProcessedCitation[]>([]);
  console.log(citations);
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
    `;
    document.head.appendChild(style);
    // eslint-disable-next-line no-void
    return () => void document.head.removeChild(style);
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

        setProcessedCitations(processed);
        setHighlights(processed.map((c) => c.highlight).filter(Boolean) as HighlightType[]);
      } else {
        setProcessedCitations([]);
        setHighlights([]);
      }
    };

    processCitationsWithHighlights();
  }, [actualPdfUrl, actualPdfBuffer, citations]);

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
    <Box sx={{ display: 'flex', height: '100%', width: '100%' }}>
      <Box sx={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
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
                  overflow: 'auto',
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
                  const isTextHighlight = !highlight.content?.image;
                  const component = isTextHighlight ? (
                    <div
                      className="highlight-wrapper"
                      style={
                        {
                          '--highlight-color': '#e6f4f1',
                          '--highlight-opacity': '0.4',
                        } as CSSProperties
                      }
                    >
                      <Highlight
                        isScrolledTo={isScrolledTo}
                        position={highlight.position}
                        comment={highlight.comment}
                      />
                    </div>
                  ) : (
                    <AreaHighlight
                      isScrolledTo={isScrolledTo}
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
      <CitationSidebar citations={processedCitations} scrollViewerTo={scrollViewerTo.current} />
    </Box>
  );
};

export default PdfHighlighterComp;
