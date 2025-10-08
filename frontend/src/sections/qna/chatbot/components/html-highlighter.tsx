import type { Theme } from '@mui/material';
import type { CustomCitation } from 'src/types/chat-bot';
import type { Position, HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';
import type {
  SearchResult,
  DocumentContent,
} from 'src/sections/knowledgebase/types/search-response';

import DOMPurify from 'dompurify';
import { Icon } from '@iconify/react';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import React, { useRef, useState, useEffect, useCallback } from 'react';

import { styled } from '@mui/material/styles';
import { Box, Paper, useTheme, Typography, CircularProgress } from '@mui/material';

import CitationSidebar from './highlighter-sidebar';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

// Props type definition - UPDATED to match MarkdownViewer
type HtmlViewerProps = {
  citations: DocumentContent[] | CustomCitation[];
  url: string | null;
  html?: string | null;
  buffer?: ArrayBuffer | null;
  sx?: Record<string, unknown>;
  highlightCitation?: SearchResult | CustomCitation | null;
  onClosePdf :() => void;
};

const SIMILARITY_THRESHOLD = 0.6;

const HtmlViewerContainer = styled(Box)(({ theme }) => ({
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

const HtmlContentContainer = styled(Box)({
  width: '100%',
  height: '100%',
  overflow: 'auto',
  minHeight: '100px',
  padding: '1rem 1.5rem',
  '& .html-rendered-content': {
    fontFamily:
      "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif",
    lineHeight: 1.6,
    maxWidth: '100%',
    wordWrap: 'break-word',
    '& img': {
      maxWidth: '100%',
      height: 'auto',
    },
    '& pre': {
      backgroundColor: 'rgba(0, 0, 0, 0.05)',
      padding: '1em',
      borderRadius: '3px',
      overflowX: 'auto',
      fontFamily: 'monospace',
    },
    '& code:not(pre > code)': {
      fontFamily: 'monospace',
      backgroundColor: 'rgba(0, 0, 0, 0.05)',
      padding: '0.2em 0.4em',
      borderRadius: '3px',
    },
    '& table': {
      borderCollapse: 'collapse',
      marginBottom: '1em',
      width: 'auto',
    },
    '& th, & td': {
      border: '1px solid #ddd',
      padding: '0.5em',
      textAlign: 'left',
    },
    '& th': {
      backgroundColor: '#f2f2f2',
    },
    '& a': {
      color: '#007bff',
      textDecoration: 'underline',
    },
    '& blockquote': {
      borderLeft: '4px solid #ccc',
      paddingLeft: '1em',
      marginLeft: 0,
      color: '#666',
    },
    '& h1, & h2, & h3, & h4, & h5, & h6': {
      marginTop: '1.5em',
      marginBottom: '0.8em',
    },
  },
});

// --- Helper functions ---
const getNextId = (): string => `html-hl-${Math.random().toString(36).substring(2, 10)}`;

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
      boundingRect: { x1: 0, y1: 0, x2: 0, y2: 0, width: 0, height: 0 },
      rects: [],
    };

    return {
      content: { text: normalizedContent },
      position,
      comment: { text: '', emoji: '' },
      id,
    };
  } catch (error) {
    console.error('Error processing highlight for citation:', citation, error);
    return null;
  }
};
// --- End Helper functions ---

const HtmlViewer: React.FC<HtmlViewerProps> = ({
  url,
  html: initialHtml,
  buffer,
  sx = {},
  citations = [],
  highlightCitation = null,
  onClosePdf
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const contentWrapperRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [documentHtml, setDocumentHtml] = useState<string>('');
  const [documentReady, setDocumentReady] = useState<boolean>(false);
  const [processedCitations, setProcessedCitations] = useState<ProcessedCitation[]>([]);
  const [highlightedCitationId, setHighlightedCitationId] = useState<string | null>(null);
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
    const styleId = 'html-highlight-styles';
    if (document.getElementById(styleId)) return undefined;

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      /* Base highlight style - Professional with warm amber tones */
      .html-highlight {
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
        /* Preserve formatting */
        white-space: pre-wrap !important;
        color: inherit !important;
        text-decoration: none !important;
        /* Subtle text contrast enhancement */
        text-shadow: 0 0 1px rgba(0, 0, 0, 0.1);
      }

      /* Dark mode adjustments - Enhanced visibility with warmer tones */
      @media (prefers-color-scheme: dark) {
        .html-highlight {
          background-color: rgba(255, 213, 79, 0.20); /* Brighter amber for dark backgrounds */
          border-color: rgba(255, 213, 79, 0.35);
          text-shadow: 0 0 1px rgba(0, 0, 0, 0.5);
        }
      }

      /* MUI dark theme class support */
      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .html-highlight,
      [data-theme="dark"] .html-highlight,
      .dark .html-highlight {
        background-color: rgba(255, 213, 79, 0.20); /* Brighter amber for dark backgrounds */
        border-color: rgba(255, 213, 79, 0.35);
        text-shadow: 0 0 1px rgba(0, 0, 0, 0.5);
      }

      .html-highlight:hover {
        background-color: rgba(255, 193, 7, 0.25);
        border-color: rgba(255, 193, 7, 0.45);
        transform: translateY(-0.5px);
        box-shadow: 0 2px 4px rgba(255, 193, 7, 0.2);
        z-index: 2;
      }

      @media (prefers-color-scheme: dark) {
        .html-highlight:hover {
          background-color: rgba(255, 213, 79, 0.30);
          border-color: rgba(255, 213, 79, 0.55);
          box-shadow: 0 2px 4px rgba(255, 213, 79, 0.25);
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .html-highlight:hover,
      [data-theme="dark"] .html-highlight:hover,
      .dark .html-highlight:hover {
        background-color: rgba(255, 213, 79, 0.30);
        border-color: rgba(255, 213, 79, 0.55);
        box-shadow: 0 2px 4px rgba(255, 213, 79, 0.25);
      }

      .html-highlight-active {
        background-color: rgba(255, 152, 0, 0.35) !important; /* Vibrant orange for active state */
        border-color: rgba(255, 152, 0, 0.8) !important;
        border-width: 1.5px !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 3px 8px rgba(255, 152, 0, 0.3) !important;
        z-index: 3 !important;
        animation: htmlHighlightPulse 0.8s 1 ease-out;
      }

      @media (prefers-color-scheme: dark) {
        .html-highlight-active {
          background-color: rgba(255, 183, 77, 0.40) !important; /* Brighter orange for dark mode */
          border-color: rgba(255, 183, 77, 0.9) !important;
          box-shadow: 0 3px 8px rgba(255, 183, 77, 0.35) !important;
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .html-highlight-active,
      [data-theme="dark"] .html-highlight-active,
      .dark .html-highlight-active {
        background-color: rgba(255, 183, 77, 0.40) !important; /* Brighter orange for dark mode */
        border-color: rgba(255, 183, 77, 0.9) !important;
        box-shadow: 0 3px 8px rgba(255, 183, 77, 0.35) !important;
      }

      @keyframes htmlHighlightPulse {
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

      /* Professional fuzzy match styling with green tones */
      .html-highlight-fuzzy {
        background-color: rgba(76, 175, 80, 0.12); /* Soft green */
        border-color: rgba(76, 175, 80, 0.25);
        border-style: dashed;
      }

      @media (prefers-color-scheme: dark) {
        .html-highlight-fuzzy {
          background-color: rgba(129, 199, 132, 0.18); /* Brighter green for dark mode */
          border-color: rgba(129, 199, 132, 0.35);
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .html-highlight-fuzzy,
      [data-theme="dark"] .html-highlight-fuzzy,
      .dark .html-highlight-fuzzy {
        background-color: rgba(129, 199, 132, 0.18); /* Brighter green for dark mode */
        border-color: rgba(129, 199, 132, 0.35);
      }

      .html-highlight-fuzzy:hover {
        background-color: rgba(76, 175, 80, 0.20);
        border-color: rgba(76, 175, 80, 0.40);
        box-shadow: 0 2px 4px rgba(76, 175, 80, 0.15);
      }

      @media (prefers-color-scheme: dark) {
        .html-highlight-fuzzy:hover {
          background-color: rgba(129, 199, 132, 0.25);
          border-color: rgba(129, 199, 132, 0.50);
          box-shadow: 0 2px 4px rgba(129, 199, 132, 0.20);
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .html-highlight-fuzzy:hover,
      [data-theme="dark"] .html-highlight-fuzzy:hover,
      .dark .html-highlight-fuzzy:hover {
        background-color: rgba(129, 199, 132, 0.25);
        border-color: rgba(129, 199, 132, 0.50);
        box-shadow: 0 2px 4px rgba(129, 199, 132, 0.20);
      }

      .html-highlight-fuzzy.html-highlight-active {
        background-color: rgba(67, 160, 71, 0.30) !important; /* Stronger green for active fuzzy */
        border-color: rgba(67, 160, 71, 0.8) !important;
        border-style: dashed !important;
        box-shadow: 0 3px 8px rgba(67, 160, 71, 0.25) !important;
        animation: htmlHighlightPulseFuzzy 0.8s 1 ease-out;
      }

      @media (prefers-color-scheme: dark) {
        .html-highlight-fuzzy.html-highlight-active {
          background-color: rgba(129, 199, 132, 0.35) !important; /* Brighter green for dark mode */
          border-color: rgba(129, 199, 132, 0.9) !important;
          box-shadow: 0 3px 8px rgba(129, 199, 132, 0.30) !important;
        }
      }

      .MuiCssBaseline-root[data-mui-color-scheme="dark"] .html-highlight-fuzzy.html-highlight-active,
      [data-theme="dark"] .html-highlight-fuzzy.html-highlight-active,
      .dark .html-highlight-fuzzy.html-highlight-active {
        background-color: rgba(129, 199, 132, 0.35) !important; /* Brighter green for dark mode */
        border-color: rgba(129, 199, 132, 0.9) !important;
        box-shadow: 0 3px 8px rgba(129, 199, 132, 0.30) !important;
      }

      @keyframes htmlHighlightPulseFuzzy {
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

      /* Reduce motion for accessibility */
      @media (prefers-reduced-motion: reduce) {
        .html-highlight,
        .html-highlight:hover,
        .html-highlight-active,
        .html-highlight-fuzzy,
        .html-highlight-fuzzy:hover,
        .html-highlight-fuzzy.html-highlight-active {
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
          console.error('Error removing HTML highlight styles:', e);
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
      const searchRoot = contentWrapperRef.current || element;
      if (!searchRoot || !normalizedTextToHighlight || normalizedTextToHighlight.length < 3) {
        return { success: false };
      }

      const highlightBaseClass = 'html-highlight';
      const highlightIdClass = `highlight-${highlightId}`;
      const highlightTypeClass = `html-highlight-${matchType}`;
      const fullHighlightClass = `${highlightBaseClass} ${highlightIdClass} ${highlightTypeClass}`;

      const walker = document.createTreeWalker(searchRoot, NodeFilter.SHOW_TEXT, (node) => {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName))
          return NodeFilter.FILTER_REJECT;
        if (parent.closest(`.${highlightBaseClass}.${highlightIdClass}`))
          return NodeFilter.FILTER_REJECT;
        if (!node.textContent?.trim()) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      });

      let node: Node | null;
      const nodesToWrap: { node: Text; startIndex: number; endIndex: number }[] = [];
      let found = false;

      while (true && !found) {
        node = walker.nextNode();
        if (!node) break;
        const textNode = node as Text;
        const nodeText = textNode.nodeValue || '';
        const normalizedNodeText = normalizeText(nodeText);

        let startIndexInNormalized = -1;
        let searchIndex = 0;
        while (searchIndex < normalizedNodeText.length && !found) {
          startIndexInNormalized = normalizedNodeText.indexOf(
            normalizedTextToHighlight,
            searchIndex
          );
          if (startIndexInNormalized === -1) break;
          let originalIndex = -1;
          let normalizedCharsCount = 0;
          let originalCharsCount = 0;
          let inWhitespaceSequence = false;

          while (
            originalCharsCount < nodeText.length &&
            normalizedCharsCount < startIndexInNormalized
          ) {
            const isWhitespace = /\s/.test(nodeText[originalCharsCount]);
            if (isWhitespace) {
              if (!inWhitespaceSequence) {
                normalizedCharsCount += 1;
                inWhitespaceSequence = true;
              }
            } else {
              normalizedCharsCount += 1;
              inWhitespaceSequence = false;
            }
            originalCharsCount += 1;
          }
          while (originalCharsCount < nodeText.length && /\s/.test(nodeText[originalCharsCount])) {
            originalCharsCount += 1;
          }
          if (normalizedCharsCount === startIndexInNormalized) {
            originalIndex = originalCharsCount;
          }

          if (originalIndex !== -1) {
            let approxEndIndex = originalIndex;
            let normalizedLenCount = 0;
            inWhitespaceSequence = false;
            while (
              approxEndIndex < nodeText.length &&
              normalizedLenCount < normalizedTextToHighlight.length
            ) {
              const isWhitespace = /\s/.test(nodeText[approxEndIndex]);
              if (isWhitespace) {
                if (!inWhitespaceSequence) {
                  normalizedLenCount += 1;
                  inWhitespaceSequence = true;
                }
              } else {
                normalizedLenCount += 1;
                inWhitespaceSequence = false;
              }
              approxEndIndex += 1;
            }

            const originalSubstring = nodeText.substring(originalIndex, approxEndIndex);
            if (normalizeText(originalSubstring) === normalizedTextToHighlight) {
              const rangeToCheck = document.createRange();
              try {
                rangeToCheck.setStart(textNode, originalIndex);
                rangeToCheck.setEnd(textNode, approxEndIndex);
                const intersectingHighlights = searchRoot.querySelectorAll(`.${highlightIdClass}`);
                let overlaps = false;
                intersectingHighlights.forEach((existingSpan) => {
                  const existingRange = document.createRange();
                  existingRange.selectNodeContents(existingSpan);
                  if (
                    rangeToCheck.intersectsNode(existingSpan) ||
                    (rangeToCheck.compareBoundaryPoints(Range.START_TO_START, existingRange) >= 0 &&
                      rangeToCheck.compareBoundaryPoints(Range.END_TO_END, existingRange) <= 0) ||
                    (rangeToCheck.compareBoundaryPoints(Range.START_TO_START, existingRange) <= 0 &&
                      rangeToCheck.compareBoundaryPoints(Range.END_TO_END, existingRange) >= 0)
                  ) {
                    overlaps = true;
                  }
                });

                if (!overlaps) {
                  nodesToWrap.push({
                    node: textNode,
                    startIndex: originalIndex,
                    endIndex: approxEndIndex,
                  });
                  found = true;
                } else {
                  searchIndex = startIndexInNormalized + 1;
                }
              } catch (rangeError) {
                console.error('Range error during overlap check:', rangeError);
                searchIndex = startIndexInNormalized + 1;
              }
            } else {
              searchIndex = startIndexInNormalized + 1;
            }
          } else {
            searchIndex = startIndexInNormalized + 1;
          }
        }
      }

      if (found && nodesToWrap.length > 0) {
        const { node: textNode, startIndex, endIndex } = nodesToWrap[0];

        const safeEndIndex = Math.min(endIndex, textNode.nodeValue?.length ?? startIndex);
        if (startIndex >= safeEndIndex) {
          return { success: false };
        }

        try {
          const range = document.createRange();
          range.setStart(textNode, startIndex);
          range.setEnd(textNode, safeEndIndex);

          const span = document.createElement('span');
          span.className = fullHighlightClass;
          span.dataset.highlightId = highlightId;

          span.addEventListener('click', (e) => {
            e.stopPropagation();
            const container = containerRef.current;
            if (!container) return;

            container.querySelectorAll(`.${highlightBaseClass}-active`).forEach((el) => {
              el.classList.remove(`${highlightBaseClass}-active`);
            });
            span.classList.add(`${highlightBaseClass}-active`);
            setHighlightedCitationId(highlightId);
            span.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
          });

          range.surroundContents(span);

          const cleanup = () => {
            if (span.parentNode) {
              const text = document.createTextNode(span.textContent || '');
              try {
                span.parentNode.replaceChild(text, span);
              } catch (replaceError) {
                console.error(
                  `Error replacing highlight span during cleanup (ID: ${highlightId}):`,
                  replaceError
                );
                if (span.parentNode)
                  try {
                    span.parentNode.removeChild(span);
                  } catch (_) {
                    /* ignore */
                  }
              }
            }
          };
          return { success: true, cleanup };
        } catch (wrapError) {
          console.error(
            `Error surrounding content for highlight ${highlightId} (matchType: ${matchType}):`,
            wrapError,
            'Normalized Text:',
            normalizedTextToHighlight,
            'Node Content:',
            `${textNode.nodeValue?.substring(0, 100)}...`,
            'Range:',
            `[${startIndex}, ${safeEndIndex}]`
          );
          return { success: false };
        }
      } else if (matchType === 'fuzzy' && !found && element !== searchRoot) {
        if (
          element.classList.contains(highlightIdClass) ||
          element.querySelector(`.${highlightIdClass}`)
        ) {
          return { success: false };
        }

        try {
          const wrapperSpan = document.createElement('span');
          wrapperSpan.className = fullHighlightClass;
          wrapperSpan.dataset.highlightId = highlightId;

          while (element.firstChild) {
            wrapperSpan.appendChild(element.firstChild);
          }
          element.appendChild(wrapperSpan);

          wrapperSpan.addEventListener('click', (e) => {
            e.stopPropagation();
            const container = containerRef.current;
            if (!container) return;
            container
              .querySelectorAll(`.${highlightBaseClass}-active`)
              .forEach((el) => el.classList.remove(`${highlightBaseClass}-active`));
            wrapperSpan.classList.add(`${highlightBaseClass}-active`);
            setHighlightedCitationId(highlightId);
            wrapperSpan.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
          });

          const cleanup = () => {
            if (wrapperSpan.parentNode === element) {
              while (wrapperSpan.firstChild) {
                element.insertBefore(wrapperSpan.firstChild, wrapperSpan);
              }
              try {
                element.removeChild(wrapperSpan);
              } catch (removeError) {
                console.error(
                  `Error removing fuzzy wrapper during cleanup (ID: ${highlightId}):`,
                  removeError
                );
              }
            }
          };
          return { success: true, cleanup };
        } catch (wrapError) {
          console.error(`Error wrapping element for fuzzy highlight ${highlightId}:`, wrapError);
          return { success: false };
        }
      }

      return { success: false };
    },
    [containerRef, contentWrapperRef]
  );

  const clearHighlights = useCallback(() => {
    const scope = contentWrapperRef.current || containerRef.current;
    if (!scope || highlightingInProgressRef.current) {
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

    const remainingSpans = scope.querySelectorAll('.html-highlight');
    if (remainingSpans.length > 0) {
      console.warn(
        `Found ${remainingSpans.length} HTML highlight spans remaining after cleanup. Forcing removal.`
      );
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
                /* ignore */
              }
          }
        }
      });
    }

    scope.querySelectorAll('.html-highlight-active').forEach((el) => {
      el.classList.remove('html-highlight-active');
    });

    highlightsAppliedRef.current = false;
    highlightingInProgressRef.current = false;
  }, []);

  const applyTextHighlights = useCallback(
    (citationsToHighlight: ProcessedCitation[]): void => {
      const scope = contentWrapperRef.current;
      if (
        !scope ||
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
          if (!contentWrapperRef.current) {
            console.error('Content wrapper ref lost during applyTextHighlights delay.');
            highlightingInProgressRef.current = false;
            return;
          }
          const currentScope = contentWrapperRef.current;

          let appliedCount = 0;
          const newCleanups = new Map<string, () => void>();

          const selector =
            'p, li, blockquote, h1, h2, h3, h4, h5, h6, td, th, pre, span:not(:has(p, li, blockquote, h1, h2, h3, h4, h5, h6, td, th, pre, div)), div:not(:has(p, li, blockquote, h1, h2, h3, h4, h5, h6, td, th, pre, div))';
          const candidateElements = Array.from(currentScope.querySelectorAll(selector));

          if (candidateElements.length === 0) {
            console.warn(
              'No candidate text elements found in HTML content using selector:',
              selector
            );
            if (currentScope.hasChildNodes()) {
              candidateElements.push(currentScope);
              console.log('Using content wrapper as fallback candidate element.');
            }
          }

          citationsToHighlight.forEach((citation) => {
            const normalizedText = citation.highlight?.content?.text;
            const highlightId = citation.highlight?.id;

            if (!normalizedText || !highlightId) {
              return;
            }

            let matchFoundInIteration = false;

            const exactMatchApplied = candidateElements.some((element) => {
              if (
                element.classList.contains(`highlight-${highlightId}`) ||
                element.querySelector(`.highlight-${highlightId}`)
              ) {
                return false;
              }

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
                  matchFoundInIteration = true;
                  return true;
                }
              }
              return false;
            });

            if (!matchFoundInIteration && candidateElements.length > 0) {
              const similarityScores = candidateElements
                .map((el) => {
                  if (
                    el.classList.contains(`highlight-${highlightId}`) ||
                    el.querySelector(`.highlight-${highlightId}`)
                  ) {
                    return { element: el, score: -1 };
                  }
                  return {
                    element: el,
                    score: calculateSimilarity(normalizedText, el.textContent),
                  };
                })
                .filter((item) => item.score >= SIMILARITY_THRESHOLD)
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
                  matchFoundInIteration = true;
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
    [
      documentReady,
      contentRenderedRef,
      clearHighlights,
      highlightTextInElement,
      calculateSimilarity,
    ]
  );

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
        contentWrapperRef.current &&
        documentReady &&
        contentRenderedRef.current &&
        !highlightingInProgressRef.current
      ) {
        requestAnimationFrame(() => applyTextHighlights(processed));
      }
    } catch (err) {
      console.error('Error processing HTML citations:', err);
    } finally {
      processingCitationsRef.current = false;
    }
  }, [citations, documentReady, contentRenderedRef, applyTextHighlights]);

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

  useEffect(() => {
    if (!documentReady || !documentHtml || !containerRef.current) {
      return undefined;
    }

    const observer = new MutationObserver((mutations) => {
      const hasHtmlContent = contentWrapperRef.current?.querySelector(
        'p, h1, h2, h3, h4, h5, h6, ul, ol, blockquote, table, pre, div, span'
      );

      if (hasHtmlContent && !contentRenderedRef.current) {
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
                          ?.querySelectorAll('.html-highlight-active')
                          .forEach((el) => el.classList.remove('html-highlight-active'));

                        highlightElement.classList.add('html-highlight-active');
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
    documentHtml,
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
        containerRef.current.querySelectorAll('.html-highlight-active').forEach((el) => {
          el.classList.remove('html-highlight-active');
        });

        existingHighlight.classList.add('html-highlight-active');
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
          if (!containerRef.current || !contentWrapperRef.current) {
            highlightingInProgressRef.current = false;
            return;
          }

          const selector =
            'p, li, blockquote, h1, h2, h3, h4, h5, h6, td, th, pre, span:not(:has(p, li, blockquote, h1, h2, h3, h4, h5, h6, td, th, pre, div)), div:not(:has(p, li, blockquote, h1, h2, h3, h4, h5, h6, td, th, pre, div))';
          const candidateElements = Array.from(
            contentWrapperRef.current.querySelectorAll(selector)
          );

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
              containerRef.current?.querySelectorAll('.html-highlight-active').forEach((el) => {
                el.classList.remove('html-highlight-active');
              });

              targetHighlight.classList.add('html-highlight-active');
              targetHighlight.scrollIntoView({
                behavior: 'smooth',
                block: 'center',
                inline: 'nearest',
              });
            } else if (attempts < maxAttempts) {
              setTimeout(attemptHighlightAndScroll, baseDelay * attempts); // Increasing delay
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

  // Scrolling Logic
  const scrollToHighlight = useCallback(
    (highlight: HighlightType | null): void => {
      if (!containerRef.current || !highlight || !highlight.id) return;

      const highlightId = highlight.id;

      const findAndScroll = () => {
        const highlightElement = containerRef.current?.querySelector(`.highlight-${highlightId}`);

        if (highlightElement) {
          containerRef.current?.querySelectorAll('.html-highlight-active').forEach((el) => {
            el.classList.remove('html-highlight-active');
          });

          highlightElement.classList.add('html-highlight-active');
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

  // STEP 1: Load and Sanitize HTML Content
  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setError(null);
    setDocumentHtml('');
    setDocumentReady(false);
    contentRenderedRef.current = false;
    highlightsAppliedRef.current = false;
    prevCitationsJsonRef.current = '[]';
    clearHighlights();

    if (contentWrapperRef.current) {
      contentWrapperRef.current.innerHTML = '';
    }

    const loadAndProcessHtml = async () => {
      try {
        let rawHtml = '';
        if (initialHtml) {
          rawHtml = initialHtml;
        } else if (buffer) {
          if (buffer instanceof ArrayBuffer) {
            rawHtml = new TextDecoder().decode(buffer);
          } else {
            throw new Error('Provided buffer is not an ArrayBuffer.');
          }
        } else if (url) {
          const response = await fetch(url);
          if (!response.ok) throw new Error(`Fetch failed: ${response.statusText} (URL: ${url})`);
          rawHtml = await response.text();
        } else {
          console.log('No HTML content source provided, viewer will be empty.');
          rawHtml = '';
        }

        const sanitizedHtml = DOMPurify.sanitize(rawHtml, {
          USE_PROFILES: { html: true },
          ADD_ATTR: ['target', 'id', 'class', 'style', 'href', 'src', 'alt', 'title'],
          ADD_TAGS: ['iframe', 'figure', 'figcaption'], // Add tags if needed
          FORBID_TAGS: ['script', 'style', 'link', 'base'], // Keep style forbidden to avoid conflicts
          FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus', 'autofocus'], // Forbid event handlers
          ALLOWED_URI_REGEXP:
            /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|cid|xmpp|data):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
        });

        if (isMounted) {
          setDocumentHtml(sanitizedHtml);
          setLoading(false);
        }
      } catch (err: any) {
        console.error('Error loading/sanitizing HTML:', err);
        if (isMounted) {
          setError(err.message || 'Failed to load or process HTML content.');
          setDocumentHtml('');
          setDocumentReady(false);
          setLoading(false);
        }
      }
    };

    loadAndProcessHtml();

    return () => {
      isMounted = false;
    };
  }, [url, initialHtml, buffer, clearHighlights]);

  // STEP 2: Render Sanitized HTML into the DOM
  useEffect(() => {
    if (loading || error || !containerRef.current || documentReady) {
      return;
    }

    const container = containerRef.current;
    if (contentWrapperRef.current && contentWrapperRef.current.parentNode === container) {
      container.removeChild(contentWrapperRef.current);
    }
    contentWrapperRef.current = null;

    try {
      const contentDiv = document.createElement('div');
      contentDiv.className = 'html-rendered-content';
      contentDiv.innerHTML = documentHtml;

      container.appendChild(contentDiv);
      contentWrapperRef.current = contentDiv;

      setDocumentReady(true);
      contentRenderedRef.current = true;
    } catch (renderError) {
      console.error('Error rendering sanitized HTML to DOM:', renderError);
      setError('Failed to display HTML content.');
      setDocumentReady(false);
      contentRenderedRef.current = false;
    }
  }, [documentHtml, loading, error, documentReady]);

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

  useEffect(() => {
    let timerId: NodeJS.Timeout | number | undefined;

    if (documentReady && !contentRenderedRef.current) {
      const checkRendered = () => {
        if (
          containerRef.current &&
          (containerRef.current.childNodes.length > 0 || documentHtml === '')
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
  }, [documentReady, documentHtml, citations, processCitations]);

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

  // STEP 6: Ensure highlights and styles are cleared on unmount
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

  // --- Render logic ---
  return (
    <HtmlViewerContainer ref={fullScreenContainerRef} component={Paper} sx={sx}>
      {loading && (
        <LoadingOverlay>
          <CircularProgress size={40} sx={{ mb: 2 }} />
          <Typography variant="body1">Loading Document...</Typography>
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
        {/* HTML Content Area */}
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
                ? (themeVal: Theme) => `1px solid ${themeVal.palette.divider}`
                : 'none',
            overflow: 'hidden',
          }}
        >
          <HtmlContentContainer ref={containerRef} sx={{ ...scrollableStyles }}>
            {!loading && !error && !documentHtml && documentReady && (
              <Typography sx={{ p: 3, color: 'text.secondary', textAlign: 'center', mt: 4 }}>
                No document content available to display.
              </Typography>
            )}
          </HtmlContentContainer>
        </Box>

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
    </HtmlViewerContainer>
  );
};

export default HtmlViewer;
