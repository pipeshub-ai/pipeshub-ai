'use client';

import { useState, useEffect, useLayoutEffect, useRef } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import type { PreviewCitation } from '../types';
import { useTextHighlighter } from '../use-text-highlighter';

interface DocxRendererProps {
  fileUrl: string;
  fileName: string;
  /**
   * Optional in-memory Blob for the DOCX file. When provided, the renderer
   * skips `fetch(fileUrl)` and hands the Blob's ArrayBuffer straight to
   * `docx-preview`. This is what fixes the "blank preview" symptom we saw
   * when rendering from a freshly-minted `URL.createObjectURL` blob URL.
   */
  fileBlob?: Blob;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

const DOCX_PREVIEW_OPTIONS = {
  className: 'docx',
  inWrapper: true,
  // Reflow document layout to the preview pane width (Word page width is often ~816px and
  // would otherwise be clipped in a narrow sidebar without horizontal scroll).
  ignoreWidth: true,
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
};

export function DocxRenderer({ fileUrl, fileName: _fileName, fileBlob, citations, activeCitationId, onHighlightClick }: DocxRendererProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [documentReady, setDocumentReady] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const activeCitationIdRef = useRef<string | null | undefined>(activeCitationId);
  useLayoutEffect(() => {
    activeCitationIdRef.current = activeCitationId;
  }, [activeCitationId]);

  const { applyHighlights, clearHighlights, scrollToHighlight } = useTextHighlighter({
    citations,
    activeCitationId,
    onHighlightClick,
  });

  // ── Step 1: Fetch buffer & render with docx-preview ───────────────
  useEffect(() => {
    const hasBlob = fileBlob instanceof Blob;
    const hasUrl = !!fileUrl && fileUrl.trim() !== '';

    if (!hasBlob && !hasUrl) {
      setError('File data not available');
      setIsLoading(false);
      return;
    }

    let cancelled = false;

    const renderDocument = async () => {
      try {
        setIsLoading(true);
        setDocumentReady(false);

        // Prefer the in-memory Blob when present — avoids a redundant
        // round-trip through a `URL.createObjectURL` blob URL, which was
        // the root cause of the blank DOCX preview.
        let arrayBuffer: ArrayBuffer;
        if (hasBlob) {
          if (fileBlob.size === 0) {
            throw new Error('Received an empty file from the server.');
          }
          arrayBuffer = await fileBlob.arrayBuffer();
        } else {
          const response = await fetch(fileUrl);
          if (!response.ok) throw new Error('Failed to fetch document');
          arrayBuffer = await response.arrayBuffer();
        }

        if (!arrayBuffer || arrayBuffer.byteLength === 0) {
          throw new Error('Document is empty.');
        }

        if (cancelled || !containerRef.current) return;

        // Dynamic import to avoid SSR issues (docx-preview uses DOM APIs)
        const docxPreview = await import('docx-preview');

        if (cancelled || !containerRef.current) return;

        // Clear any previous content
        containerRef.current.innerHTML = '';

        await docxPreview.renderAsync(arrayBuffer, containerRef.current, undefined, DOCX_PREVIEW_OPTIONS);

        if (cancelled) return;

        // If docx-preview produced no output (invalid file, silent failure,
        // etc.) show a concrete error instead of a blank pane.
        const container = containerRef.current;
        const renderedNodes = container.childElementCount;
        if (renderedNodes === 0) {
          throw new Error(
            'Unable to render this document. It may not be a valid .docx file (legacy .doc files are not supported).'
          );
        }

        // Add IDs to elements for highlight targeting
        let idCounter = 0;
        const addIds = (selector: string, prefix: string) => {
          container.querySelectorAll(selector).forEach((el) => {
            el.id = `${prefix}-${idCounter++}`;
          });
        };
        addIds('p:not([id])', 'p');
        addIds('span:not([id])', 'span');
        addIds('div:not([id]):not(:has(p, div))', 'div');

        setDocumentReady(true);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          console.error('Error loading docx file:', err);
          setError(err instanceof Error ? err.message : 'Failed to load document');
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    renderDocument();
    return () => { cancelled = true; };
  }, [fileUrl, fileBlob]);

  // ── Step 2: Apply citation highlights once document is rendered ────
  useEffect(() => {
    if (!documentReady || !citations?.length) return;
    const container = containerRef.current;
    if (!container) return;

    applyHighlights(container);
    return () => { clearHighlights(); };
  }, [documentReady, citations, applyHighlights, clearHighlights]);

  // ── Step 3: Scroll to active citation (retry pattern) ─────────────
  // `useTextHighlighter.applyHighlights` schedules work in rAF; wait briefly before scrolling
  // so the `.highlight-*` spans exist (mirrors `TextRenderer` / `HtmlRenderer`).
  // Cleanup cancels the initial delay and all nested retries; a ref avoids scrolling to a superseded id.
  useEffect(() => {
    if (!activeCitationId || !documentReady || !citations?.length) return;
    if (!containerRef.current) return;

    const targetId = activeCitationId;
    const timeouts: ReturnType<typeof setTimeout>[] = [];
    let cancelled = false;

    const clearAll = () => {
      cancelled = true;
      for (const t of timeouts) clearTimeout(t);
      timeouts.length = 0;
    };

    const schedule = (fn: () => void, ms: number) => {
      const id = setTimeout(() => {
        if (cancelled) return;
        fn();
      }, ms);
      timeouts.push(id);
    };

    schedule(() => {
      if (activeCitationIdRef.current !== targetId) return;
      const attemptScroll = (attempts: number) => {
        if (cancelled) return;
        if (activeCitationIdRef.current !== targetId) return;
        const root = containerRef.current;
        if (attempts <= 0 || !root) return;
        const el = root.querySelector(`.highlight-${CSS.escape(targetId)}`);
        if (el) {
          if (activeCitationIdRef.current !== targetId) return;
          scrollToHighlight(targetId, root);
        } else if (attempts > 1) {
          schedule(() => {
            if (activeCitationIdRef.current === targetId) {
              attemptScroll(attempts - 1);
            }
          }, 120);
        }
      };
      attemptScroll(12);
    }, 150);

    return clearAll;
  }, [activeCitationId, documentReady, scrollToHighlight, citations]);

  // ── Inject scoped styles for docx-preview highlights ──────────────
  useEffect(() => {
    const styleId = 'ph-docx-renderer-styles';
    if (document.getElementById(styleId)) return;

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      /* Fill parent width — docx-preview defaults can leave fixed "page" widths */
      .docx-wrapper {
        background: white !important;
        padding: 16px !important;
        width: 100% !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
      }
      .docx-wrapper > section.docx {
        box-shadow: 0 1px 3px rgba(0,0,0,0.08) !important;
        margin-bottom: 16px !important;
        width: 100% !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
      }
      .docx-wrapper .docx {
        max-width: 100% !important;
        box-sizing: border-box !important;
      }
      .docx-wrapper table {
        max-width: 100% !important;
      }
      /* Ensure highlights inherit text color inside docx-preview */
      .docx .ph-highlight * {
        color: inherit !important;
      }
    `;
    document.head.appendChild(style);

    return () => {
      const existing = document.getElementById(styleId);
      if (existing) existing.remove();
    };
  }, []);

  // IMPORTANT: The mount target for `docx-preview.renderAsync` must stay in the DOM while
  // we async-load bytes + the library. If we only render a loading screen, `containerRef`
  // is null and the effect bails out — same symptom as the old "blank preview" bug.
  return (
    <Box
      style={{
        width: '100%',
        maxWidth: '100%',
        minWidth: 0,
        height: '100%',
        minHeight: 0,
        position: 'relative',
        overflow: 'hidden',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--olive-6)',
        boxSizing: 'border-box',
      }}
    >
      <Box
        ref={containerRef}
        className="file-preview-scroll-area"
        style={{
          width: '100%',
          maxWidth: '100%',
          minWidth: 0,
          height: '100%',
          minHeight: 0,
          overflow: 'auto',
          boxSizing: 'border-box',
          WebkitOverflowScrolling: 'touch',
          visibility: error ? 'hidden' : 'visible',
        }}
      />

      {isLoading && (
        <Flex
          align="center"
          justify="center"
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 2,
            backgroundColor: 'var(--color-panel-solid)',
            padding: 'var(--space-6)',
          }}
        >
          <Text size="2" color="gray">Loading document...</Text>
        </Flex>
      )}

      {error && !isLoading && (
        <Flex
          direction="column"
          align="center"
          justify="center"
          gap="3"
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 2,
            backgroundColor: 'var(--color-panel-solid)',
            padding: 'var(--space-6)',
          }}
        >
          <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--red-9)' }}>
            error_outline
          </span>
          <Text size="3" weight="medium" color="red">{error}</Text>
        </Flex>
      )}
    </Box>
  );
}
