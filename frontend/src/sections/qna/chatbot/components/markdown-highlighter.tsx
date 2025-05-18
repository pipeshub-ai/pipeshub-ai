import type { Theme } from '@mui/material';
import type { Components } from 'react-markdown';
import type { CustomCitation } from 'src/types/chat-bot';
import type { DocumentContent } from 'src/sections/knowledgebase/types/search-response';
import type { Position, HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';

import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw'; // Handles HTML within Markdown
import { Icon } from '@iconify/react';
import ReactMarkdown from 'react-markdown';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import React, { useRef, useState, useEffect, useCallback } from 'react';

import { styled } from '@mui/material/styles';
import { Box, Paper, Typography, CircularProgress, alpha } from '@mui/material';

import CitationSidebar from './highlighter-sidebar';

// Props type definition
type MarkdownViewerProps = {
  citations: DocumentContent[] | CustomCitation[];
  url: string | null;
  content?: string | null;
  buffer?: ArrayBuffer | null;
  sx?: Record<string, unknown>;
};

// --- Styled components (Unchanged) ---
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
  // color: theme.palette.text.primary,

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
    color: theme.palette.mode === 'dark' ?  alpha(theme.palette.primary.light, 1) : theme.palette.text.primary,
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
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.grey[700], 0.6) 
        : 'rgba(0, 0, 0, 0.05)',
    padding: '0.2em 0.4em',
    borderRadius: '3px',
    color: theme.palette.mode === 'dark' ? theme.palette.primary.light : 'inherit',
  },

  '& pre': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.grey[700], 0.6) 
        : 'rgba(0, 0, 0, 0.03)',
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
    color:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.common.white, 0.85) 
        : 'inherit',
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
// --- End Styled components ---

// Custom renderer (Usually not needed when rehypeRaw is used for standard HTML)
const customRenderers = {} as Components;

// --- Helper functions ---
const getNextId = (): string => `md-hl-${Math.random().toString(36).substring(2, 10)}`;

const isDocumentContent = (
  citation: DocumentContent | CustomCitation
): citation is DocumentContent => 'metadata' in citation && citation.metadata !== undefined;

// *** IMPROVEMENT: Add text normalization helper ***
const normalizeText = (text: string | null | undefined): string => {
  if (!text) return '';
  // Trim, replace multiple whitespace characters (including newlines) with a single space
  return text.trim().replace(/\s+/g, ' ');
};

