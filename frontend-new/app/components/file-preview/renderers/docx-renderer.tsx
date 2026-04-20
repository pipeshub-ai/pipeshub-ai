'use client';

import { useState, useEffect, useRef } from 'react';
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
};

export function DocxRenderer({ fileUrl, fileName: _fileName, fileBlob, citations, activeCitationId, onHighlightClick }: DocxRendererProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [documentReady, setDocumentReady] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

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
  useEffect(() => {
    if (!activeCitationId || !documentReady) return;
    const container = containerRef.current;
    if (!container) return;

    // Re-apply to update active state
    if (citations?.length) {
      applyHighlights(container);
    }

    const attemptScroll = (attempts: number) => {
      if (attempts <= 0) return;
      const el = container.querySelector(`.highlight-${CSS.escape(activeCitationId)}`);
      if (el) {
        scrollToHighlight(activeCitationId, container);
      } else if (attempts > 1) {
        setTimeout(() => attemptScroll(attempts - 1), 100);
      }
    };

    attemptScroll(3);
  }, [activeCitationId, documentReady, scrollToHighlight, citations, applyHighlights]);

  // ── Inject scoped styles for docx-preview highlights ──────────────
  useEffect(() => {
    const styleId = 'ph-docx-renderer-styles';
    if (document.getElementById(styleId)) return;

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      /* Ensure docx-preview wrapper fills available space */
      .docx-wrapper {
        background: white !important;
        padding: 16px !important;
      }
      .docx-wrapper > section.docx {
        box-shadow: 0 1px 3px rgba(0,0,0,0.08) !important;
        margin-bottom: 16px !important;
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

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <Text size="2" color="gray">Loading document...</Text>
      </Flex>
    );
  }

  if (error) {
    return (
      <Flex direction="column" align="center" justify="center" gap="3" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--red-9)' }}>
          error_outline
        </span>
        <Text size="3" weight="medium" color="red">{error}</Text>
      </Flex>
    );
  }

  return (
    <Box
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        overflow: 'hidden',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--olive-6)',
      }}
    >
      <Box
        ref={containerRef}
        style={{
          width: '100%',
          height: '100%',
          overflow: 'auto',
          minHeight: '100px',
        }}
      />
      {/* docx-preview renders directly into containerRef */}
    </Box>
  );
}
