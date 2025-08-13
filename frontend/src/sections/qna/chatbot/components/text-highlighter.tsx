import type { CustomCitation } from 'src/types/chat-bot';
import type { Position, HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';
import type {
  SearchResult,
  DocumentContent,
} from 'src/sections/knowledgebase/types/search-response';

import { Icon } from '@iconify/react';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import React, { useRef, useState, useEffect, useCallback } from 'react';

import { styled } from '@mui/material/styles';
import { Box, Paper, useTheme, Typography, CircularProgress } from '@mui/material';

import CitationSidebar from './highlighter-sidebar';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

// Props type definition - UPDATED to match MarkdownViewer
type TextViewerProps = {
  citations: DocumentContent[] | CustomCitation[];
  url: string | null;
  text?: string | null;
  buffer?: ArrayBuffer | null;
  sx?: Record<string, unknown>;
  highlightCitation?: SearchResult | CustomCitation | null; // NEW: Added like MarkdownViewer
  onClosePdf: () => void;
};

// similarity threshold
const SIMILARITY_THRESHOLD = 0.6;

// Styled components
const TextViewerContainer = styled(Box)(({ theme }) => ({
  width: '100%',
  height: '100%',
  position: 'relative',
  overflow: 'hidden',
  borderRadius: theme.shape.borderRadius,
  border: `1px solid ${theme.palette.divider}`,
}));

const LoadingOverlay = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: 0,
  left: 0,
  width: '100%',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: 'rgba(255, 255, 255, 0.8)',
  zIndex: 10,
}));

const ErrorOverlay = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: 0,
  left: 0,
  width: '100%',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: theme.palette.error.lighter,
  color: theme.palette.error.dark,
  padding: theme.spacing(2),
  textAlign: 'center',
  zIndex: 10,
}));

const DocumentContainer = styled(Box)({
  width: '100%',
  height: '100%',
  overflow: 'auto',
  minHeight: '100px',
  fontFamily: 'monospace',
  padding: '16px',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  lineHeight: 1.6,
});

// Helper functions
const getNextId = (): string => `text-hl-${Math.random().toString(36).substring(2, 10)}`;

const isDocumentContent = (
  citation: DocumentContent | CustomCitation
): citation is DocumentContent => 'metadata' in citation && citation.metadata !== undefined;

const normalizeText = (text: string | null | undefined): string => {
  if (!text) return '';
  return text.trim().replace(/\s+/g, ' ');
};

const processTextHighlight = (citation: DocumentContent | CustomCitation): HighlightType | null => {
  try {
    const rawContent = citation.content;
    const normalizedContent = normalizeText(rawContent);

    if (!normalizedContent || normalizedContent.length < 5) {
      return null;
    }

    let id: string;

    if ('highlightId' in citation && citation.highlightId) id = citation.highlightId as string;
    else if ('id' in citation && citation.id) id = citation.id as string;
    else if ('citationId' in citation && citation.citationId) id = citation.citationId as string;
    else if (isDocumentContent(citation) && citation.metadata?._id) id = citation.metadata._id;
    else if ('_id' in citation && citation._id) id = citation._id as string;
    else id = getNextId();

    const position: Position = {
      pageNumber: -10,
      boundingRect: {
        x1: 0,
        y1: 0,
        x2: 0,
        y2: 0,
        width: 0,
        height: 0,
      },
      rects: [],
    };

    return {
      content: {
        text: normalizedContent,
      },
      position,
      comment: {
        text: '',
        emoji: '',
      },
      id,
    };
  } catch (error) {
    console.error('Error processing highlight:', error);
    return null;
  }
};

