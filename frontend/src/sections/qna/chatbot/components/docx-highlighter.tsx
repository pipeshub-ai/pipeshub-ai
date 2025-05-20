import type { CustomCitation } from 'src/types/chat-bot';
import type { DocumentContent, SearchResult } from 'src/sections/knowledgebase/types/search-response';
import type { Position, HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';

import { Icon } from '@iconify/react';
import * as docxPreview from 'docx-preview';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import React, { useRef, useState, useEffect, useCallback } from 'react';

import { styled } from '@mui/material/styles';
import { Box, Paper, Typography, CircularProgress } from '@mui/material';

import CitationSidebar from './highlighter-sidebar';

type DocxViewerProps = {
  citations: DocumentContent[] | CustomCitation[];
  url: string | null;
  buffer?: ArrayBuffer | null;
  renderOptions?: Record<string, unknown>;
  sx?: Record<string, unknown>;
  highlightCitation?: SearchResult | CustomCitation | null;
};

// Styled components
const DocViewerContainer = styled(Box)(({ theme }) => ({
  width: '100%',
  height: '100%',
  position: 'relative',
  overflow: 'hidden',
  borderRadius: theme.shape.borderRadius,
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
  backgroundColor: theme.palette.background.paper,
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
  backgroundColor: theme.palette.error.light,
  color: theme.palette.error.contrastText,
  zIndex: 10,
}));

const DocumentContainer = styled(Box)({
  width: '100%',
  height: '100%',
  overflow: 'auto',
  minHeight: '100px', // Ensure the container has a minimum height
});

// Helper function to generate unique IDs
const getNextId = (): string => String(Math.random()).slice(2);

// Helper type guard to check if an object is DocumentContent
const isDocumentContent = (
  citation: DocumentContent | CustomCitation
): citation is DocumentContent => 'metadata' in citation && citation.metadata !== undefined;

// Process citation to create a highlight based on text content
const processTextHighlight = (citation: DocumentContent | CustomCitation): HighlightType | null => {
  try {
    if (!citation.content) {
      console.warn('Citation missing content, skipping highlight');
      return null;
    }

    let id: string;

    if (isDocumentContent(citation) && citation.metadata && citation.metadata._id) {
      id = citation.metadata._id;
    } else if ('id' in citation) {
      id = citation.id as string;
    } else if ('_id' in citation) {
      id = citation._id as string;
    } else if ('citationId' in citation) {
      id = citation.citationId as string;
    } else {
      id = getNextId();
    }

    // Create a complete Position object with required properties
    const position: Position = {
      pageNumber: -10,
      // Add missing required properties
      boundingRect: {
        x1: 0,
        y1: 0,
        x2: 0,
        y2: 0,
        width: 0,
        height: 0,
      },
      rects: [
        {
          x1: 0,
          y1: 0,
          x2: 0,
          y2: 0,
          width: 0,
          height: 0,
        },
      ],
    };

    return {
      content: {
        text: citation.content,
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

const DocxViewer: React.FC<DocxViewerProps> = ({
  url,
  buffer,
  renderOptions = {},
  sx = {},
  citations = [],
  highlightCitation = null,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [documentReady, setDocumentReady] = useState<boolean>(false);
  const scrollViewerToRef = useRef<(highlight: HighlightType) => void>(() => {});
  const [processedCitations, setProcessedCitations] = useState<ProcessedCitation[]>([]);
  const renderAttemptedRef = useRef<boolean>(false);
  const styleAddedRef = useRef<boolean>(false);
  const processingCitationsRef = useRef<boolean>(false);
  const highlightAppliersRef = useRef<(() => void)[]>([]);
  const [highlightedCitationId, setHighlightedCitationId] = useState<string | null>(null);

  // STEP 1: Render document only once
  useEffect(() => {
    // Skip if we've already tried rendering or document is ready
    if (renderAttemptedRef.current || documentReady) {
      return;
    }

    renderAttemptedRef.current = true;

    // Improved and more efficient attemptRender function
    const attemptRender = async (): Promise<boolean> => {
      if (!containerRef.current) {
        return false;
      }

      try {
        // Clear container
        containerRef.current.innerHTML = '';

        // Default render options with better performance settings
        const defaultOptions = {
          className: 'docx',
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          ignoreFonts: false,
          breakPages: true,
          ignoreLastRenderedPageBreak: true,
          experimental: false,
          trimXmlDeclaration: true,
          useBase64URL: false,
          renderChanges: false,
          renderHeaders: true,
          renderFooters: true,
          renderFootnotes: true,
          renderEndnotes: true,
          renderComments: false,
          renderAltChunks: true,
          debug: true,
        };

        // Merge options
        const options = { ...defaultOptions, ...renderOptions };

        // Check document source
        if (!url && !buffer) {
          throw new Error('Either url or buffer must be provided');
        }

        // Render document
        if (url) {
          try {
            // Use a timeout to prevent hanging on unresponsive URLs
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

            // Fetch the file with timeout
            const response = await fetchWithTimeout(url);

            if (!response.ok) {
              throw new Error(
                `Failed to fetch document: ${response.status} ${response.statusText}`
              );
            }

            // Get content type to verify it's a DOCX file
            const contentType = response.headers.get('content-type');
            if (
              contentType &&
              !contentType.includes(
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
              ) &&
              !contentType.includes('application/octet-stream')
            ) {
              console.warn(`Warning: Content type "${contentType}" may not be a valid DOCX file`);
            }

            // Get the document as an ArrayBuffer (more efficient than Blob)
            const fileBuffer = await response.arrayBuffer();

            if (!fileBuffer || fileBuffer.byteLength === 0) {
              throw new Error('Received empty file from URL');
            }

            // Render using the buffer
            await docxPreview.renderAsync(fileBuffer, containerRef.current, undefined, options);
          } catch (urlError) {
            if (urlError.name === 'AbortError') {
              throw new Error('Document fetch timed out. Check if the URL is accessible.');
            }
            throw new Error(`Failed to process document: ${urlError.message}`);
          }
        } else if (buffer) {
          await docxPreview.renderAsync(buffer, containerRef.current, undefined, options);
        }

        // Add IDs to elements for easier highlighting - Use a more efficient approach with a single pass
        if (containerRef.current) {
          // Create a DocumentFragment for better performance when adding IDs
          const addElementIds = (selector: string, prefix: string) => {
            const elements = containerRef.current?.querySelectorAll(selector);
            if (elements?.length) {
              elements.forEach((element, index) => {
                if (!element.id) {
                  element.id = `${prefix}-${index}`;
                }
              });
            }
          };

          // Add IDs in a single pass for each element type
          addElementIds('p:not([id])', 'p');
          addElementIds('span:not([id])', 'span');
          addElementIds('div:not([id]):not(:has(p, div))', 'div');
        }

        setLoading(false);
        setDocumentReady(true);
        return true;
      } catch (err) {
        console.error('Error rendering document:', err);
        const errorMessage =
          err instanceof Error ? err.message : 'Unknown error rendering document';
        setError(errorMessage);
        setLoading(false);
        return false;
      }
    };

    // First attempt - if container is already available
    if (containerRef.current) {
      attemptRender();
      // eslint-disable-next-line
      return;
    }

    // If container not available yet, set up a limited retry mechanism
    let attempts = 0;
    const maxAttempts = 10;

    const checkInterval = setInterval(async () => {
      attempts += 1;

      if ((await attemptRender()) || attempts >= maxAttempts) {
        clearInterval(checkInterval);

        if (attempts >= maxAttempts && !documentReady) {
          console.error('Max render attempts reached without success');
          setError('Failed to render document after multiple attempts');
          setLoading(false);
        }
      }
    }, 500);
    // eslint-disable-next-line
    const cleanup = (): void => {
      clearInterval(checkInterval);
    };
    // eslint-disable-next-line
    return cleanup;
  }, [url, buffer, renderOptions, documentReady]);

  // STEP 2: Add highlight styles once
  useEffect(() => {
    if (styleAddedRef.current) return;

    styleAddedRef.current = true;
    createHighlightStyles();
  }, []);

  // Create highlight styles function
  const createHighlightStyles = (): (() => void) | undefined => {
    const styleId = 'docx-highlight-styles';

    // Check if style already exists
    if (document.getElementById(styleId)) {
      return undefined; // Explicit return for consistent return type
    }

    // Create style element with enhanced highlighting styles
    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      /* Base highlight style */
      .docx-highlight {
        cursor: pointer;
        background-color: rgba(255, 235, 59, 0.5) !important; /* Bright yellow with transparency */
        position: relative;
        z-index: 1;
        border-radius: 2px;
        box-shadow: 0 0 0 1px rgba(255, 193, 7, 0.3);
        transition: all 0.25s ease;
        text-decoration: none !important;
        color: inherit !important;
        border-bottom: 2px solid #FFC107;
      }
      
      /* Hover state */
      .docx-highlight:hover {
        background-color: rgba(255, 193, 7, 0.6) !important; /* Amber on hover */
        box-shadow: 0 0 0 2px rgba(255, 152, 0, 0.4);
        z-index: 2;
      }
   
      /* Active state */
      .docx-highlight-active {
        background-color: rgba(255, 152, 0, 0.7) !important; /* Orange for active */
        box-shadow: 0 0 0 3px rgba(255, 87, 34, 0.4) !important;
        font-weight: bold !important;
        z-index: 3 !important;
        color: inherit !important;
        border-bottom: 2px solid #FF5722;
      }
      
      /* Animation */
      .highlight-pulse {
        animation: highlightPulse 1.5s 1;
      }
      
      @keyframes highlightPulse {
        0% { box-shadow: 0 0 0 3px rgba(255, 87, 34, 0.3); }
        50% { box-shadow: 0 0 0 6px rgba(255, 87, 34, 0.1); }
        100% { box-shadow: 0 0 0 3px rgba(255, 87, 34, 0.3); }
      }
      
      /* Custom highlight styles for different types of matches */
      .docx-highlight-exact {
        background-color: rgba(255, 235, 59, 0.6) !important; /* Brighter yellow for exact matches */
        border-bottom: 2px solid #FFC107;
      }
      
      .docx-highlight-partial {
        background-color: rgba(156, 204, 101, 0.4) !important; /* Light green for partial matches */
        border-bottom: 2px dashed #8BC34A;
      }
      
      .docx-highlight-fuzzy {
        background-color: rgba(187, 222, 251, 0.5) !important; /* Light blue for fuzzy matches */
        border-bottom: 2px dotted #2196F3;
      }
      
      /* Fix for docx-preview styling conflicts */
      .docx .docx-highlight * {
        color: inherit !important;
        background-color: inherit !important;
      }
      
      /* Make sure highlights preserve line breaks and formatting */
      .docx-highlight {
        display: inline !important;
        white-space: pre-wrap !important;
      }
    `;

    document.head.appendChild(style);

    // Create cleanup function
    const cleanup = (): void => {
      const styleElement = document.getElementById(styleId);
      if (styleElement) {
        document.head.removeChild(styleElement);
      }
    };

    return cleanup;
  };

  // STEP 3: Process citations when document is ready
  // Replace the citation processing code block with this corrected version

  // STEP 3: Process citations when document is ready
  useEffect(() => {
    // Skip if document not ready or already processing citations
    if (!documentReady || processingCitationsRef.current || !citations?.length) {
      return;
    }

    processingCitationsRef.current = true;

    try {
      // Clear previous highlight appliers
      highlightAppliersRef.current = [];

      // Create a properly typed array from the start
      const processed: ProcessedCitation[] = [];

      // Process citations into highlights with type safety
      citations.forEach((citation) => {
        const highlight = processTextHighlight(citation);

        // Only add items where highlight is not null
        if (highlight) {
          // Create a properly typed ProcessedCitation
          const processedCitation: ProcessedCitation = {
            ...citation,
            highlight,
          } as ProcessedCitation; // Cast is necessary because we're merging DocumentContent with highlight

          processed.push(processedCitation);
        }
      });

      setProcessedCitations(processed);

      // Apply highlights after a short delay to ensure document is fully rendered
      if (processed.length > 0 && containerRef.current) {
        setTimeout(() => {
          applyTextHighlights(processed);
        }, 500);
      }
    } catch (err) {
      console.error('Error processing citations:', err);
    } finally {
      // Even if there's an error, mark processing as complete
      // to allow future attempts when the document changes
      processingCitationsRef.current = false;
    }
    // eslint-disable-next-line
  }, [documentReady, citations]);

  // Helper function to calculate text similarity (simple word overlap for now)
  const calculateSimilarity = (text1: string, text2: string): number => {
    const words1 = text1.toLowerCase().split(/\s+/);
    const words2 = text2.toLowerCase().split(/\s+/);

    const uniqueWords1 = new Set(words1);
    const uniqueWords2 = new Set(words2);

    let matchCount = 0;
    uniqueWords1.forEach((word) => {
      if (word.length > 3 && uniqueWords2.has(word)) {
        matchCount += 1;
      }
    });

    return matchCount / Math.max(uniqueWords1.size, uniqueWords2.size);
  };

  // Extract click handler to a separate function for reuse
  const addHighlightClickHandler = (element: HTMLElement, highlightId: string): void => {
    element.addEventListener('click', () => {
      // Remove active class from all highlights
      document.querySelectorAll('.docx-highlight-active').forEach((el) => {
        el.classList.remove('docx-highlight-active');
        el.classList.remove('highlight-pulse');
      });

      // Add active class to this highlight
      element.classList.add('docx-highlight-active');
      element.classList.add('highlight-pulse');

      // Scroll into view with offset to ensure visibility
      element.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    });
  };

  // Improved text highlighting function that uses different styles for different match types
  const highlightTextInElement = (
    element: Element,
    text: string,
    highlightId: string,
    matchType: 'exact' | 'partial' | 'fuzzy' = 'exact'
  ): boolean => {
    if (!element || !text) return false;

    try {
      // Find where the text appears in the element
      const elementContent = element.textContent || '';
      let textIndex = elementContent.indexOf(text);

      // Determine the highlight class based on match type
      const highlightClass = `docx-highlight highlight-${highlightId} docx-highlight-${matchType}`;

      // If text is not found directly and we're doing a fuzzy match
      if (textIndex === -1 && matchType === 'fuzzy') {
        const normalizedElementContent = elementContent.replace(/\s+/g, ' ').trim();
        const normalizedText = text.replace(/\s+/g, ' ').trim();
        textIndex = normalizedElementContent.indexOf(normalizedText);

        if (textIndex === -1) {
          return false;
        }

        // For fuzzy matches, we'll wrap the whole element
        try {
          const wrapper = document.createElement('span');
          wrapper.className = highlightClass;
          wrapper.textContent = elementContent;
          wrapper.dataset.id = highlightId;
          wrapper.dataset.matchType = 'fuzzy';

          element.textContent = '';
          element.appendChild(wrapper);

          // Store a cleanup function
          highlightAppliersRef.current.push(() => {
            try {
              if (element && wrapper.parentNode === element) {
                element.textContent = wrapper.textContent;
              }
            } catch (e) {
              console.error('Error in cleanup', e);
            }
          });

          // Add click handler
          addHighlightClickHandler(wrapper, highlightId);

          return true;
        } catch (e) {
          console.error('Error wrapping element', e);
          return false;
        }
      }

      // Standard approach using text nodes for exact matches
      if (textIndex >= 0) {
        const textNodes: Node[] = [];
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null);

        // Fix ESLint no-cond-assign issue with while loop
        let currentNode = walker.nextNode();
        while (currentNode !== null) {
          textNodes.push(currentNode);
          currentNode = walker.nextNode();
        }

        if (textNodes.length === 0) return false;

        // Find which text node contains our text
        let currentNodeStart = 0;
        let startNode: Node | null = null;
        let startOffset = 0;
        let endNode: Node | null = null;
        let endOffset = 0;

        // Fix ESLint no-restricted-syntax issue with for...of
        textNodes.forEach((nodeEl) => {
          const nodeText = nodeEl.nodeValue || '';
          const nodeLength = nodeText.length;

          // Check if this node contains the start of our text
          if (
            !startNode &&
            textIndex >= currentNodeStart &&
            textIndex < currentNodeStart + nodeLength
          ) {
            startNode = nodeEl;
            startOffset = textIndex - currentNodeStart;
          }

          // Check if this node contains the end of our text
          const textEnd = textIndex + text.length;
          if (!endNode && textEnd > currentNodeStart && textEnd <= currentNodeStart + nodeLength) {
            endNode = nodeEl;
            endOffset = textEnd - currentNodeStart;
          }

          // Skip remaining iterations if we found both nodes
          if (startNode && endNode) {
            return; // Early return from forEach
          }

          currentNodeStart += nodeLength;
        });

        // If we found both start and end nodes
        if (startNode && endNode) {
          // Create a range and highlight it
          const range = document.createRange();
          range.setStart(startNode, startOffset);
          range.setEnd(endNode, endOffset);

          const span = document.createElement('span');
          span.className = highlightClass;
          span.dataset.id = highlightId;
          span.dataset.matchType = matchType;

          try {
            range.surroundContents(span);

            // Add click handler
            addHighlightClickHandler(span, highlightId);

            return true;
          } catch (e) {
            console.error('Error applying range highlight', e);

            // Fallback method when range.surroundContents fails
            // Create a new span and replace text content
            try {
              const textToHighlight = elementContent.substring(textIndex, textIndex + text.length);
              const beforeText = elementContent.substring(0, textIndex);
              const afterText = elementContent.substring(textIndex + text.length);

              // Clear the element
              element.textContent = '';

              // Add the before text if it exists
              if (beforeText) {
                element.appendChild(document.createTextNode(beforeText));
              }

              // Add the highlighted text
              const highlightSpan = document.createElement('span');
              highlightSpan.className = highlightClass;
              highlightSpan.textContent = textToHighlight;
              highlightSpan.dataset.id = highlightId;
              highlightSpan.dataset.matchType = matchType;
              element.appendChild(highlightSpan);

              // Add the after text if it exists
              if (afterText) {
                element.appendChild(document.createTextNode(afterText));
              }

              // Add click handler
              addHighlightClickHandler(highlightSpan, highlightId);

              // Store cleanup function
              highlightAppliersRef.current.push(() => {
                try {
                  if (element) {
                    element.textContent = elementContent;
                  }
                } catch (err) {
                  console.error('Error in cleanup', err);
                }
              });

              return true;
            } catch (fallbackError) {
              console.error('Fallback highlighting failed', fallbackError);
              return false;
            }
          }
        }
      }

      return false;
    } catch (err) {
      console.error('Error in highlight function:', err);
      return false;
    }
  };

  // Improved function to apply text-based highlights with fuzzy matching
  const applyTextHighlights = (citationsArray: ProcessedCitation[]): void => {
    if (!containerRef.current) return;

    // Clear existing highlights
    clearHighlights();

    citationsArray.forEach((citation) => {
      if (!citation.highlight) return;

      const { text } = citation.highlight.content;
      if (!text || text.length < 10) {
        return;
      }

      try {
        // Find all text elements in the document
        const selector = 'p, div:not(:has(p)), span:not(:has(span))';
        const textElements = containerRef.current?.querySelectorAll(selector);

        if (!textElements) return;

        // Find elements containing the target text
        let matchFound = false;

        // Normalize the citation text for better matching
        const normalizedText = text.replace(/\s+/g, ' ').trim();

        // Try exact matching first
        const exactMatches = Array.from(textElements).filter((el) => {
          const content = el.textContent || '';
          return content.includes(text);
        });

        if (exactMatches.length > 0) {
          // Sort to find best match (closest length)
          exactMatches.sort((a, b) => {
            const aLength = (a.textContent || '').length;
            const bLength = (b.textContent || '').length;
            return Math.abs(aLength - text.length) - Math.abs(bLength - text.length);
          });

          // Highlight the best match
          if (highlightTextInElement(exactMatches[0], text, citation.highlight.id, 'exact')) {
            matchFound = true;
            return;
          }
        }

        // If exact match fails, try normalized text matching
        if (!matchFound) {
          const normalizedMatches = Array.from(textElements).filter((el) => {
            const content = (el.textContent || '').replace(/\s+/g, ' ').trim();
            return content.includes(normalizedText);
          });

          if (normalizedMatches.length > 0) {
            // Sort and highlight
            normalizedMatches.sort((a, b) => {
              const aLength = (a.textContent || '').replace(/\s+/g, ' ').trim().length;
              const bLength = (b.textContent || '').replace(/\s+/g, ' ').trim().length;
              return (
                Math.abs(aLength - normalizedText.length) -
                Math.abs(bLength - normalizedText.length)
              );
            });

            if (
              highlightTextInElement(
                normalizedMatches[0],
                normalizedText,
                citation.highlight.id,
                'fuzzy'
              )
            ) {
              matchFound = true;
              return;
            }
          }
        }

        // If still no match, try partial matching
        if (!matchFound && text.length > 50) {
          // Try multiple chunks
          const chunks = [
            text.substring(0, Math.min(100, Math.floor(text.length / 3))),
            text.substring(Math.floor(text.length / 3), Math.floor((2 * text.length) / 3)),
            text.substring(Math.floor((2 * text.length) / 3)),
          ];

          let chunkMatchCount = 0;

          chunks.forEach((chunk, i) => {
            if (chunk.length < 20) return; // Skip chunks that are too short

            const chunkMatches = Array.from(textElements).filter((el) =>
              (el.textContent || '').includes(chunk)
            );

            if (chunkMatches.length > 0) {
              if (citation.highlight) {
                if (
                  highlightTextInElement(
                    chunkMatches[0],
                    chunk,
                    `${citation.highlight.id}-chunk-${i}`,
                    'partial'
                  )
                ) {
                  chunkMatchCount += 1;
                }
              }
            }
          });

          if (chunkMatchCount > 0) {
            matchFound = true;
          }
        }

        // If still no match, try fuzzy string matching with a tolerance
        if (!matchFound && text.length > 30) {
          // Try a simple approach - look for elements with substantial text overlap (e.g., 70% of words match)
          const textWords = normalizedText.split(' ');
          const minMatchWords = Math.ceil(textWords.length * 0.7);

          const fuzzyMatches = Array.from(textElements).filter((el) => {
            const content = (el.textContent || '').replace(/\s+/g, ' ').trim();
            const contentWords = content.split(' ');
            let matchCount = 0;

            textWords.forEach((word: any) => {
              if (word.length > 3 && contentWords.includes(word)) {
                matchCount += 1;
              }
            });

            return matchCount >= minMatchWords;
          });

          if (fuzzyMatches.length > 0) {
            // Sort by best match
            fuzzyMatches.sort((a, b) => {
              const aContent = (a.textContent || '').replace(/\s+/g, ' ').trim();
              const bContent = (b.textContent || '').replace(/\s+/g, ' ').trim();

              const aSimilarity = calculateSimilarity(aContent, normalizedText);
              const bSimilarity = calculateSimilarity(bContent, normalizedText);

              return bSimilarity - aSimilarity; // Higher similarity first
            });

            if (
              fuzzyMatches[0].textContent &&
              highlightTextInElement(
                fuzzyMatches[0],
                fuzzyMatches[0].textContent,
                citation.highlight.id,
                'fuzzy'
              )
            ) {
              matchFound = true;
            }
          }
        }

      } catch (err) {
        console.error('Error applying highlight:', err);
      }
    });
  };

  // Clear all highlights
  const clearHighlights = (): void => {
    // Get all highlight elements
    const highlightElements = containerRef.current?.querySelectorAll('.docx-highlight');
    if (highlightElements?.length) {
      highlightElements.forEach((el) => {
        try {
          const parent = el.parentNode;
          if (parent) {
            const textNode = document.createTextNode(el.textContent || '');
            parent.replaceChild(textNode, el);
          }
        } catch (err) {
          console.error('Error removing highlight', err);
        }
      });
    }

    // Execute custom cleanup functions if any were stored
    highlightAppliersRef.current.forEach((cleanup) => {
      try {
        if (typeof cleanup === 'function') cleanup();
      } catch (e) {
        console.error('Error in highlight cleanup', e);
      }
    });

    // Reset the list
    highlightAppliersRef.current = [];
  };

  // Scroll to highlight function
  const scrollToHighlight = useCallback((highlight: HighlightType): void => {
    if (!containerRef.current || !highlight) return;

    const highlightId = highlight.id;
    const highlightElement = containerRef.current.querySelector(`.highlight-${highlightId}`);

    if (highlightElement) {
      // Remove active class from all highlights
      document.querySelectorAll('.docx-highlight-active').forEach((el) => {
        el.classList.remove('docx-highlight-active');
        el.classList.remove('highlight-pulse');
      });

      // Add active class to this highlight
      highlightElement.classList.add('docx-highlight-active');
      highlightElement.classList.add('highlight-pulse');

      // Scroll to the highlight
      highlightElement.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    } else {
      console.warn(`Highlight element with ID ${highlightId} not found`);

      // Try to find partial highlights if they exist
      for (let i = 0; i < 3; i += 1) {
        const chunkHighlight = containerRef.current.querySelector(
          `.highlight-${highlightId}-chunk-${i}`
        );
        if (chunkHighlight) {
          document.querySelectorAll('.docx-highlight-active').forEach((el) => {
            el.classList.remove('docx-highlight-active');
            el.classList.remove('highlight-pulse');
          });

          chunkHighlight.classList.add('docx-highlight-active');
          chunkHighlight.classList.add('highlight-pulse');

          chunkHighlight.scrollIntoView({
            behavior: 'smooth',
            block: 'center',
          });

          return;
        }
      }
    }
  }, []);

  // Set up scroll function ref - only once
  useEffect(() => {
    scrollViewerToRef.current = scrollToHighlight;
  }, [scrollToHighlight]);

  // Clean up highlights when component unmounts
  useEffect(
    () => () => {
      clearHighlights();
    },
    []
  );

  useEffect(() => {
    if (!highlightCitation) return;

    // Process the citation to extract its ID based on its type
    let highlightId: string | null = null;

    if (isDocumentContent(highlightCitation) && highlightCitation.metadata?._id) {
      highlightId = highlightCitation.metadata._id;
    } else if ('id' in highlightCitation) {
      highlightId = highlightCitation.id as string;
    } else if ('_id' in highlightCitation) {
      highlightId = highlightCitation._id as string;
    } else if ('citationId' in highlightCitation) {
      highlightId = highlightCitation.citationId as string;
    }

    if (highlightId) {
      setHighlightedCitationId(highlightId);
    }
  }, [highlightCitation]);

  useEffect(() => {
    if (!documentReady || !highlightedCitationId) return;


    // If we don't have processed citations yet, wait for them
    if (!processedCitations.length) {
      return;
    }

    // Find the processed citation that matches our ID
    const targetCitation = processedCitations.find(
      (citation) => citation.highlight?.id === highlightedCitationId
    );

    if (targetCitation?.highlight) {

      // Use a delay to ensure highlights are applied first
      setTimeout(() => {
        if (targetCitation.highlight) {
          scrollToHighlight(targetCitation.highlight);
        }
      }, 1000); // Longer delay to ensure document is fully processed
    } 
  }, [documentReady, processedCitations, highlightedCitationId, scrollToHighlight]);

  return (
    <DocViewerContainer component={Paper}>
      {loading && (
        <LoadingOverlay>
          <CircularProgress size={40} sx={{ mb: 2 }} />
          <Typography variant="body1">Loading document...</Typography>
        </LoadingOverlay>
      )}

      {error && (
        <ErrorOverlay>
          <Icon icon={alertCircleIcon} style={{ fontSize: 40, marginBottom: 16 }} />
          <Typography variant="body1">Error: {error}</Typography>
        </ErrorOverlay>
      )}

      <Box sx={{ display: 'flex', height: '100%' }}>
        {/* Document container - always render this */}
        <Box sx={{ height: '100%', width: '72%', position: 'relative' }}>
          <DocumentContainer ref={containerRef} />
        </Box>

        {/* Only show sidebar if there are processed citations */}
        {processedCitations.length > 0 && (
          <CitationSidebar
            citations={processedCitations}
            scrollViewerTo={scrollViewerToRef.current}
          />
        )}
      </Box>
    </DocViewerContainer>
  );
};

export default DocxViewer;