const processTextHighlight = (citation: DocumentContent | CustomCitation): HighlightType | null => {
  try {
    const rawContent = citation.content;
    // *** IMPROVEMENT: Normalize content here and check length of normalized content ***
    const normalizedContent = normalizeText(rawContent);

    if (!normalizedContent || normalizedContent.length < 5) {
      // Ignore very short/empty citations
      // console.log('Skipping short/empty citation content:', rawContent);
      return null;
    }

    let id: string;
    // Simplified ID assignment (prioritize existing IDs)
    if ('highlightId' in citation && citation.highlightId) id = citation.highlightId as string;
    else if ('id' in citation && citation.id) id = citation.id as string;
    else if ('citationId' in citation && citation.citationId) id = citation.citationId as string;
    else if (isDocumentContent(citation) && citation.metadata?._id) id = citation.metadata._id;
    else if ('_id' in citation && citation._id) id = citation._id as string;
    else id = getNextId(); // Fallback to random ID

    const position: Position = {
      pageNumber: -10, // Indicates it's not from a PDF page
      boundingRect: { x1: 0, y1: 0, x2: 0, y2: 0, width: 0, height: 0 },
      rects: [],
    };

    return {
      // *** Store the *normalized* text for matching, but maybe keep original for display if needed?
      // For simplicity, we'll use normalized for matching. If display needs original, store both.
      content: { text: normalizedContent }, // Use normalized for reliable matching
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

const MarkdownViewer: React.FC<MarkdownViewerProps> = ({
  url,
  content: initialContent,
  buffer,
  sx = {},
  citations = [],
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [markdownContent, setMarkdownContent] = useState<string>('');
  const [documentReady, setDocumentReady] = useState<boolean>(false);
  const [processedCitations, setProcessedCitations] = useState<ProcessedCitation[]>([]);

  // --- Refs (Unchanged) ---
  const styleAddedRef = useRef<boolean>(false);
  const processingCitationsRef = useRef<boolean>(false);
  const contentRenderedRef = useRef<boolean>(false);
  const highlightsAppliedRef = useRef<boolean>(false);
  const highlightingInProgressRef = useRef<boolean>(false);
  const cleanupStylesRef = useRef<(() => void) | null>(null);
  const prevCitationsJsonRef = useRef<string>('[]');
  const highlightCleanupsRef = useRef<Map<string, () => void>>(new Map()); // Keep track of cleanup functions

  // --- Highlighting logic functions ---

  // *** IMPROVEMENT: Enhanced Styles ***
  const createHighlightStyles = useCallback((): (() => void) | undefined => {
    const styleId = 'markdown-highlight-styles';
    if (document.getElementById(styleId)) return undefined; // Already added

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      .markdown-highlight {
        background-color: rgba(255, 224, 130, 0.5); /* Lighter yellow for exact */
        border-radius: 3px;
        padding: 0.1em 0;
        margin: -0.1em 0;
        cursor: pointer;
        transition: background-color 0.3s ease, box-shadow 0.3s ease;
        display: inline; /* Try to keep flow */
        box-shadow: 0 0 0 0px rgba(255, 179, 0, 0.3);
        position: relative;
        z-index: 1;
        /* *** ADDED: Ensure whitespace/breaks within highlight are preserved *** */
        white-space: pre-wrap !important;
      }

      .markdown-highlight:hover {
        background-color: rgba(255, 213, 79, 0.7);
        box-shadow: 0 0 0 2px rgba(255, 179, 0, 0.3);
        z-index: 2;
      }

      .markdown-highlight-active {
        background-color: rgba(255, 179, 0, 0.8) !important; /* Amber/Orange for active */
        box-shadow: 0 0 0 3px rgba(255, 111, 0, 0.4) !important;
        font-weight: 600;
        z-index: 3 !important;
        animation: highlightPulse 1.2s 1 ease-out;
      }

      @keyframes highlightPulse {
        0% { box-shadow: 0 0 0 3px rgba(255, 111, 0, 0.4); }
        50% { box-shadow: 0 0 0 6px rgba(255, 111, 0, 0.1); }
        100% { box-shadow: 0 0 0 3px rgba(255, 111, 0, 0.4); }
      }

      /* *** ADDED: Distinct style for fuzzy matches *** */
      .markdown-highlight-fuzzy {
         background-color: rgba(173, 216, 230, 0.6); /* Light blue */
      }
      .markdown-highlight-fuzzy:hover {
         background-color: rgba(135, 206, 250, 0.8);
      }
      .markdown-highlight-fuzzy.markdown-highlight-active {
         background-color: rgba(70, 130, 180, 0.9) !important; /* Steel Blue */
         box-shadow: 0 0 0 3px rgba(0, 71, 171, 0.5) !important; /* Darker blue shadow */
         animation: highlightPulseFuzzy 1.2s 1 ease-out; /* Optional distinct pulse */
      }

       @keyframes highlightPulseFuzzy {
        0% { box-shadow: 0 0 0 3px rgba(0, 71, 171, 0.5); }
        50% { box-shadow: 0 0 0 6px rgba(0, 71, 171, 0.2); }
        100% { box-shadow: 0 0 0 3px rgba(0, 71, 171, 0.5); }
      }
    `;
    document.head.appendChild(style);

    const cleanup = (): void => {
      const styleElement = document.getElementById(styleId);
      if (styleElement) {
        try {
          document.head.removeChild(styleElement);
          console.log('Removed highlight styles.');
        } catch (e) {
          console.error('Error removing highlight styles:', e);
        }
      }
    };
    cleanupStylesRef.current = cleanup;
    return cleanup;
  }, []);

  // Similarity calculation (unchanged, but consider alternatives like Levenshtein if needed)
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
    // Using Jaccard Index for potentially better results with varying lengths
    return unionSize === 0 ? 0 : intersectionSize / unionSize;
    // Alternative: return intersectionSize / Math.max(words1.size, words2.size);
  }, []);

  // *** IMPROVEMENT: Use normalized text for matching within element ***
  const highlightTextInElement = useCallback(
    (
      element: Element,
      normalizedTextToHighlight: string, // Expect normalized text
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

      // --- Exact Match Search ---
      // eslint-disable-next-line no-constant-condition
      while (true) {
        node = walker.nextNode();
        if (!node) break;

        const textNode = node as Text;
        const nodeText = textNode.nodeValue || '';
        // *** IMPROVEMENT: Normalize the node's text for comparison ***
        const normalizedNodeText = normalizeText(nodeText);

        // Find the *normalized* text within the *normalized* node content
        const startIndexInNormalized = normalizedNodeText.indexOf(normalizedTextToHighlight);

        if (startIndexInNormalized !== -1) {
          // If found in normalized, we need to map the start/end back to the *original* text node
          // This is tricky if normalization changed lengths significantly (e.g., multiple spaces -> one)
          // Simplification: Assume the first occurrence in original text corresponds to the normalized find.
          // This might be inaccurate if the pattern repeats differently before/after normalization.
          const startIndexInOriginal = nodeText.indexOf(normalizedTextToHighlight.trim()); // Try finding the core text
          // A more robust mapping would be needed for complex normalization cases.

          if (startIndexInOriginal !== -1) {
            // Find the *actual* substring in the original text that corresponds
            // This is still imperfect. We really need to find the *range* in the original text
            // that *becomes* the normalizedTextToHighlight after normalization.
            // Let's stick to finding the normalized text within the normalized node text for now,
            // and use the length of the *normalized* text to define the range on the original node.
            // This assumes normalization doesn't drastically reorder things.

            const approxEndIndexInOriginal =
              startIndexInOriginal + normalizedTextToHighlight.length; // This is an approximation!

            // Refined check: does the substring *look* like the target?
            const originalSubstring = nodeText.substring(
              startIndexInOriginal,
              approxEndIndexInOriginal
            );
            if (normalizeText(originalSubstring) === normalizedTextToHighlight) {
              nodesToWrap.push({
                node: textNode,
                startIndex: startIndexInOriginal,
                endIndex: approxEndIndexInOriginal, // Use calculated end based on *normalized* length
              });
              found = true;
              break; // Found the first match in this element, stop searching nodes
            } else {
              // console.warn(`Normalized match found, but original substring mismatch for ID ${highlightId}. Skipping.`);
            }
          } else {
            // Try finding just the first word if the whole thing fails
            const firstWord = normalizedTextToHighlight.split(' ')[0];
            const simpleStartIndex = nodeText.indexOf(firstWord);
            if (simpleStartIndex !== -1) {
              // Less precise fallback - highlight based on approx length
              nodesToWrap.push({
                node: textNode,
                startIndex: simpleStartIndex,
                endIndex: simpleStartIndex + normalizedTextToHighlight.length, // Still approx
              });
              found = true;
              // console.log(`Using approximate start index for highlight ${highlightId}`);
              break;
            }
          }
        }
      }
      // Limitation: This still doesn't handle text split across multiple adjacent text nodes
      // (e.g., `text <b>bold</b> text`). `surroundContents` works on a single node range.

      if (found && nodesToWrap.length > 0) {
        const { node: textNode, startIndex, endIndex } = nodesToWrap[0];
        // Ensure endIndex doesn't exceed node length
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
            // console.log(`Highlight clicked: ${highlightId}`);
            containerRef.current?.querySelectorAll('.markdown-highlight-active').forEach((el) => {
              el.classList.remove('markdown-highlight-active');
            });
            span.classList.add('markdown-highlight-active');
            span.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
          });

          // *** Robustness: Catch errors during DOM manipulation ***
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
                // Attempt simple removal if replace failed
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
      }
      // --- Fuzzy Match Fallback (Wrap whole element) ---
      // This is less ideal but acts as a fallback. Only wrap if no exact match found *within*.
      else if (matchType === 'fuzzy' && element.matches(':not(:has(.markdown-highlight))')) {
        // Only wrap if no highlights exist inside
        // console.log(`Attempting fuzzy wrap for element (ID: ${highlightId})`);
        try {
          // Check if the element itself ALREADY has the highlight class from a previous attempt
          if (element.classList.contains(`highlight-${highlightId}`)) {
            // console.log(`Skipping fuzzy wrap for ${highlightId}, element already has class.`);
            return { success: false };
          }

          const span = document.createElement('span');
          span.className = highlightClass; // Use the fuzzy style class
          span.dataset.highlightId = highlightId;

          // Add click listener to the wrapper span as well
          span.addEventListener('click', (e) => {
            e.stopPropagation();
            // console.log(`Highlight clicked (fuzzy wrapper): ${highlightId}`);
            containerRef.current?.querySelectorAll('.markdown-highlight-active').forEach((el) => {
              el.classList.remove('markdown-highlight-active');
            });
            // Add active class to the wrapper itself
            span.classList.add('markdown-highlight-active');
            span.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
          });

          // Wrap the existing content of the element
          while (element.firstChild) {
            span.appendChild(element.firstChild);
          }
          element.appendChild(span);

          const cleanup = () => {
            if (span.parentNode === element) {
              // Move children back out
              while (span.firstChild) {
                element.insertBefore(span.firstChild, span);
              }
              // Remove the wrapper span
              try {
                element.removeChild(span);
              } catch (removeError) {
                console.error(
                  `Error removing fuzzy wrapper during cleanup (ID: ${highlightId}):`,
                  removeError
                );
              }
            } else {
              // console.warn(`Fuzzy wrapper span parent is not the original element during cleanup (ID: ${highlightId}). Span might have been removed elsewhere.`);
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
  ); // Include containerRef if used inside

  // *** IMPROVEMENT: More robust clearHighlights ***
  const clearHighlights = useCallback(() => {
    if (!containerRef.current || highlightingInProgressRef.current) {
      // console.log("Skipping clearHighlights (no container or already in progress)");
      return;
    }
    highlightingInProgressRef.current = true; // Prevent concurrent modification
    // console.log(`Clearing highlights. ${highlightCleanupsRef.current.size} cleanups registered.`);

    // Run registered cleanup functions
    highlightCleanupsRef.current.forEach((cleanup, id) => {
      try {
        cleanup();
      } catch (e) {
        console.error(`Error running cleanup for highlight ${id}:`, e);
      }
    });
    highlightCleanupsRef.current.clear(); // Clear the map after running

    // Fallback: Find any remaining highlight spans (shouldn't happen if cleanup works)
    const remainingSpans = containerRef.current.querySelectorAll('.markdown-highlight');
    if (remainingSpans.length > 0) {
      console.warn(
        `Found ${remainingSpans.length} highlight spans remaining after cleanup. Forcing removal.`
      );
      remainingSpans.forEach((span) => {
        if (span.parentNode) {
          const textContent = span.textContent || '';
          try {
            // Replace span with its text content
            span.parentNode.replaceChild(document.createTextNode(textContent), span);
          } catch (replaceError) {
            console.error('Error during fallback removal of span:', replaceError, span);
            // If replace fails, just try removing the node
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

    // Remove active classes separately
    containerRef.current.querySelectorAll('.markdown-highlight-active').forEach((el) => {
      el.classList.remove('markdown-highlight-active');
    });

    highlightsAppliedRef.current = false;
    highlightingInProgressRef.current = false; // Release lock
    // console.log('Finished clearing highlights.');
  }, []); // No dependencies needed, operates on refs

  // *** IMPROVEMENT: Use normalized text for matching in applyTextHighlights ***
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
      highlightingInProgressRef.current = true; // Re-acquire lock

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

            // --- Attempt Exact Match First using Array.some() ---
            // The .some() method will stop iterating as soon as the callback returns true.
            const exactMatchFound = candidateElements.some((element) => {
              const normalizedElementText = normalizeText(element.textContent);

              if (normalizedElementText.includes(normalizedText)) {
                const alreadyHighlightedInside = !!element.querySelector(
                  `.highlight-${highlightId}`
                );
                const parentAlreadyHighlighted = element.closest(`.highlight-${highlightId}`);

                if (!alreadyHighlightedInside && !parentAlreadyHighlighted) {
                  // Attempt to highlight
                  const result = highlightTextInElement(
                    element,
                    normalizedText,
                    highlightId,
                    'exact'
                  );

                  if (result.success) {
                    // If successful, increment count, store cleanup, and return true to stop .some()
                    appliedCount += 1;
                    if (result.cleanup) newCleanups.set(highlightId, result.cleanup);
                    return true; // Exit .some() for this citation
                  }
                } else {
                  // console.log(`Skipping exact match for ${highlightId} due to existing highlight.`);
                }
              }
              // If text doesn't include, or it was already highlighted, or highlight failed, return false to continue
              return false;
            });

            // --- Fuzzy Match Fallback ---
            // Only attempt fuzzy if exact match was *not* found by .some()
            if (!exactMatchFound && candidateElements.length > 0) {
              // console.log(`Exact match failed for ${highlightId}. Attempting fuzzy match...`);
              const similarityScores = candidateElements
                .map((el) => ({
                  element: el,
                  score: calculateSimilarity(normalizedText, el.textContent),
                }))
                .filter((item) => item.score > 0.4)
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
                    // No need to set 'success' flag here like before, just track appliedCount
                    if (result.cleanup) newCleanups.set(highlightId, result.cleanup);
                  }
                } else {
                  // console.log(`Skipping fuzzy match for ${highlightId} due to existing highlight.`);
                }
              }
            }

            // if (!exactMatchFound && !fuzzyMatchApplied) { // Need logic to track fuzzy success if required
            //     console.warn(`Could not find/apply highlight for ID: ${highlightId}`);
            // }
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

  // Attempt scroll (unchanged, relies on classes set above)
  const attemptScrollToHighlight = useCallback(
    (highlight: HighlightType | null) => {
      if (!containerRef.current || !highlight || !highlight.id) return;

      const highlightId = highlight.id;
      // Target the specific class added by highlightTextInElement
      const selector = `.highlight-${highlightId}`;
      const highlightElement = containerRef.current.querySelector(selector);

      if (highlightElement) {
        // console.log(`Scrolling to highlight ID: ${highlightId}`);
        containerRef.current.querySelectorAll('.markdown-highlight-active').forEach((el) => {
          el.classList.remove('markdown-highlight-active');
        });
        highlightElement.classList.add('markdown-highlight-active');
        // Use scrollIntoView - options ensure visibility
        highlightElement.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
      } else {
        console.warn(
          `Highlight element with ID ${highlightId} not found for scrolling using selector "${selector}". It might not have been successfully applied.`
        );
        // Option: You could try reapplying highlights here, but be careful of infinite loops.
        // if (!highlightingInProgressRef.current) {
        //   console.log("Triggering re-highlight on scroll failure.");
        //   applyTextHighlights(processedCitations);
        // }
      }
    },
    [] // No dependencies, operates on DOM/refs
  );

  // Scroll coordinator (unchanged, relies on event/flags)
  const scrollToHighlight = useCallback(
    (highlight: HighlightType | null): void => {
      if (!containerRef.current || !highlight || !highlight.id) return;
      // console.log('ScrollToHighlight called for ID:', highlight.id);

      const attemptScroll = () => attemptScrollToHighlight(highlight);

      if (highlightingInProgressRef.current || !highlightsAppliedRef.current) {
        // console.log("Highlighting pending/in progress, waiting for 'highlightsapplied' event.");
        const onHighlightsApplied = () => {
          document.removeEventListener('highlightsapplied', onHighlightsApplied);
          // console.log(`'highlightsapplied' received. Scrolling for ${highlight.id}`);
          setTimeout(attemptScroll, 50); // Small delay after event seems safer
        };
        document.addEventListener('highlightsapplied', onHighlightsApplied);

        // If highlights *definitely* haven't been applied yet, and we're not busy, trigger them.
        if (
          !highlightsAppliedRef.current &&
          !highlightingInProgressRef.current &&
          processedCitations.length > 0
        ) {
          // console.log("Triggering highlight application before scroll.");
          applyTextHighlights(processedCitations);
        }
      } else {
        // console.log('Highlights should be ready. Scrolling directly.');
        attemptScroll();
      }
    },
    [processedCitations, applyTextHighlights, attemptScrollToHighlight] // Include dependencies
  );
  // --- End Highlighting logic functions ---

  // --- Process citations function (Use normalized highlights) ---
  const processCitations = useCallback(() => {
    // Create a stable representation of citation content for comparison
    const currentCitationsContent = JSON.stringify(
      citations?.map((c) => normalizeText(c?.content)).sort() ?? []
    );

    if (
      processingCitationsRef.current ||
      !citations ||
      citations.length === 0 ||
      (currentCitationsContent === prevCitationsJsonRef.current && highlightsAppliedRef.current) // Skip if same content AND highlights are already applied
    ) {
      // Update ref even if skipping processing, to prevent re-processing if citations object changes but content is the same
      if (currentCitationsContent !== prevCitationsJsonRef.current) {
        prevCitationsJsonRef.current = currentCitationsContent;
        highlightsAppliedRef.current = false; // Content changed, need re-apply
        // console.log("Citation content changed, marked for re-highlighting.");
      }
      return;
    }

    processingCitationsRef.current = true;
    // console.log('Processing citations...');

    try {
      const processed: ProcessedCitation[] = citations
        .map((citation) => {
          // processTextHighlight now uses normalization
          const highlight = processTextHighlight(citation);
          if (highlight) {
            // Ensure the original citation object structure is preserved, plus the highlight
            return { ...citation, highlight } as ProcessedCitation;
          }
          return null;
        })
        .filter((item): item is ProcessedCitation => item !== null);

      // console.log(`Processed ${processed.length} valid citations for highlighting.`);
      setProcessedCitations(processed); // Update state with processed citations
      prevCitationsJsonRef.current = currentCitationsContent; // Store the processed content representation
      highlightsAppliedRef.current = false; // Reset flag, highlights need to be (re)applied

      // Trigger apply highlights if content is ready
      if (
        processed.length > 0 &&
        containerRef.current &&
        documentReady &&
        contentRenderedRef.current &&
        !highlightingInProgressRef.current // Ensure not already highlighting
      ) {
        // console.log('Content ready, triggering highlight application after processing citations.');
        // Apply immediately or with minimal delay
        requestAnimationFrame(() => applyTextHighlights(processed));
        // setTimeout(() => applyTextHighlights(processed), 0); // Alternative delay
      } else {
        // console.log('Content not ready or no citations, skipping automatic highlight application trigger.');
      }
    } catch (err) {
      console.error('Error processing citations:', err);
    } finally {
      processingCitationsRef.current = false;
      // console.log('Processing citations finished.');
    }
  }, [citations, documentReady, applyTextHighlights]); // Dependencies: citations object, documentReady state, applyTextHighlights function

  // --- useEffect Hooks ---

  // STEP 1: Load markdown content (Unchanged)
  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setError(null);
    setMarkdownContent('');
    setDocumentReady(false);
    contentRenderedRef.current = false;
    highlightsAppliedRef.current = false;
    prevCitationsJsonRef.current = '[]'; // Reset on new content load
    clearHighlights(); // Clear any old highlights immediately

    const loadContent = async () => {
      // ... (loading logic unchanged) ...
      try {
        let loadedMd = '';
        if (initialContent) {
          loadedMd = initialContent;
        } else if (buffer) {
          // Ensure buffer is ArrayBuffer
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
          // If no source, treat as empty content rather than error? Depends on requirements.
          // setError('No content source provided (URL, content, or buffer).');
          loadedMd = ''; // Set to empty string, allows rendering "No content" message
          console.log('No content source provided, viewer will be empty.');
          // if (isMounted) setLoading(false); // No error, just no content
          // return;
        }

        if (isMounted) {
          setMarkdownContent(loadedMd);
          setDocumentReady(true); // Mark as ready even if content is empty
          setLoading(false);
          // console.log('Markdown content processing finished. Document ready.');
        }
      } catch (err: any) {
        console.error('Error loading markdown:', err);
        if (isMounted) {
          setError(err.message || 'Failed to load markdown content.');
          setMarkdownContent(''); // Ensure content is empty on error
          setDocumentReady(false); // Mark as not ready on error
          setLoading(false);
        }
      }
    };

    loadContent();

    return () => {
      isMounted = false;
      // console.log("MarkdownViewer source effect cleanup.");
    };
  }, [url, initialContent, buffer, clearHighlights]); // Added clearHighlights dependency

  // STEP 2: Add highlight styles (Unchanged)
  useEffect(() => {
    // Add styles only once
    if (!styleAddedRef.current) {
      createHighlightStyles();
      styleAddedRef.current = true;
      // console.log('Highlight styles added.');
    }
    // Cleanup function is returned by createHighlightStyles and managed by cleanupStylesRef
    return () => {
      // This cleanup runs when the component unmounts *or* if createHighlightStyles changes identity (it shouldn't)
      if (cleanupStylesRef.current) {
        cleanupStylesRef.current();
        cleanupStylesRef.current = null; // Ensure it's cleared
        styleAddedRef.current = false;
        // console.log("Highlight styles cleanup executed.");
      }
    };
  }, [createHighlightStyles]); // Dependency on the memoized function

  // STEP 3: Mark content as rendered and trigger initial citation processing
  useEffect(() => {
    let timerId: NodeJS.Timeout | number | undefined;

    // Only proceed if the document is loaded and content *hasn't* been marked as rendered yet
    if (documentReady && !contentRenderedRef.current) {
      const checkRendered = () => {
        // Check if the container exists and has child nodes OR if markdown is explicitly empty
        if (
          containerRef.current &&
          (containerRef.current.childNodes.length > 0 || markdownContent === '')
        ) {
          // console.log('Content considered rendered (has children or is empty).');
          contentRenderedRef.current = true;
          // Now that content is rendered, process citations if needed
          if (
            !processingCitationsRef.current &&
            citations.length > 0 &&
            !highlightsAppliedRef.current
          ) {
            // console.log('Content rendered, triggering initial citation processing.');
            processCitations();
          }
        } else if (documentReady) {
          // If still not rendered, try again shortly
          // console.log('Content not detected in DOM yet, retrying check...');
          timerId = requestAnimationFrame(checkRendered);
          // timerId = setTimeout(checkRendered, 50); // Alternative: setTimeout
        }
      };
      // Start the check
      timerId = requestAnimationFrame(checkRendered);
      // timerId = setTimeout(checkRendered, 50); // Start check after brief delay
    }

    // Reset if document becomes not ready (e.g., loading new content)
    if (!documentReady) {
      contentRenderedRef.current = false; // Reset render flag
    }

    return () => {
      // Cleanup timeout/rAF on unmount or dependency change
      if (typeof timerId === 'number') {
        // clearTimeout(timerId); // Use if using setTimeout
        cancelAnimationFrame(timerId); // Use if using rAF
      }
    };
    // Run when document readiness or markdown content changes, or citations/processCitations potentially trigger a need to run
  }, [documentReady, markdownContent, citations, processCitations]);

  // STEP 4: Re-process citations if the `citations` prop changes *content*
  useEffect(() => {
    // Calculate current citation content representation inside the effect
    const currentCitationsContent = JSON.stringify(
      citations?.map((c) => normalizeText(c?.content)).sort() ?? []
    );

    // Check if content is ready AND citation content has actually changed
    if (
      documentReady &&
      contentRenderedRef.current &&
      currentCitationsContent !== prevCitationsJsonRef.current
    ) {
      console.log('Citations prop content changed, re-processing and applying highlights...');
      // processCitations handles updating the prev ref and triggering applyTextHighlights
      processCitations();
    }
    // This effect specifically handles changes in the CITATIONS prop.
    // processCitations is included as it's called inside.
  }, [citations, documentReady, processCitations]);

  // STEP 5: Ensure highlights are cleared on unmount (Unchanged)
  useEffect(
    () =>
      // This effect's sole purpose is cleanup on unmount
      () => {
        // console.log('MarkdownViewer unmounting, ensuring highlights and styles are cleared.');
        clearHighlights();
        if (cleanupStylesRef.current) {
          // Also ensure styles are removed if component unmounts fast
          cleanupStylesRef.current();
          cleanupStylesRef.current = null;
          styleAddedRef.current = false;
        }
      },
    [clearHighlights]
  ); // Depend on the stable clearHighlights

  // --- Render logic ---
  return (
    <ViewerContainer component={Paper} sx={sx}>
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
          width: '100%', // Ensure Box takes full width
          visibility: loading || error ? 'hidden' : 'visible', // Hide container until loaded/error shown
        }}
      >
        {/* Markdown Content Area */}
        <Box
          sx={{
            height: '100%',
            flexGrow: 1, // Allow content area to take available space
            width: processedCitations.length > 0 ? 'calc(100% - 280px)' : '100%', // Adjust width based on sidebar
            transition: 'width 0.3s ease-in-out',
            position: 'relative', // Needed for potential absolute positioning inside?
            borderRight:
              processedCitations.length > 0
                ? (theme: Theme) => `1px solid ${theme.palette.divider}`
                : 'none',
            // Overflow handled by DocumentContainer inside
          }}
        >
          <DocumentContainer ref={containerRef}>
            {documentReady && markdownContent && (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                // *** IMPORTANT SECURITY NOTE ***
                // rehypeRaw allows rendering raw HTML found in markdown.
                // ONLY use this if you COMPLETELY TRUST the source of your markdown content.
                // If the content can come from users or untrusted sources, it creates
                // a Cross-Site Scripting (XSS) vulnerability.
                // Consider using rehype-sanitize *after* rehype-raw in that case:
                // rehypePlugins={[rehypeRaw, rehypeSanitize]}
                rehypePlugins={[rehypeRaw]}
                components={customRenderers} // Kept, but likely unused if rehypeRaw handles HTML
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
              width: '280px', // Fixed width for the sidebar
              height: '100%',
              flexShrink: 0, // Prevent sidebar from shrinking
              overflowY: 'auto', // Allow sidebar itself to scroll if needed
            }}
          >
            <CitationSidebar
              citations={processedCitations}
              scrollViewerTo={scrollToHighlight} // Pass the stable callback
            />
          </Box>
        )}
      </Box>
    </ViewerContainer>
  );
};

export default MarkdownViewer;