const TextViewer: React.FC<TextViewerProps> = ({
  url,
  text,
  buffer,
  sx = {},
  citations = [],
  highlightCitation = null,
  onClosePdf,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [documentReady, setDocumentReady] = useState<boolean>(false);
  const [documentText, setDocumentText] = useState<string>('');
  const [processedCitations, setProcessedCitations] = useState<ProcessedCitation[]>([]);
  const [highlightedCitationId, setHighlightedCitationId] = useState<string | null>(null); // NEW: Added like MarkdownViewer
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const fullScreenContainerRef = useRef<HTMLDivElement>(null);

  const styleAddedRef = useRef<boolean>(false);
  const processingCitationsRef = useRef<boolean>(false);
  const contentRenderedRef = useRef<boolean>(false);
  const highlightsAppliedRef = useRef<boolean>(false);
  const highlightingInProgressRef = useRef<boolean>(false);
  const cleanupStylesRef = useRef<(() => void) | null>(null);
  const prevCitationsJsonRef = useRef<string>('[]');
  const highlightCleanupsRef = useRef<Map<string, () => void>>(new Map());
  const theme = useTheme();
  const scrollableStyles = createScrollableContainerStyle(theme);

  const createHighlightStyles = useCallback((): (() => void) | undefined => {
    const styleId = 'text-highlight-styles';
    if (document.getElementById(styleId)) return undefined;

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      /* Base highlight style - Professional with warm amber tones */
      .text-highlight {
        background-color: rgba(255, 193, 7, 0.15); /* Warm amber */
        border-radius: 2px;
        padding: 0.1em 0.2em;
        margin: -0.1em -0.1em;
        cursor: pointer;
        transition: all 0.2s ease;
        display: inline;
        position: relative;
        z-index: 1;
        border: 1px solid rgba(255, 193, 7, 0.25);
        white-space: pre-wrap !important;
        color: inherit !important;
        text-decoration: none !important;
        text-shadow: 0 0 1px rgba(0, 0, 0, 0.1);
      }

      /* Dark mode adjustments */
      @media (prefers-color-scheme: dark) {
        .text-highlight {
          background-color: rgba(255, 213, 79, 0.20);
          border-color: rgba(255, 213, 79, 0.35);
          text-shadow: 0 0 1px rgba(0, 0, 0, 0.5);
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .text-highlight,
      [data-theme="dark"] .text-highlight,
      .dark .text-highlight {
        background-color: rgba(255, 213, 79, 0.20);
        border-color: rgba(255, 213, 79, 0.35);
        text-shadow: 0 0 1px rgba(0, 0, 0, 0.5);
      }

      .text-highlight:hover {
        background-color: rgba(255, 193, 7, 0.25);
        border-color: rgba(255, 193, 7, 0.45);
        transform: translateY(-0.5px);
        box-shadow: 0 2px 4px rgba(255, 193, 7, 0.2);
        z-index: 2;
      }

      @media (prefers-color-scheme: dark) {
        .text-highlight:hover {
          background-color: rgba(255, 213, 79, 0.30);
          border-color: rgba(255, 213, 79, 0.55);
          box-shadow: 0 2px 4px rgba(255, 213, 79, 0.25);
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .text-highlight:hover,
      [data-theme="dark"] .text-highlight:hover,
      .dark .text-highlight:hover {
        background-color: rgba(255, 213, 79, 0.30);
        border-color: rgba(255, 213, 79, 0.55);
        box-shadow: 0 2px 4px rgba(255, 213, 79, 0.25);
      }

      .text-highlight-active {
        background-color: rgba(255, 152, 0, 0.35) !important;
        border-color: rgba(255, 152, 0, 0.8) !important;
        border-width: 1.5px !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 3px 8px rgba(255, 152, 0, 0.3) !important;
        z-index: 3 !important;
        animation: textHighlightPulse 0.8s 1 ease-out;
      }

      @media (prefers-color-scheme: dark) {
        .text-highlight-active {
          background-color: rgba(255, 183, 77, 0.40) !important;
          border-color: rgba(255, 183, 77, 0.9) !important;
          box-shadow: 0 3px 8px rgba(255, 183, 77, 0.35) !important;
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .text-highlight-active,
      [data-theme="dark"] .text-highlight-active,
      .dark .text-highlight-active {
        background-color: rgba(255, 183, 77, 0.40) !important;
        border-color: rgba(255, 183, 77, 0.9) !important;
        box-shadow: 0 3px 8px rgba(255, 183, 77, 0.35) !important;
      }

      @keyframes textHighlightPulse {
        0% { 
          transform: translateY(-1px) scale(1);
          box-shadow: 0 3px 8px rgba(255, 152, 0, 0.3);
        }
        50% { 
          transform: translateY(-1px) scale(1.02);
          box-shadow: 0 4px 12px rgba(255, 152, 0, 0.4);
        }
        100% { 
          transform: translateY(-1px) scale(1);
          box-shadow: 0 3px 8px rgba(255, 152, 0, 0.3);
        }
      }

      /* Fuzzy match styling with green tones */
      .text-highlight-fuzzy {
        background-color: rgba(76, 175, 80, 0.12);
        border-color: rgba(76, 175, 80, 0.25);
        border-style: dashed;
      }

      @media (prefers-color-scheme: dark) {
        .text-highlight-fuzzy {
          background-color: rgba(129, 199, 132, 0.18);
          border-color: rgba(129, 199, 132, 0.35);
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .text-highlight-fuzzy,
      [data-theme="dark"] .text-highlight-fuzzy,
      .dark .text-highlight-fuzzy {
        background-color: rgba(129, 199, 132, 0.18);
        border-color: rgba(129, 199, 132, 0.35);
      }

      .text-highlight-fuzzy:hover {
        background-color: rgba(76, 175, 80, 0.20);
        border-color: rgba(76, 175, 80, 0.40);
        box-shadow: 0 2px 4px rgba(76, 175, 80, 0.15);
      }

      @media (prefers-color-scheme: dark) {
        .text-highlight-fuzzy:hover {
          background-color: rgba(129, 199, 132, 0.25);
          border-color: rgba(129, 199, 132, 0.50);
          box-shadow: 0 2px 4px rgba(129, 199, 132, 0.20);
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .text-highlight-fuzzy:hover,
      [data-theme="dark"] .text-highlight-fuzzy:hover,
      .dark .text-highlight-fuzzy:hover {
        background-color: rgba(129, 199, 132, 0.25);
        border-color: rgba(129, 199, 132, 0.50);
        box-shadow: 0 2px 4px rgba(129, 199, 132, 0.20);
      }

      .text-highlight-fuzzy.text-highlight-active {
        background-color: rgba(67, 160, 71, 0.30) !important;
        border-color: rgba(67, 160, 71, 0.8) !important;
        border-style: dashed !important;
        box-shadow: 0 3px 8px rgba(67, 160, 71, 0.25) !important;
        animation: textHighlightPulseFuzzy 0.8s 1 ease-out;
      }

      @media (prefers-color-scheme: dark) {
        .text-highlight-fuzzy.text-highlight-active {
          background-color: rgba(129, 199, 132, 0.35) !important;
          border-color: rgba(129, 199, 132, 0.9) !important;
          box-shadow: 0 3px 8px rgba(129, 199, 132, 0.30) !important;
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .text-highlight-fuzzy.text-highlight-active,
      [data-theme="dark"] .text-highlight-fuzzy.text-highlight-active,
      .dark .text-highlight-fuzzy.text-highlight-active {
        background-color: rgba(129, 199, 132, 0.35) !important;
        border-color: rgba(129, 199, 132, 0.9) !important;
        box-shadow: 0 3px 8px rgba(129, 199, 132, 0.30) !important;
      }

      @keyframes textHighlightPulseFuzzy {
        0% { 
          transform: translateY(-1px) scale(1);
          box-shadow: 0 3px 8px rgba(67, 160, 71, 0.25);
        }
        50% { 
          transform: translateY(-1px) scale(1.02);
          box-shadow: 0 4px 12px rgba(67, 160, 71, 0.35);
        }
        100% { 
          transform: translateY(-1px) scale(1);
          box-shadow: 0 3px 8px rgba(67, 160, 71, 0.25);
        }
      }

      /* Text line specific styles */
      .text-line {
        margin: 0;
        padding: 2px 0;
      }

      /* Reduce motion for accessibility */
      @media (prefers-reduced-motion: reduce) {
        .text-highlight,
        .text-highlight:hover,
        .text-highlight-active,
        .text-highlight-fuzzy,
        .text-highlight-fuzzy:hover,
        .text-highlight-fuzzy.text-highlight-active {
          transition: none;
          animation: none;
          transform: none !important;
        }
      }
    `;

    document.head.appendChild(style);

    const cleanup = (): void => {
      const styleElement = document.getElementById(styleId);
      if (styleElement) {
        try {
          document.head.removeChild(styleElement);
        } catch (e) {
          console.error('Error removing text highlight styles:', e);
        }
      }
    };
    cleanupStylesRef.current = cleanup;
    return cleanup;
  }, []);

  const calculateSimilarity = useCallback((text1: string, text2: string | null): number => {
    const normalized1 = normalizeText(text1);
    const normalized2 = normalizeText(text2);
    if (!normalized1 || !normalized2) return 0;

    const words1 = new Set(
      normalized1
        .toLowerCase()
        .split(/\s+/)
        .filter((w) => w.length > 2)
    );
    const words2 = new Set(
      normalized2
        .toLowerCase()
        .split(/\s+/)
        .filter((w) => w.length > 2)
    );
    if (words1.size === 0 || words2.size === 0) return 0;

    let intersectionSize = 0;
    words1.forEach((word) => {
      if (words2.has(word)) intersectionSize += 1;
    });
    const unionSize = words1.size + words2.size - intersectionSize;
    return unionSize === 0 ? 0 : intersectionSize / unionSize;
  }, []);

  const highlightTextInElement = useCallback(
    (
      element: Element,
      normalizedTextToHighlight: string,
      highlightId: string,
      matchType: 'exact' | 'fuzzy' = 'exact'
    ): { success: boolean; cleanup?: () => void } => {
      if (!element || !normalizedTextToHighlight || normalizedTextToHighlight.length < 3) {
        return { success: false };
      }

      const highlightBaseClass = 'text-highlight';
      const highlightIdClass = `highlight-${highlightId}`;
      const highlightTypeClass = `text-highlight-${matchType}`;
      const fullHighlightClass = `${highlightBaseClass} ${highlightIdClass} ${highlightTypeClass}`;

      try {
        const elementContent = element.textContent || '';
        const normalizedContent = normalizeText(elementContent);

        const textIndex = normalizedContent
          .toLowerCase()
          .indexOf(normalizedTextToHighlight.toLowerCase());

        if (textIndex === -1 && matchType === 'fuzzy') {
          const wrapper = document.createElement('span');
          wrapper.className = fullHighlightClass;
          wrapper.textContent = elementContent;
          wrapper.dataset.highlightId = highlightId;

          wrapper.addEventListener('click', (e) => {
            e.stopPropagation();
            containerRef.current?.querySelectorAll('.text-highlight-active').forEach((el) => {
              el.classList.remove('text-highlight-active');
            });
            wrapper.classList.add('text-highlight-active');
            setHighlightedCitationId(highlightId);
            wrapper.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
          });

          element.textContent = '';
          element.appendChild(wrapper);

          const cleanup = () => {
            if (element && wrapper.parentNode === element) {
              element.textContent = wrapper.textContent;
            }
          };

          return { success: true, cleanup };
        }

        if (textIndex >= 0) {
          const beforeText = elementContent.substring(0, textIndex);
          const highlightText = elementContent.substring(
            textIndex,
            textIndex + normalizedTextToHighlight.length
          );
          const afterText = elementContent.substring(textIndex + normalizedTextToHighlight.length);

          element.textContent = '';

          if (beforeText) {
            element.appendChild(document.createTextNode(beforeText));
          }

          const highlightSpan = document.createElement('span');
          highlightSpan.className = fullHighlightClass;
          highlightSpan.textContent = highlightText;
          highlightSpan.dataset.highlightId = highlightId;

          highlightSpan.addEventListener('click', (e) => {
            e.stopPropagation();
            containerRef.current?.querySelectorAll('.text-highlight-active').forEach((el) => {
              el.classList.remove('text-highlight-active');
            });
            highlightSpan.classList.add('text-highlight-active');
            setHighlightedCitationId(highlightId);
            highlightSpan.scrollIntoView({
              behavior: 'smooth',
              block: 'center',
              inline: 'nearest',
            });
          });

          element.appendChild(highlightSpan);

          if (afterText) {
            element.appendChild(document.createTextNode(afterText));
          }

          const cleanup = () => {
            if (element) {
              element.textContent = elementContent;
            }
          };

          return { success: true, cleanup };
        }

        return { success: false };
      } catch (err) {
        console.error('Error in highlightTextInElement:', err);
        return { success: false };
      }
    },
    [containerRef]
  );

  const clearHighlights = useCallback(() => {
    if (!containerRef.current || highlightingInProgressRef.current) {
      return;
    }
    highlightingInProgressRef.current = true;

    highlightCleanupsRef.current.forEach((cleanup, id) => {
      try {
        cleanup();
      } catch (e) {
        console.error(`Error running cleanup for highlight ${id}:`, e);
      }
    });
    highlightCleanupsRef.current.clear();

    const remainingSpans = containerRef.current.querySelectorAll('.text-highlight');
    if (remainingSpans.length > 0) {
      remainingSpans.forEach((span) => {
        if (span.parentNode) {
          const textContent = span.textContent || '';
          try {
            span.parentNode.replaceChild(document.createTextNode(textContent), span);
          } catch (replaceError) {
            console.error('Error during fallback removal of span:', replaceError, span);
            if (span.parentNode)
              try {
                span.parentNode.removeChild(span);
              } catch (_) {
                console.error('Error during removal of span:', replaceError, span);
              }
          }
        }
      });
    }

    containerRef.current.querySelectorAll('.text-highlight-active').forEach((el) => {
      el.classList.remove('text-highlight-active');
    });

    highlightsAppliedRef.current = false;
    highlightingInProgressRef.current = false;
  }, []);

  const applyTextHighlights = useCallback(
    (citationsToHighlight: ProcessedCitation[]): void => {
      if (
        !containerRef.current ||
        highlightingInProgressRef.current ||
        !documentReady ||
        !contentRenderedRef.current
      ) {
        return;
      }

      highlightingInProgressRef.current = true;
      clearHighlights();
      highlightingInProgressRef.current = true;

      requestAnimationFrame(() => {
        try {
          if (!containerRef.current) {
            console.error('Container ref lost during applyTextHighlights delay.');
            highlightingInProgressRef.current = false;
            return;
          }

          let appliedCount = 0;
          const newCleanups = new Map<string, () => void>();
          const candidateElements = Array.from(containerRef.current.querySelectorAll('.text-line'));

          citationsToHighlight.forEach((citation) => {
            const normalizedText = citation.highlight?.content?.text;
            const highlightId = citation.highlight?.id;

            if (!normalizedText || !highlightId) {
              return;
            }

            const exactMatchFound = candidateElements.some((element) => {
              const normalizedElementText = normalizeText(element.textContent);

              if (normalizedElementText.includes(normalizedText)) {
                const result = highlightTextInElement(
                  element,
                  normalizedText,
                  highlightId,
                  'exact'
                );

                if (result.success) {
                  appliedCount += 1;
                  if (result.cleanup) newCleanups.set(highlightId, result.cleanup);
                  return true;
                }
              }
              return false;
            });

            if (!exactMatchFound && candidateElements.length > 0) {
              const similarityScores = candidateElements
                .map((el) => ({
                  element: el,
                  score: calculateSimilarity(normalizedText, el.textContent),
                }))
                .filter((item) => item.score > SIMILARITY_THRESHOLD)
                .sort((a, b) => b.score - a.score);

              if (similarityScores.length > 0) {
                const bestMatch = similarityScores[0];
                const result = highlightTextInElement(
                  bestMatch.element,
                  normalizedText,
                  highlightId,
                  'fuzzy'
                );
                if (result.success) {
                  appliedCount += 1;
                  if (result.cleanup) newCleanups.set(highlightId, result.cleanup);
                }
              }
            }
          });

          highlightCleanupsRef.current = newCleanups;
          highlightsAppliedRef.current = appliedCount > 0;
          document.dispatchEvent(new CustomEvent('highlightsapplied'));
        } catch (e) {
          console.error('Error during applyTextHighlights execution:', e);
          highlightsAppliedRef.current = false;
        } finally {
          highlightingInProgressRef.current = false;
        }
      });
    },
    [documentReady, clearHighlights, highlightTextInElement, calculateSimilarity]
  );

  useEffect(() => {
    if (!highlightCitation) {
      setHighlightedCitationId(null);
      return;
    }

    let highlightId: string | null = null;

    if ('citationId' in highlightCitation && highlightCitation.citationId) {
      highlightId = highlightCitation.citationId;
    } else if ('_id' in highlightCitation && highlightCitation._id) {
      highlightId = highlightCitation._id;
    } else if ('id' in highlightCitation && highlightCitation.id) {
      highlightId = highlightCitation.id;
    } else if (highlightCitation.metadata?._id) {
      highlightId = highlightCitation.metadata._id;
    }

    if (!highlightId) return;

    setHighlightedCitationId(highlightId);
  }, [highlightCitation]);

  const processCitations = useCallback(() => {
    const currentCitationsContent = JSON.stringify(
      citations?.map((c) => normalizeText(c?.content)).sort() ?? []
    );

    if (
      processingCitationsRef.current ||
      !citations ||
      citations.length === 0 ||
      (currentCitationsContent === prevCitationsJsonRef.current && highlightsAppliedRef.current)
    ) {
      if (currentCitationsContent !== prevCitationsJsonRef.current) {
        prevCitationsJsonRef.current = currentCitationsContent;
        highlightsAppliedRef.current = false;
      }
      return;
    }

    processingCitationsRef.current = true;

    try {
      const processed: ProcessedCitation[] = citations
        .map((citation) => {
          const highlight = processTextHighlight(citation);
          if (highlight) {
            return { ...citation, highlight } as ProcessedCitation;
          }
          return null;
        })
        .filter((item): item is ProcessedCitation => item !== null);

      setProcessedCitations(processed);
      prevCitationsJsonRef.current = currentCitationsContent;
      highlightsAppliedRef.current = false;

      if (
        processed.length > 0 &&
        containerRef.current &&
        documentReady &&
        contentRenderedRef.current &&
        !highlightingInProgressRef.current
      ) {
        requestAnimationFrame(() => applyTextHighlights(processed));
      }
    } catch (err) {
      console.error('Error processing citations:', err);
    } finally {
      processingCitationsRef.current = false;
    }
  }, [citations, documentReady, applyTextHighlights]);

  useEffect(() => {
    if (!documentReady || !documentText || !containerRef.current) {
      return undefined;
    }

    const observer = new MutationObserver((mutations) => {
      const hasTextContent = containerRef.current?.querySelector('.text-line');

      if (hasTextContent && !contentRenderedRef.current) {
        contentRenderedRef.current = true;
        observer.disconnect();

        if (citations.length > 0 && !processingCitationsRef.current) {
          processCitations();

          if (highlightedCitationId) {
            setTimeout(() => {
              const targetCitation = processedCitations.find(
                (citation) => citation.highlight?.id === highlightedCitationId
              );

              if (targetCitation?.highlight) {
                setTimeout(() => {
                  if (!highlightingInProgressRef.current) {
                    applyTextHighlights(processedCitations);

                    setTimeout(() => {
                      const highlightElement = containerRef.current?.querySelector(
                        `.highlight-${highlightedCitationId}`
                      );

                      if (highlightElement) {
                        containerRef.current
                          ?.querySelectorAll('.text-highlight-active')
                          .forEach((el) => el.classList.remove('text-highlight-active'));

                        highlightElement.classList.add('text-highlight-active');
                        highlightElement.scrollIntoView({
                          behavior: 'smooth',
                          block: 'center',
                          inline: 'nearest',
                        });
                      }
                    }, 200);
                  }
                }, 100);
              }
            }, 50);
          }
        }
      }
    });

    if (containerRef.current) {
      observer.observe(containerRef.current, {
        childList: true,
        subtree: true,
        attributes: false,
        characterData: false,
      });
    }

    return () => {
      observer.disconnect();
    };
  }, [
    documentReady,
    documentText,
    highlightedCitationId,
    citations,
    processedCitations,
    applyTextHighlights,
    processCitations,
  ]);

  useEffect(() => {
    if (!documentReady || !highlightedCitationId || !processedCitations.length) {
      return;
    }

    const targetCitation = processedCitations.find(
      (citation) => citation.highlight?.id === highlightedCitationId
    );

    if (!targetCitation?.highlight) {
      return;
    }

    let attempts = 0;
    const maxAttempts = 3;
    const baseDelay = 100;

    const attemptHighlightAndScroll = () => {
      attempts += 1;

      if (!contentRenderedRef.current || !containerRef.current) {
        if (attempts < maxAttempts) {
          setTimeout(attemptHighlightAndScroll, baseDelay);
        }
        return;
      }

      const existingHighlight = containerRef.current.querySelector(
        `.highlight-${highlightedCitationId}`
      );

      if (existingHighlight) {
        containerRef.current.querySelectorAll('.text-highlight-active').forEach((el) => {
          el.classList.remove('text-highlight-active');
        });

        existingHighlight.classList.add('text-highlight-active');
        existingHighlight.scrollIntoView({
          behavior: 'smooth',
          block: 'center',
          inline: 'nearest',
        });
        return;
      }

      if (!highlightingInProgressRef.current) {
        highlightingInProgressRef.current = true;

        clearHighlights();

        requestAnimationFrame(() => {
          if (!containerRef.current) {
            highlightingInProgressRef.current = false;
            return;
          }

          const candidateElements = Array.from(containerRef.current.querySelectorAll('.text-line'));
          let highlightApplied = false;
          const newCleanups = new Map<string, () => void>();

          processedCitations.forEach((citation) => {
            const normalizedText = citation.highlight?.content?.text;
            const citationId = citation.highlight?.id;

            if (!normalizedText || !citationId) return;

            const exactMatch = candidateElements.find((element) => {
              const elementText = normalizeText(element.textContent);
              return elementText.includes(normalizedText);
            });

            if (exactMatch) {
              const result = highlightTextInElement(
                exactMatch,
                normalizedText,
                citationId,
                'exact'
              );
              if (result.success) {
                if (result.cleanup) newCleanups.set(citationId, result.cleanup);
                if (citationId === highlightedCitationId) {
                  highlightApplied = true;
                }
              }
            } else {
              const fuzzyMatches = candidateElements
                .map((el) => ({
                  element: el,
                  score: calculateSimilarity(normalizedText, el.textContent),
                }))
                .filter((item) => item.score > SIMILARITY_THRESHOLD)
                .sort((a, b) => b.score - a.score);

              if (fuzzyMatches.length > 0) {
                const result = highlightTextInElement(
                  fuzzyMatches[0].element,
                  normalizedText,
                  citationId,
                  'fuzzy'
                );
                if (result.success) {
                  if (result.cleanup) newCleanups.set(citationId, result.cleanup);
                  if (citationId === highlightedCitationId) {
                    highlightApplied = true;
                  }
                }
              }
            }
          });

          highlightCleanupsRef.current = newCleanups;
          highlightsAppliedRef.current = newCleanups.size > 0;
          highlightingInProgressRef.current = false;

          setTimeout(() => {
            const targetHighlight = containerRef.current?.querySelector(
              `.highlight-${highlightedCitationId}`
            );

            if (targetHighlight) {
              containerRef.current?.querySelectorAll('.text-highlight-active').forEach((el) => {
                el.classList.remove('text-highlight-active');
              });

              targetHighlight.classList.add('text-highlight-active');
              targetHighlight.scrollIntoView({
                behavior: 'smooth',
                block: 'center',
                inline: 'nearest',
              });
            } else if (attempts < maxAttempts) {
              setTimeout(attemptHighlightAndScroll, baseDelay * attempts);
            }
          }, 100);
        });
      } else if (attempts < maxAttempts) {
        setTimeout(attemptHighlightAndScroll, baseDelay);
      }
    };

    setTimeout(attemptHighlightAndScroll, 200);
  }, [
    documentReady,
    highlightedCitationId,
    processedCitations,
    highlightTextInElement,
    calculateSimilarity,
    clearHighlights,
  ]);

  const scrollToHighlight = useCallback(
    (highlight: HighlightType | null): void => {
      if (!containerRef.current || !highlight || !highlight.id) return;

      const highlightId = highlight.id;

      const findAndScroll = () => {
        const highlightElement = containerRef.current?.querySelector(`.highlight-${highlightId}`);

        if (highlightElement) {
          containerRef.current?.querySelectorAll('.text-highlight-active').forEach((el) => {
            el.classList.remove('text-highlight-active');
          });

          highlightElement.classList.add('text-highlight-active');
          setHighlightedCitationId(highlightId);

          highlightElement.scrollIntoView({
            behavior: 'smooth',
            block: 'center',
            inline: 'nearest',
          });
          return true;
        }
        return false;
      };

      if (findAndScroll()) {
        return;
      }

      if (processedCitations.length > 0 && !highlightingInProgressRef.current) {
        applyTextHighlights(processedCitations);

        setTimeout(() => {
          findAndScroll();
        }, 300);
      }
    },
    [processedCitations, applyTextHighlights]
  );

  // STEP 1: Load text content
  useEffect(() => {
    const loadTextContent = async (): Promise<void> => {
      try {
        setLoading(true);
        setError(null);
        setDocumentText('');
        setDocumentReady(false);
        contentRenderedRef.current = false;
        highlightsAppliedRef.current = false;
        prevCitationsJsonRef.current = '[]';
        clearHighlights();

        if (text) {
          setDocumentText(text);
          setLoading(false);
          setDocumentReady(true);
          return;
        }

        if (buffer) {
          try {
            const decoder = new TextDecoder('utf-8');
            const decodedText = decoder.decode(buffer);

            if (!decodedText) {
              throw new Error('Failed to decode buffer content');
            }

            setDocumentText(decodedText);
            setLoading(false);
            setDocumentReady(true);
            return;
          } catch (bufferError) {
            throw new Error(`Failed to process buffer: ${bufferError.message}`);
          }
        }

        if (url) {
          try {
            const fetchWithTimeout = async (fetchUrl: string, timeoutMs = 30000) => {
              const controller = new AbortController();
              const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

              try {
                const response = await fetch(fetchUrl, { signal: controller.signal });
                clearTimeout(timeoutId);
                return response;
              } catch (err) {
                clearTimeout(timeoutId);
                throw err;
              }
            };

            const response = await fetchWithTimeout(url);

            if (!response.ok) {
              throw new Error(
                `Failed to fetch document: ${response.status} ${response.statusText}`
              );
            }

            const fileText = await response.text();

            if (!fileText) {
              throw new Error('Received empty file from URL');
            }

            setDocumentText(fileText);
            setLoading(false);
            setDocumentReady(true);
          } catch (urlError) {
            if (urlError.name === 'AbortError') {
              throw new Error('Document fetch timed out. Check if the URL is accessible.');
            }
            throw new Error(`Failed to process document: ${urlError.message}`);
          }
        } else {
          throw new Error('Either url, text, or buffer must be provided');
        }
      } catch (err) {
        console.error('Error loading text document:', err);
        const errorMessage = err instanceof Error ? err.message : 'Unknown error loading document';
        setError(errorMessage);
        setLoading(false);
      }
    };

    loadTextContent();
  }, [url, text, buffer, clearHighlights]);

  // STEP 2: Render text content when document is ready
  useEffect(() => {
    if (!documentReady || !documentText || !containerRef.current) return;

    try {
      containerRef.current.innerHTML = '';

      const lines = documentText.split('\n');
      const fragment = document.createDocumentFragment();

      lines.forEach((line, index) => {
        const paragraph = document.createElement('p');
        paragraph.id = `line-${index}`;
        paragraph.className = 'text-line';
        paragraph.textContent = line || ' ';
        fragment.appendChild(paragraph);
      });

      containerRef.current.appendChild(fragment);
    } catch (err) {
      console.error('Error rendering text document:', err);
      setError(err instanceof Error ? err.message : 'Error rendering text document');
    }
  }, [documentReady, documentText]);

  // STEP 3: Add highlight styles
  useEffect(() => {
    if (!styleAddedRef.current) {
      createHighlightStyles();
      styleAddedRef.current = true;
    }
    return () => {
      if (cleanupStylesRef.current) {
        cleanupStylesRef.current();
        cleanupStylesRef.current = null;
        styleAddedRef.current = false;
      }
    };
  }, [createHighlightStyles]);

  // STEP 4: Mark content as rendered and trigger initial citation processing (like MarkdownViewer)
  useEffect(() => {
    let timerId: NodeJS.Timeout | number | undefined;

    if (documentReady && !contentRenderedRef.current) {
      const checkRendered = () => {
        if (
          containerRef.current &&
          (containerRef.current.childNodes.length > 0 || documentText === '')
        ) {
          contentRenderedRef.current = true;
          if (
            !processingCitationsRef.current &&
            citations.length > 0 &&
            !highlightsAppliedRef.current
          ) {
            processCitations();
          }
        } else if (documentReady) {
          timerId = requestAnimationFrame(checkRendered);
        }
      };
      timerId = requestAnimationFrame(checkRendered);
    }

    if (!documentReady) {
      contentRenderedRef.current = false;
    }

    return () => {
      if (typeof timerId === 'number') {
        cancelAnimationFrame(timerId);
      }
    };
  }, [documentReady, documentText, citations, processCitations]);

  // STEP 5: Re-process citations if the `citations` prop changes *content* (like MarkdownViewer)
  useEffect(() => {
    const currentCitationsContent = JSON.stringify(
      citations?.map((c) => normalizeText(c?.content)).sort() ?? []
    );

    if (
      documentReady &&
      contentRenderedRef.current &&
      currentCitationsContent !== prevCitationsJsonRef.current
    ) {
      processCitations();
    }
  }, [citations, documentReady, processCitations]);

  // STEP 6: Clean up highlights when component unmounts
  useEffect(
    () => () => {
      clearHighlights();
      if (cleanupStylesRef.current) {
        cleanupStylesRef.current();
        cleanupStylesRef.current = null;
        styleAddedRef.current = false;
      }
    },
    [clearHighlights]
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
      if (!document.fullscreenElement && fullScreenContainerRef.current) {
        await fullScreenContainerRef.current.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch (err) {
      console.error('Error toggling fullscreen:', err);
    }
  }, []);

  return (
    <TextViewerContainer ref={fullScreenContainerRef} component={Paper} sx={sx}>
      {loading && (
        <LoadingOverlay>
          <CircularProgress size={40} sx={{ mb: 2 }} />
          <Typography variant="body1">Loading document...</Typography>
        </LoadingOverlay>
      )}

      {error && !loading && (
        <ErrorOverlay>
          <Icon icon={alertCircleIcon} style={{ fontSize: 40, marginBottom: '16px' }} />
          <Typography variant="h6">Loading Error</Typography>
          <Typography variant="body1">{error}</Typography>
        </ErrorOverlay>
      )}

      {/* Container for main content and sidebar */}
      <Box
        sx={{
          display: 'flex',
          height: '100%',
          width: '100%',
          visibility: loading || error ? 'hidden' : 'visible',
        }}
      >
        {/* Text Content Area */}
        <Box
          sx={{
            height: '100%',
            flexGrow: 1,
            width:
              !loading && !error && processedCitations.length > 0 ? 'calc(100% - 280px)' : '100%',
            transition: 'width 0.3s ease-in-out',
            position: 'relative',
            borderRight:
              !loading && !error && processedCitations.length > 0
                ? (themeVal) => `1px solid ${themeVal.palette.divider}`
                : 'none',
            overflow: 'hidden',
          }}
        >
          <DocumentContainer ref={containerRef} sx={{ ...scrollableStyles }}>
            {!loading && !error && !documentText && documentReady && (
              <Typography sx={{ p: 3, color: 'text.secondary', textAlign: 'center', mt: 4 }}>
                No document content available to display.
              </Typography>
            )}
          </DocumentContainer>
        </Box>

        {/* Sidebar Area (Conditional) - UPDATED to match MarkdownViewer */}
        {processedCitations.length > 0 && !loading && !error && (
          <Box
            sx={{
              width: '280px',
              height: '100%',
              flexShrink: 0,
              overflowY: 'auto',
            }}
          >
            <CitationSidebar
              citations={processedCitations}
              scrollViewerTo={scrollToHighlight}
              highlightedCitationId={highlightedCitationId}
              toggleFullScreen={toggleFullScreen}
              onClosePdf={onClosePdf}
            />
          </Box>
        )}
      </Box>
    </TextViewerContainer>
  );
};

export default TextViewer;
