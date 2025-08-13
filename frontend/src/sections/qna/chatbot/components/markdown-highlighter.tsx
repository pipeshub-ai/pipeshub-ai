import type { Theme } from '@mui/material';
import type { Components } from 'react-markdown';
import type { CustomCitation } from 'src/types/chat-bot';
import type { Position, HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';
import type {
  SearchResult,
  DocumentContent,
} from 'src/sections/knowledgebase/types/search-response';

import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw'; 
import { Icon } from '@iconify/react';
import ReactMarkdown from 'react-markdown';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import React, { useRef, useState, useEffect, useCallback } from 'react';

import { styled } from '@mui/material/styles';
import { Box, Paper, alpha, useTheme, Typography, CircularProgress } from '@mui/material';

import CitationSidebar from './highlighter-sidebar';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

// Props type definition
type MarkdownViewerProps = {
  citations: DocumentContent[] | CustomCitation[];
  url: string | null;
  content?: string | null;
  buffer?: ArrayBuffer | null;
  sx?: Record<string, unknown>;
  highlightCitation?: SearchResult | CustomCitation | null;
  onClosePdf :() => void;
};

const SIMILARITY_THRESHOLD = 0.6;

const ViewerContainer = styled(Box)(({ theme }) => ({
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

const DocumentContainer = styled(Box)(({ theme }) => ({
  width: '100%',
  height: '100%',
  overflow: 'auto',
  minHeight: '100px',
  padding: '1rem 1.5rem',
  backgroundColor: theme.palette.mode === 'dark' ? 'transparent' : theme.palette.background.paper,

  // Text formatting
  '& p, & li, & blockquote': {
    lineHeight: 1.6,
    color:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.common.white, 0.9)
        : theme.palette.text.primary,
  },

  // Headings
  '& h1, & h2, & h3, & h4, & h5, & h6': {
    marginTop: '1.5em',
    marginBottom: '0.8em',
    color:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.primary.light, 1)
        : theme.palette.text.primary,
  },

  // Tables
  '& table': {
    borderCollapse: 'collapse',
    marginBottom: '1em',
    width: 'auto',
    borderColor:
      theme.palette.mode === 'dark' ? alpha(theme.palette.divider, 0.1) : theme.palette.divider,
  },

  '& th, & td': {
    border:
      theme.palette.mode === 'dark'
        ? `1px solid ${alpha(theme.palette.divider, 0.1)}`
        : '1px solid #ddd',
    padding: '0.5em',
    textAlign: 'left',
  },

  '& th': {
    backgroundColor:
      theme.palette.mode === 'dark' ? alpha(theme.palette.background.default, 0.4) : '#f2f2f2',
    color:
      theme.palette.mode === 'dark' ? theme.palette.primary.lighter : theme.palette.text.primary,
  },

  // Text formatting
  '& strong, & b': {
    fontWeight: 'bold',
    color: theme.palette.mode === 'dark' ? theme.palette.primary.lighter : 'inherit',
  },

  '& em, & i': {
    fontStyle: 'italic',
  },

  // Code blocks
  '& code': {
    fontFamily: 'monospace',
    backgroundColor:
      theme.palette.mode === 'dark' ? alpha(theme.palette.grey[700], 0.6) : 'rgba(0, 0, 0, 0.05)',
    padding: '0.2em 0.4em',
    borderRadius: '3px',
    color: theme.palette.mode === 'dark' ? theme.palette.primary.light : 'inherit',
  },

  '& pre': {
    backgroundColor:
      theme.palette.mode === 'dark' ? alpha(theme.palette.grey[700], 0.6) : 'rgba(0, 0, 0, 0.03)',
    borderRadius: '4px',
    border:
      theme.palette.mode === 'dark'
        ? `1px solid ${alpha(theme.palette.divider, 0.2)}`
        : `1px solid ${alpha(theme.palette.divider, 0.1)}`,
    margin: '1em 0',
  },

  '& pre > code': {
    display: 'block',
    padding: '1em',
    overflowX: 'auto',
    backgroundColor: 'transparent',
    border: 'none',
    color: theme.palette.mode === 'dark' ? alpha(theme.palette.common.white, 0.85) : 'inherit',
  },

  // Syntax highlighting enhancement for dark mode
  '& pre .token.comment': {
    color: theme.palette.mode === 'dark' ? '#6a9955' : 'inherit',
  },
  '& pre .token.string': {
    color: theme.palette.mode === 'dark' ? '#ce9178' : 'inherit',
  },
  '& pre .token.keyword': {
    color: theme.palette.mode === 'dark' ? '#569cd6' : 'inherit',
  },
  '& pre .token.function': {
    color: theme.palette.mode === 'dark' ? '#dcdcaa' : 'inherit',
  },
  '& pre .token.number': {
    color: theme.palette.mode === 'dark' ? '#b5cea8' : 'inherit',
  },

  // Blockquotes
  '& blockquote': {
    borderLeft:
      theme.palette.mode === 'dark'
        ? `4px solid ${alpha(theme.palette.primary.main, 0.6)}`
        : '4px solid #ddd',
    padding: '0.5em 1em',
    margin: '1em 0',
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.background.default, 0.2)
        : 'rgba(0, 0, 0, 0.02)',
  },

  // Links
  '& a': {
    color: theme.palette.primary.main,
    textDecoration: 'none',
    '&:hover': {
      textDecoration: 'underline',
      color:
        theme.palette.mode === 'dark' ? theme.palette.primary.light : theme.palette.primary.dark,
    },
  },
}));

const customRenderers = {} as Components;

// --- Helper functions ---
const getNextId = (): string => `md-hl-${Math.random().toString(36).substring(2, 10)}`;

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

const MarkdownViewer: React.FC<MarkdownViewerProps> = ({
  url,
  content: initialContent,
  buffer,
  sx = {},
  citations = [],
  highlightCitation = null,
  onClosePdf
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [markdownContent, setMarkdownContent] = useState<string>('');
  const [documentReady, setDocumentReady] = useState<boolean>(false);
  const [processedCitations, setProcessedCitations] = useState<ProcessedCitation[]>([]);
  const [highlightedCitationId, setHighlightedCitationId] = useState<string | null>(null);
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
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const fullScreenContainerRef = useRef<HTMLDivElement>(null);

  const createHighlightStyles = useCallback((): (() => void) | undefined => {
    const styleId = 'markdown-highlight-styles';
    if (document.getElementById(styleId)) return undefined;

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
    .markdown-highlight {
      background-color: rgba(59, 130, 246, 0.15); /* Professional blue with low opacity */
      border-radius: 2px;
      padding: 0.1em 0.2em;
      margin: -0.1em 0;
      cursor: pointer;
      transition: all 0.2s ease;
      display: inline;
      box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2);
      position: relative;
      z-index: 1;
      white-space: pre-wrap !important;
      border-bottom: 1px solid rgba(59, 130, 246, 0.3);
    }

    .markdown-highlight:hover {
      background-color: rgba(59, 130, 246, 0.25);
      box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.4);
      z-index: 2;
    }

    .markdown-highlight-active {
      background-color: rgba(59, 130, 246, 0.3) !important;
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.6) !important;
      border-bottom: 2px solid rgba(59, 130, 246, 0.8) !important;
      z-index: 3 !important;
      animation: highlightPulse 0.8s 1 ease-out;
    }

    @keyframes highlightPulse {
      0% { 
        box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.6);
        background-color: rgba(59, 130, 246, 0.3);
      }
      50% { 
        box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.4);
        background-color: rgba(59, 130, 246, 0.4);
      }
      100% { 
        box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.6);
        background-color: rgba(59, 130, 246, 0.3);
      }
    }

    /* Dark mode optimizations */
    [data-mui-color-scheme="dark"] .markdown-highlight,
    .dark .markdown-highlight {
      background-color: rgba(99, 179, 237, 0.2);
      box-shadow: 0 0 0 1px rgba(99, 179, 237, 0.3);
      border-bottom: 1px solid rgba(99, 179, 237, 0.4);
    }

    [data-mui-color-scheme="dark"] .markdown-highlight:hover,
    .dark .markdown-highlight:hover {
      background-color: rgba(99, 179, 237, 0.3);
      box-shadow: 0 0 0 1px rgba(99, 179, 237, 0.5);
    }

    [data-mui-color-scheme="dark"] .markdown-highlight-active,
    .dark .markdown-highlight-active {
      background-color: rgba(99, 179, 237, 0.35) !important;
      box-shadow: 0 0 0 2px rgba(99, 179, 237, 0.7) !important;
      border-bottom: 2px solid rgba(99, 179, 237, 0.9) !important;
      animation: highlightPulseDark 0.8s 1 ease-out;
    }

    @keyframes highlightPulseDark {
      0% { 
        box-shadow: 0 0 0 2px rgba(99, 179, 237, 0.7);
        background-color: rgba(99, 179, 237, 0.35);
      }
      50% { 
        box-shadow: 0 0 0 4px rgba(99, 179, 237, 0.5);
        background-color: rgba(99, 179, 237, 0.45);
      }
      100% { 
        box-shadow: 0 0 0 2px rgba(99, 179, 237, 0.7);
        background-color: rgba(99, 179, 237, 0.35);
      }
    }

    /* Fuzzy matches - slightly different styling */
    .markdown-highlight-fuzzy {
      background-color: rgba(16, 185, 129, 0.12);
      box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.2);
      border-bottom: 1px dashed rgba(16, 185, 129, 0.3);
    }

    .markdown-highlight-fuzzy:hover {
      background-color: rgba(16, 185, 129, 0.2);
      box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.4);
    }

    .markdown-highlight-fuzzy.markdown-highlight-active {
      background-color: rgba(16, 185, 129, 0.25) !important;
      box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.6) !important;
      border-bottom: 2px dashed rgba(16, 185, 129, 0.8) !important;
      animation: highlightPulseFuzzy 0.8s 1 ease-out;
    }

    @keyframes highlightPulseFuzzy {
      0% { 
        box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.6);
        background-color: rgba(16, 185, 129, 0.25);
      }
      50% { 
        box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.4);
        background-color: rgba(16, 185, 129, 0.35);
      }
      100% { 
        box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.6);
        background-color: rgba(16, 185, 129, 0.25);
      }
    }

    /* Dark mode fuzzy matches */
    [data-mui-color-scheme="dark"] .markdown-highlight-fuzzy,
    .dark .markdown-highlight-fuzzy {
      background-color: rgba(52, 211, 153, 0.15);
      box-shadow: 0 0 0 1px rgba(52, 211, 153, 0.25);
      border-bottom: 1px dashed rgba(52, 211, 153, 0.35);
    }

    [data-mui-color-scheme="dark"] .markdown-highlight-fuzzy:hover,
    .dark .markdown-highlight-fuzzy:hover {
      background-color: rgba(52, 211, 153, 0.25);
      box-shadow: 0 0 0 1px rgba(52, 211, 153, 0.45);
    }

    [data-mui-color-scheme="dark"] .markdown-highlight-fuzzy.markdown-highlight-active,
    .dark .markdown-highlight-fuzzy.markdown-highlight-active {
      background-color: rgba(52, 211, 153, 0.3) !important;
      box-shadow: 0 0 0 2px rgba(52, 211, 153, 0.7) !important;
      border-bottom: 2px dashed rgba(52, 211, 153, 0.9) !important;
      animation: highlightPulseFuzzyDark 0.8s 1 ease-out;
    }

    @keyframes highlightPulseFuzzyDark {
      0% { 
        box-shadow: 0 0 0 2px rgba(52, 211, 153, 0.7);
        background-color: rgba(52, 211, 153, 0.3);
      }
      50% { 
        box-shadow: 0 0 0 4px rgba(52, 211, 153, 0.5);
        background-color: rgba(52, 211, 153, 0.4);
      }
      100% { 
        box-shadow: 0 0 0 2px rgba(52, 211, 153, 0.7);
        background-color: rgba(52, 211, 153, 0.3);
      }
    }

    /* Ensure highlights don't interfere with text selection */
    .markdown-highlight::selection {
      background-color: rgba(59, 130, 246, 0.4);
    }

    [data-mui-color-scheme="dark"] .markdown-highlight::selection,
    .dark .markdown-highlight::selection {
      background-color: rgba(99, 179, 237, 0.5);
    }
  `;
    document.head.appendChild(style);

    const cleanup = (): void => {
      const styleElement = document.getElementById(styleId);
      if (styleElement) {
        try {
          document.head.removeChild(styleElement);
        } catch (e) {
          console.error('Error removing highlight styles:', e);
        }
      }
    };
    cleanupStylesRef.current = cleanup;
    return cleanup;
  }, []);

  // Similarity calculation
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

      const highlightClass = `markdown-highlight highlight-${highlightId} markdown-highlight-${matchType}`;
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      let node: Node | null;
      const nodesToWrap: { node: Text; startIndex: number; endIndex: number }[] = [];
      let found = false;

      // eslint-disable-next-line no-constant-condition
      while (true) {
        node = walker.nextNode();
        if (!node) break;

        const textNode = node as Text;
        const nodeText = textNode.nodeValue || '';
        const normalizedNodeText = normalizeText(nodeText);

        const startIndexInNormalized = normalizedNodeText.indexOf(normalizedTextToHighlight);

        if (startIndexInNormalized !== -1) {
          const startIndexInOriginal = nodeText.indexOf(normalizedTextToHighlight.trim());
          if (startIndexInOriginal !== -1) {
            const approxEndIndexInOriginal =
              startIndexInOriginal + normalizedTextToHighlight.length;
            const originalSubstring = nodeText.substring(
              startIndexInOriginal,
              approxEndIndexInOriginal
            );
            if (normalizeText(originalSubstring) === normalizedTextToHighlight) {
              nodesToWrap.push({
                node: textNode,
                startIndex: startIndexInOriginal,
                endIndex: approxEndIndexInOriginal,
              });
              found = true;
              break;
            }
          } else {
            const firstWord = normalizedTextToHighlight.split(' ')[0];
            const simpleStartIndex = nodeText.indexOf(firstWord);
            if (simpleStartIndex !== -1) {
              nodesToWrap.push({
                node: textNode,
                startIndex: simpleStartIndex,
                endIndex: simpleStartIndex + normalizedTextToHighlight.length, 
              });
              found = true;
              break;
            }
          }
        }
      }

      if (found && nodesToWrap.length > 0) {
        const { node: textNode, startIndex, endIndex } = nodesToWrap[0];
        const safeEndIndex = Math.min(endIndex, textNode.nodeValue?.length ?? startIndex);
        if (startIndex >= safeEndIndex) {
          console.warn(
            `Invalid range calculated for highlight ${highlightId} (start >= end). Skipping.`
          );
          return { success: false };
        }

        try {
          const range = document.createRange();
          range.setStart(textNode, startIndex);
          range.setEnd(textNode, safeEndIndex);

          const span = document.createElement('span');
          span.className = highlightClass;
          span.dataset.highlightId = highlightId;

          span.addEventListener('click', (e) => {
            e.stopPropagation();
            containerRef.current?.querySelectorAll('.markdown-highlight-active').forEach((el) => {
              el.classList.remove('markdown-highlight-active');
            });
            span.classList.add('markdown-highlight-active');
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
            `Error surrounding content for highlight ${highlightId}:`,
            wrapError,
            'Normalized Text:',
            normalizedTextToHighlight,
            'Node Content:',
            textNode.nodeValue,
            'Range:',
            `[${startIndex}, ${safeEndIndex}]`
          );
          return { success: false };
        }
      } else if (matchType === 'fuzzy' && element.matches(':not(:has(.markdown-highlight))')) {
        try {
          if (element.classList.contains(`highlight-${highlightId}`)) {
            return { success: false };
          }

          const span = document.createElement('span');
          span.className = highlightClass;
          span.dataset.highlightId = highlightId;

          span.addEventListener('click', (e) => {
            e.stopPropagation();
            containerRef.current?.querySelectorAll('.markdown-highlight-active').forEach((el) => {
              el.classList.remove('markdown-highlight-active');
            });
            span.classList.add('markdown-highlight-active');
            span.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
          });

          while (element.firstChild) {
            span.appendChild(element.firstChild);
          }
          element.appendChild(span);

          const cleanup = () => {
            if (span.parentNode === element) {
              while (span.firstChild) {
                element.insertBefore(span.firstChild, span);
              }
              try {
                element.removeChild(span);
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

    const remainingSpans = containerRef.current.querySelectorAll('.markdown-highlight');
    if (remainingSpans.length > 0) {
      console.warn(
        `Found ${remainingSpans.length} highlight spans remaining after cleanup. Forcing removal.`
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
                console.error('Error during  removal of span:', replaceError, span);
              }
          }
        }
      });
    }
    containerRef.current.querySelectorAll('.markdown-highlight-active').forEach((el) => {
      el.classList.remove('markdown-highlight-active');
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
          const selector =
            'p, li, blockquote, h1, h2, h3, h4, h5, h6, td, th, pre code, span:not(:has(*)), div:not(:has(div, p, ul, ol, blockquote, h1, h2, h3, h4, h5, h6, table, pre))';
          const candidateElements = Array.from(containerRef.current.querySelectorAll(selector));

          if (candidateElements.length === 0) {
            console.warn('No candidate text elements found in container using selector:', selector);
          }

          citationsToHighlight.forEach((citation) => {
            const normalizedText = citation.highlight?.content?.text;
            const highlightId = citation.highlight?.id;

            if (!normalizedText || !highlightId) {
              return;
            }

            const exactMatchFound = candidateElements.some((element) => {
              const normalizedElementText = normalizeText(element.textContent);

              if (normalizedElementText.includes(normalizedText)) {
                const alreadyHighlightedInside = !!element.querySelector(
                  `.highlight-${highlightId}`
                );
                const parentAlreadyHighlighted = element.closest(`.highlight-${highlightId}`);

                if (!alreadyHighlightedInside && !parentAlreadyHighlighted) {
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
              }
              return false;
            });

            // --- Fuzzy Match Fallback ---
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
                const alreadyHighlightedInside = !!bestMatch.element.querySelector(
                  `.highlight-${highlightId}`
                );
                const parentAlreadyHighlighted = bestMatch.element.closest(
                  `.highlight-${highlightId}`
                );

                if (!alreadyHighlightedInside && !parentAlreadyHighlighted) {
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

  const attemptScrollToHighlight = useCallback((highlight: HighlightType | null) => {
    if (!containerRef.current || !highlight || !highlight.id) return;

    const highlightId = highlight.id;
    const selector = `.highlight-${highlightId}`;
    const highlightElement = containerRef.current.querySelector(selector);

    if (highlightElement) {
      containerRef.current.querySelectorAll('.markdown-highlight-active').forEach((el) => {
        el.classList.remove('markdown-highlight-active');
      });
      highlightElement.classList.add('markdown-highlight-active');
      highlightElement.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    } else {
      console.warn(
        `Highlight element with ID ${highlightId} not found for scrolling using selector "${selector}". It might not have been successfully applied.`
      );
    }
  }, []);

  const processCitations = useCallback(() => {
    const currentCitationsContent = JSON.stringify(
      citations?.map((c) => normalizeText(c?.content)).sort() ?? []
    );

    if (
      processingCitationsRef.current ||
      !citations ||
      citations.length === 0 ||
      (currentCitationsContent === prevCitationsJsonRef.current && highlightsAppliedRef.current) // Skip if same content AND highlights are already applied
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
    if (!documentReady || !markdownContent || !containerRef.current) {
      return undefined;
    }

    const observer = new MutationObserver((mutations) => {
      const hasMarkdownContent = containerRef.current?.querySelector(
        'p, h1, h2, h3, h4, h5, h6, ul, ol, blockquote, table, pre'
      );

      if (hasMarkdownContent && !contentRenderedRef.current) {
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
                          ?.querySelectorAll('.markdown-highlight-active')
                          .forEach((el) => el.classList.remove('markdown-highlight-active'));

                        highlightElement.classList.add('markdown-highlight-active');
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
    markdownContent,
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
        containerRef.current.querySelectorAll('.markdown-highlight-active').forEach((el) => {
          el.classList.remove('markdown-highlight-active');
        });

        existingHighlight.classList.add('markdown-highlight-active');
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

          const selector =
            'p, li, blockquote, h1, h2, h3, h4, h5, h6, td, th, pre code, span:not(:has(*)), div:not(:has(div, p, ul, ol, blockquote, h1, h2, h3, h4, h5, h6, table, pre))';
          const candidateElements = Array.from(containerRef.current.querySelectorAll(selector));

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
              containerRef.current?.querySelectorAll('.markdown-highlight-active').forEach((el) => {
                el.classList.remove('markdown-highlight-active');
              });

              targetHighlight.classList.add('markdown-highlight-active');
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
    calculateSimilarity,
    clearHighlights,
    highlightTextInElement,
  ]);

  const scrollToHighlight = useCallback(
    (highlight: HighlightType | null): void => {
      if (!containerRef.current || !highlight || !highlight.id) return;

      const highlightId = highlight.id;

      const findAndScroll = () => {
        const highlightElement = containerRef.current?.querySelector(`.highlight-${highlightId}`);

        if (highlightElement) {
          containerRef.current?.querySelectorAll('.markdown-highlight-active').forEach((el) => {
            el.classList.remove('markdown-highlight-active');
          });

          highlightElement.classList.add('markdown-highlight-active');
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


  // STEP 1: Load markdown content
  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setError(null);
    setMarkdownContent('');
    setDocumentReady(false);
    contentRenderedRef.current = false;
    highlightsAppliedRef.current = false;
    prevCitationsJsonRef.current = '[]';
    clearHighlights();

    const loadContent = async () => {
      try {
        let loadedMd = '';
        if (initialContent) {
          loadedMd = initialContent;
        } else if (buffer) {
          if (buffer instanceof ArrayBuffer) {
            loadedMd = new TextDecoder().decode(buffer);
          } else {
            throw new Error('Provided buffer is not an ArrayBuffer.');
          }
        } else if (url) {
          const response = await fetch(url);
          if (!response.ok) throw new Error(`Fetch failed: ${response.statusText} (URL: ${url})`);
          loadedMd = await response.text();
        } else {
          loadedMd = '';
        }

        if (isMounted) {
          setMarkdownContent(loadedMd);
          setDocumentReady(true);
          setLoading(false);
        }
      } catch (err: any) {
        console.error('Error loading markdown:', err);
        if (isMounted) {
          setError(err.message || 'Failed to load markdown content.');
          setMarkdownContent('');
          setDocumentReady(false);
          setLoading(false);
        }
      }
    };

    loadContent();

    return () => {
      isMounted = false;
    };
  }, [url, initialContent, buffer, clearHighlights]);

  // STEP 2: Add highlight styles
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

  // STEP 3: Mark content as rendered and trigger initial citation processing
  useEffect(() => {
    let timerId: NodeJS.Timeout | number | undefined;

    if (documentReady && !contentRenderedRef.current) {
      const checkRendered = () => {
        if (
          containerRef.current &&
          (containerRef.current.childNodes.length > 0 || markdownContent === '')
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
  }, [documentReady, markdownContent, citations, processCitations]);

  // STEP 4: Re-process citations if the `citations` prop changes *content*
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

  // STEP 5: Ensure highlights are cleared on unmount (Unchanged)
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
    <ViewerContainer ref={fullScreenContainerRef} component={Paper} sx={sx}>
      {loading && (
        <LoadingOverlay>
          <CircularProgress size={40} sx={{ mb: 2 }} />
          <Typography variant="body1">Loading Document...</Typography>
        </LoadingOverlay>
      )}

      {error && !loading && (
        <ErrorOverlay>
          <Icon icon={alertCircleIcon} style={{ fontSize: 40, marginBottom: 16 }} />
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
        {/* Markdown Content Area */}
        <Box
          sx={{
            height: '100%',
            flexGrow: 1,
            width: processedCitations.length > 0 ? 'calc(100% - 280px)' : '100%',
            transition: 'width 0.3s ease-in-out',
            position: 'relative',
            borderRight:
              processedCitations.length > 0
                ? (themeVal: Theme) => `1px solid ${themeVal.palette.divider}`
                : 'none',
          }}
        >
          <DocumentContainer ref={containerRef} sx={{ ...scrollableStyles }}>
            {documentReady && markdownContent && (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={customRenderers}
              >
                {markdownContent}
              </ReactMarkdown>
            )}
            {/* Show message if loading finished without error but content is empty */}
            {!loading && !error && !markdownContent && documentReady && (
              <Typography sx={{ p: 3, color: 'text.secondary', textAlign: 'center', mt: 4 }}>
                No document content available to display.
              </Typography>
            )}
          </DocumentContainer>
        </Box>

        {/* Sidebar Area (Conditional) */}
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
    </ViewerContainer>
  );
};

export default MarkdownViewer;
