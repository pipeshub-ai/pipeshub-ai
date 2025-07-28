import type { CustomCitation } from 'src/types/chat-bot';
import type { Position, HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';
import type {
  SearchResult,
  DocumentContent,
} from 'src/sections/knowledgebase/types/search-response';

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
  onClosePdf :() => void;
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
  minHeight: '100px', 
});

// Helper function to generate unique IDs
const getNextId = (): string => String(Math.random()).slice(2);

const isDocumentContent = (
  citation: DocumentContent | CustomCitation
): citation is DocumentContent => 'metadata' in citation && citation.metadata !== undefined;

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
  onClosePdf
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
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const fullScreenContainerRef = useRef<HTMLDivElement>(null);

  // STEP 1: Render document only once
  useEffect(() => {
    if (renderAttemptedRef.current || documentReady) {
      return;
    }

    renderAttemptedRef.current = true;

    const attemptRender = async (): Promise<boolean> => {
      if (!containerRef.current) {
        return false;
      }

      try {
        containerRef.current.innerHTML = '';
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

        const options = { ...defaultOptions, ...renderOptions };

        if (!url && !buffer) {
          throw new Error('Either url or buffer must be provided');
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

            const fileBuffer = await response.arrayBuffer();

            if (!fileBuffer || fileBuffer.byteLength === 0) {
              throw new Error('Received empty file from URL');
            }

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

        if (containerRef.current) {
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

    if (containerRef.current) {
      attemptRender();
      // eslint-disable-next-line
      return;
    }

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

  const createHighlightStyles = (): (() => void) | undefined => {
    const styleId = 'docx-highlight-styles';

    if (document.getElementById(styleId)) {
      return undefined; 
    }

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      /* Base highlight style - Professional neutral tones */
      .docx-highlight {
        cursor: pointer;
        background-color: rgba(59, 130, 246, 0.15) !important; /* Subtle blue */
        position: relative;
        z-index: 1;
        border-radius: 3px;
        box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2);
        transition: all 0.2s ease;
        text-decoration: none !important;
        color: inherit !important;
        border-bottom: 1px solid rgba(59, 130, 246, 0.4);
      }
      
      /* Dark mode support */
      @media (prefers-color-scheme: dark) {
        .docx-highlight {
          background-color: rgba(96, 165, 250, 0.2) !important;
          box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.3);
          border-bottom: 1px solid rgba(96, 165, 250, 0.5);
        }
      }
      
      /* Hover state */
      .docx-highlight:hover {
        background-color: rgba(59, 130, 246, 0.25) !important;
        box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.4);
        z-index: 2;
      }
      
      @media (prefers-color-scheme: dark) {
        .docx-highlight:hover {
          background-color: rgba(96, 165, 250, 0.3) !important;
          box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.5);
        }
      }
   
      /* Active state */
      .docx-highlight-active {
        background-color: rgba(59, 130, 246, 0.3) !important;
        box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.4) !important;
        font-weight: 500 !important;
        z-index: 3 !important;
        color: inherit !important;
        border-bottom: 2px solid rgba(59, 130, 246, 0.6);
      }
      
      @media (prefers-color-scheme: dark) {
        .docx-highlight-active {
          background-color: rgba(96, 165, 250, 0.35) !important;
          box-shadow: 0 0 0 2px rgba(96, 165, 250, 0.5) !important;
          border-bottom: 2px solid rgba(96, 165, 250, 0.7);
        }
      }
      
      /* Animation - more subtle */
      .highlight-pulse {
        animation: highlightPulse 1s ease-out 1;
      }
      
      @keyframes highlightPulse {
        0% { 
          box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.4);
          background-color: rgba(59, 130, 246, 0.3);
        }
        50% { 
          box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.2);
          background-color: rgba(59, 130, 246, 0.35);
        }
        100% { 
          box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.4);
          background-color: rgba(59, 130, 246, 0.3);
        }
      }
      
      @media (prefers-color-scheme: dark) {
        @keyframes highlightPulse {
          0% { 
            box-shadow: 0 0 0 2px rgba(96, 165, 250, 0.5);
            background-color: rgba(96, 165, 250, 0.35);
          }
          50% { 
            box-shadow: 0 0 0 4px rgba(96, 165, 250, 0.3);
            background-color: rgba(96, 165, 250, 0.4);
          }
          100% { 
            box-shadow: 0 0 0 2px rgba(96, 165, 250, 0.5);
            background-color: rgba(96, 165, 250, 0.35);
          }
        }
      }
      
      /* Professional highlight styles for different types of matches */
      .docx-highlight-exact {
        background-color: rgba(59, 130, 246, 0.2) !important;
        border-bottom: 1px solid rgba(59, 130, 246, 0.5);
      }
      
      .docx-highlight-partial {
        background-color: rgba(16, 185, 129, 0.15) !important; /* Subtle green */
        border-bottom: 1px dashed rgba(16, 185, 129, 0.4);
        box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.2);
      }
      
      .docx-highlight-fuzzy {
        background-color: rgba(107, 114, 128, 0.15) !important; /* Neutral gray */
        border-bottom: 1px dotted rgba(107, 114, 128, 0.4);
        box-shadow: 0 0 0 1px rgba(107, 114, 128, 0.2);
      }
      
      /* Dark mode variants for match types */
      @media (prefers-color-scheme: dark) {
        .docx-highlight-exact {
          background-color: rgba(96, 165, 250, 0.25) !important;
          border-bottom: 1px solid rgba(96, 165, 250, 0.6);
        }
        
        .docx-highlight-partial {
          background-color: rgba(34, 197, 94, 0.2) !important;
          border-bottom: 1px dashed rgba(34, 197, 94, 0.5);
          box-shadow: 0 0 0 1px rgba(34, 197, 94, 0.3);
        }
        
        .docx-highlight-fuzzy {
          background-color: rgba(156, 163, 175, 0.2) !important;
          border-bottom: 1px dotted rgba(156, 163, 175, 0.5);
          box-shadow: 0 0 0 1px rgba(156, 163, 175, 0.3);
        }
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

    const cleanup = (): void => {
      const styleElement = document.getElementById(styleId);
      if (styleElement) {
        document.head.removeChild(styleElement);
      }
    };

    return cleanup;
  };


  // STEP 3: Process citations when document is ready
  useEffect(() => {
    if (!documentReady || processingCitationsRef.current || !citations?.length) {
      return;
    }

    processingCitationsRef.current = true;

    try {
      highlightAppliersRef.current = [];
      const processed: ProcessedCitation[] = [];

      citations.forEach((citation) => {
        const highlight = processTextHighlight(citation);

        if (highlight) {
          const processedCitation: ProcessedCitation = {
            ...citation,
            highlight,
          } as ProcessedCitation;

          processed.push(processedCitation);
        }
      });

      setProcessedCitations(processed);

      if (processed.length > 0 && containerRef.current) {
        setTimeout(() => {
          applyTextHighlights(processed);
        }, 500);
      }
    } catch (err) {
      console.error('Error processing citations:', err);
    } finally {

      processingCitationsRef.current = false;
    }
    // eslint-disable-next-line
  }, [documentReady, citations]);

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

  const addHighlightClickHandler = (element: HTMLElement, highlightId: string): void => {
    element.addEventListener('click', () => {
      document.querySelectorAll('.docx-highlight-active').forEach((el) => {
        el.classList.remove('docx-highlight-active');
        el.classList.remove('highlight-pulse');
      });

      element.classList.add('docx-highlight-active');
      element.classList.add('highlight-pulse');

      element.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    });
  };

  const highlightTextInElement = (
    element: Element,
    text: string,
    highlightId: string,
    matchType: 'exact' | 'partial' | 'fuzzy' = 'exact'
  ): boolean => {
    if (!element || !text) return false;

    try {
      const elementContent = element.textContent || '';
      let textIndex = elementContent.indexOf(text);

      const highlightClass = `docx-highlight highlight-${highlightId} docx-highlight-${matchType}`;

      if (textIndex === -1 && matchType === 'fuzzy') {
        const normalizedElementContent = elementContent.replace(/\s+/g, ' ').trim();
        const normalizedText = text.replace(/\s+/g, ' ').trim();
        textIndex = normalizedElementContent.indexOf(normalizedText);

        if (textIndex === -1) {
          return false;
        }

        try {
          const wrapper = document.createElement('span');
          wrapper.className = highlightClass;
          wrapper.textContent = elementContent;
          wrapper.dataset.id = highlightId;
          wrapper.dataset.matchType = 'fuzzy';

          element.textContent = '';
          element.appendChild(wrapper);

          highlightAppliersRef.current.push(() => {
            try {
              if (element && wrapper.parentNode === element) {
                element.textContent = wrapper.textContent;
              }
            } catch (e) {
              console.error('Error in cleanup', e);
            }
          });

          addHighlightClickHandler(wrapper, highlightId);

          return true;
        } catch (e) {
          console.error('Error wrapping element', e);
          return false;
        }
      }

      if (textIndex >= 0) {
        const textNodes: Node[] = [];
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null);

        let currentNode = walker.nextNode();
        while (currentNode !== null) {
          textNodes.push(currentNode);
          currentNode = walker.nextNode();
        }

        if (textNodes.length === 0) return false;

        let currentNodeStart = 0;
        let startNode: Node | null = null;
        let startOffset = 0;
        let endNode: Node | null = null;
        let endOffset = 0;

        textNodes.forEach((nodeEl) => {
          const nodeText = nodeEl.nodeValue || '';
          const nodeLength = nodeText.length;

          if (
            !startNode &&
            textIndex >= currentNodeStart &&
            textIndex < currentNodeStart + nodeLength
          ) {
            startNode = nodeEl;
            startOffset = textIndex - currentNodeStart;
          }

          const textEnd = textIndex + text.length;
          if (!endNode && textEnd > currentNodeStart && textEnd <= currentNodeStart + nodeLength) {
            endNode = nodeEl;
            endOffset = textEnd - currentNodeStart;
          }

          if (startNode && endNode) {
            return;
          }

          currentNodeStart += nodeLength;
        });

        if (startNode && endNode) {
          const range = document.createRange();
          range.setStart(startNode, startOffset);
          range.setEnd(endNode, endOffset);

          const span = document.createElement('span');
          span.className = highlightClass;
          span.dataset.id = highlightId;
          span.dataset.matchType = matchType;

          try {
            range.surroundContents(span);

            addHighlightClickHandler(span, highlightId);

            return true;
          } catch (e) {
            console.error('Error applying range highlight', e);
            try {
              const textToHighlight = elementContent.substring(textIndex, textIndex + text.length);
              const beforeText = elementContent.substring(0, textIndex);
              const afterText = elementContent.substring(textIndex + text.length);

              element.textContent = '';

              if (beforeText) {
                element.appendChild(document.createTextNode(beforeText));
              }

              const highlightSpan = document.createElement('span');
              highlightSpan.className = highlightClass;
              highlightSpan.textContent = textToHighlight;
              highlightSpan.dataset.id = highlightId;
              highlightSpan.dataset.matchType = matchType;
              element.appendChild(highlightSpan);

              if (afterText) {
                element.appendChild(document.createTextNode(afterText));
              }

              addHighlightClickHandler(highlightSpan, highlightId);

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

  const applyTextHighlights = (citationsArray: ProcessedCitation[]): void => {
    if (!containerRef.current) return;

    clearHighlights();

    citationsArray.forEach((citation) => {
      if (!citation.highlight) return;

      const { text } = citation.highlight.content;
      if (!text || text.length < 10) {
        return;
      }

      try {
        const selector = 'p, div:not(:has(p)), span:not(:has(span))';
        const textElements = containerRef.current?.querySelectorAll(selector);

        if (!textElements) return;

        let matchFound = false;

        const normalizedText = text.replace(/\s+/g, ' ').trim();

        const exactMatches = Array.from(textElements).filter((el) => {
          const content = el.textContent || '';
          return content.includes(text);
        });

        if (exactMatches.length > 0) {
          exactMatches.sort((a, b) => {
            const aLength = (a.textContent || '').length;
            const bLength = (b.textContent || '').length;
            return Math.abs(aLength - text.length) - Math.abs(bLength - text.length);
          });

          if (highlightTextInElement(exactMatches[0], text, citation.highlight.id, 'exact')) {
            matchFound = true;
            return;
          }
        }

        if (!matchFound) {
          const normalizedMatches = Array.from(textElements).filter((el) => {
            const content = (el.textContent || '').replace(/\s+/g, ' ').trim();
            return content.includes(normalizedText);
          });

          if (normalizedMatches.length > 0) {
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

        if (!matchFound && text.length > 50) {
          const chunks = [
            text.substring(0, Math.min(100, Math.floor(text.length / 3))),
            text.substring(Math.floor(text.length / 3), Math.floor((2 * text.length) / 3)),
            text.substring(Math.floor((2 * text.length) / 3)),
          ];

          let chunkMatchCount = 0;

          chunks.forEach((chunk, i) => {
            if (chunk.length < 20) return; 

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

        if (!matchFound && text.length > 30) {
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
            fuzzyMatches.sort((a, b) => {
              const aContent = (a.textContent || '').replace(/\s+/g, ' ').trim();
              const bContent = (b.textContent || '').replace(/\s+/g, ' ').trim();

              const aSimilarity = calculateSimilarity(aContent, normalizedText);
              const bSimilarity = calculateSimilarity(bContent, normalizedText);

              return bSimilarity - aSimilarity;
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

  const clearHighlights = (): void => {
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

    highlightAppliersRef.current.forEach((cleanup) => {
      try {
        if (typeof cleanup === 'function') cleanup();
      } catch (e) {
        console.error('Error in highlight cleanup', e);
      }
    });

    highlightAppliersRef.current = [];
  };

  const scrollToHighlight = useCallback((highlight: HighlightType): void => {
    if (!containerRef.current || !highlight) return;

    const highlightId = highlight.id;
    const highlightElement = containerRef.current.querySelector(`.highlight-${highlightId}`);

    if (highlightElement) {
      document.querySelectorAll('.docx-highlight-active').forEach((el) => {
        el.classList.remove('docx-highlight-active');
        el.classList.remove('highlight-pulse');
      });

      highlightElement.classList.add('docx-highlight-active');
      highlightElement.classList.add('highlight-pulse');

      highlightElement.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    } else {
      console.warn(`Highlight element with ID ${highlightId} not found`);

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

  useEffect(() => {
    scrollViewerToRef.current = scrollToHighlight;
  }, [scrollToHighlight]);

  useEffect(
    () => () => {
      clearHighlights();
    },
    []
  );

  useEffect(() => {
    if (!highlightCitation) return;

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

    if (!processedCitations.length) {
      return;
    }

    const targetCitation = processedCitations.find(
      (citation) => citation.highlight?.id === highlightedCitationId
    );

    if (targetCitation?.highlight) {
      setTimeout(() => {
        if (targetCitation.highlight) {
          scrollToHighlight(targetCitation.highlight);
        }
      }, 1000);
    }
  }, [documentReady, processedCitations, highlightedCitationId, scrollToHighlight]);

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
    <DocViewerContainer ref={fullScreenContainerRef} component={Paper}>
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

      <Box sx={{ display: 'flex', height: '100%', width: '100%' }}>
  {/* Document container - takes remaining space */}
  <Box sx={{ 
    height: '100%', 
    flex: 1, 
    position: 'relative',
    minWidth: 0, // Prevents flex item from overflowing
  }}>
    <DocumentContainer ref={containerRef} />
  </Box>

  {/* Sidebar - takes its natural width (300px) */}
  {processedCitations.length > 0 && (
    <CitationSidebar
      citations={processedCitations}
      scrollViewerTo={scrollViewerToRef.current}
      highlightedCitationId={highlightedCitationId}
      toggleFullScreen={toggleFullScreen}
      onClosePdf={onClosePdf}
    />
  )}
</Box>
    </DocViewerContainer>
  );
};

export default DocxViewer;
