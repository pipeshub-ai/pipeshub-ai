'use client';

import { useState, useEffect } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import type { PreviewCitation } from '../types';
import { sanitizePreviewHtml } from '../sandboxed-html';
import { SandboxedHtmlFrame } from '../sandboxed-html-frame';

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

// renderAltChunks stays off: docx-preview puts embedded HTML chunks in an
// unsandboxed srcdoc iframe, i.e. script in the app origin. Base64 URLs keep
// images and embedded fonts loadable inside the sandboxed frame's CSP.
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
  useBase64URL: true,
  renderChanges: false,
  renderHeaders: true,
  renderFooters: true,
  renderFootnotes: true,
  renderEndnotes: true,
  renderComments: false,
  renderAltChunks: false,
};

const SYMBOL_CHAR = /^[0-9a-fA-F]{1,6}$/;

/**
 * docx-preview writes a `<w:sym w:char>` value into `innerHTML` unescaped, so a
 * crafted document runs script in the app while it renders. Replace any
 * symbol that is not a hex code point before rendering.
 */
export function neutralizeDocxSymbols(parts: readonly unknown[]): void {
  const seen = new WeakSet<object>();
  const visit = (value: unknown): void => {
    if (!value || typeof value !== 'object' || seen.has(value)) return;
    seen.add(value);
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    const node = value as Record<string, unknown>;
    if (node.type === 'symbol' && !(typeof node.char === 'string' && SYMBOL_CHAR.test(node.char))) {
      node.char = 'FFFD';
    }
    for (const [key, child] of Object.entries(node)) {
      if (key === 'parent' || !child || typeof child !== 'object') continue;
      if (Array.isArray(child) || Object.getPrototypeOf(child) === Object.prototype) visit(child);
    }
  };
  for (const part of parts) {
    if (part && typeof part === 'object') {
      for (const child of Object.values(part)) {
        if (child && typeof child === 'object' && (Array.isArray(child) || Object.getPrototypeOf(child) === Object.prototype)) {
          visit(child);
        }
      }
    }
  }
}

const DOCX_FRAME_CSS = `
  html, body { margin: 0; background: white; }
  /* Fill frame width — docx-preview defaults can leave fixed "page" widths */
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
  .docx-wrapper .docx { max-width: 100% !important; box-sizing: border-box !important; }
  .docx-wrapper table { max-width: 100% !important; }
  .docx .ph-highlight * { color: inherit !important; }
`;

/**
 * DOCX is converted to HTML on a detached element (nothing in it loads or
 * runs), sanitised, and shown in the same isolated frame as HTML previews.
 */
export function DocxRenderer({ fileUrl, fileName, fileBlob, citations, activeCitationId, onHighlightClick }: DocxRendererProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sanitizedHtml, setSanitizedHtml] = useState('');

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
        setSanitizedHtml('');

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

        // Dynamic import to avoid SSR issues (docx-preview uses DOM APIs)
        const docxPreview = await import('docx-preview');
        if (cancelled) return;

        const parsed = await docxPreview.parseAsync(arrayBuffer, DOCX_PREVIEW_OPTIONS);
        neutralizeDocxSymbols((parsed as unknown as { parts?: unknown[] }).parts ?? []);
        const container = document.createElement('div');
        await docxPreview.renderDocument(parsed, container, undefined, DOCX_PREVIEW_OPTIONS);
        if (cancelled) return;

        // If docx-preview produced no output (invalid file, silent failure,
        // etc.) show a concrete error instead of a blank pane.
        if (!container.querySelector('.docx-wrapper, section')) {
          throw new Error(
            'Unable to render this document. It may not be a valid .docx file (legacy .doc files are not supported).'
          );
        }

        setSanitizedHtml(sanitizePreviewHtml(container.innerHTML, { rich: true }));
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
        background: 'white',
      }}
    >
      {sanitizedHtml && !error && (
        <SandboxedHtmlFrame
          sanitizedHtml={sanitizedHtml}
          title={fileName}
          dark={false}
          baseCss={DOCX_FRAME_CSS}
          citations={citations}
          activeCitationId={activeCitationId}
          onHighlightClick={onHighlightClick}
        />
      )}
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
