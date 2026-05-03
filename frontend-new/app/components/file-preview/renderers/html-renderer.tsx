'use client';

import { useState, useEffect, useRef } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import DOMPurify from 'dompurify';
import { useThemeAppearance } from '@/app/components/theme-provider';
import type { PreviewCitation } from '../types';
import { useTextHighlighter } from '../use-text-highlighter';

const HTML_CONTENT_CLASS = 'ph-html-rendered-content';

interface HtmlRendererProps {
  fileUrl: string;
  fileName: string;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

export function HtmlRenderer({ fileUrl, fileName: _fileName, citations, activeCitationId, onHighlightClick }: HtmlRendererProps) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const [sanitizedHtml, setSanitizedHtml] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [contentReady, setContentReady] = useState(false);

  /** Outer scrollable container ref */
  const containerRef = useRef<HTMLDivElement>(null);
  /** Inner content wrapper ref (the programmatically created div) */
  const contentWrapperRef = useRef<HTMLDivElement | null>(null);

  const { applyHighlights, clearHighlights, scrollToHighlight } = useTextHighlighter({
    citations,
    activeCitationId,
    onHighlightClick,
  });

  // ── Step 1: Fetch & sanitize ──────────────────────────────────────
  useEffect(() => {
    if (!fileUrl || fileUrl.trim() === '') {
      setError('File URL not available');
      setIsLoading(false);
      return;
    }

    let cancelled = false;

    const fetchContent = async () => {
      try {
        setIsLoading(true);
        setContentReady(false);
        const response = await fetch(fileUrl);
        if (!response.ok) throw new Error('Failed to fetch file content');
        const rawHtml = await response.text();

        if (cancelled) return;

        const clean = DOMPurify.sanitize(rawHtml, {
          USE_PROFILES: { html: true },
          ADD_ATTR: ['target', 'id', 'class', 'style', 'href', 'src', 'alt', 'title'],
          ADD_TAGS: ['figure', 'figcaption'],
          FORBID_TAGS: ['script', 'link', 'base'],
          FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus', 'autofocus'],
        });
        setSanitizedHtml(clean);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          console.error('Error loading HTML file:', err);
          setError(err instanceof Error ? err.message : 'Failed to load file');
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    fetchContent();
    return () => { cancelled = true; };
  }, [fileUrl]);

  // ── Step 2: Mount sanitized HTML into the DOM ─────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !sanitizedHtml || isLoading) return;

    // Clear previous content
    container.innerHTML = '';
    contentWrapperRef.current = null;

    const contentDiv = document.createElement('div');
    contentDiv.className = HTML_CONTENT_CLASS;
    contentDiv.innerHTML = sanitizedHtml;
    container.appendChild(contentDiv);
    contentWrapperRef.current = contentDiv;
    setContentReady(true);

    return () => {
      container.innerHTML = '';
      contentWrapperRef.current = null;
      setContentReady(false);
    };
  }, [sanitizedHtml, isLoading]);

  // ── Step 3: Apply citation highlights once content is ready ───────
  useEffect(() => {
    if (!contentReady || !citations?.length) return;
    const wrapper = contentWrapperRef.current;
    if (!wrapper) return;

    applyHighlights(wrapper);
    return () => { clearHighlights(); };
  }, [contentReady, citations, applyHighlights, clearHighlights]);

  // ── Step 4: Scroll to active citation (retry pattern) ────────────
  useEffect(() => {
    if (!activeCitationId || !contentReady) return;
    const wrapper = contentWrapperRef.current;
    if (!wrapper) return;

    // Re-apply to update active state
    if (citations?.length) {
      applyHighlights(wrapper);
    }

    const attemptScroll = (attempts: number) => {
      if (attempts <= 0) return;
      const el = wrapper.querySelector(`.highlight-${CSS.escape(activeCitationId)}`);
      if (el) {
        scrollToHighlight(activeCitationId, wrapper);
      } else if (attempts > 1) {
        setTimeout(() => attemptScroll(attempts - 1), 100);
      }
    };

    attemptScroll(3);
  }, [activeCitationId, contentReady, scrollToHighlight, citations, applyHighlights]);

  // ── Inject scoped styles for the content wrapper ──────────────────
  useEffect(() => {
    const styleId = 'ph-html-renderer-styles';
    if (document.getElementById(styleId)) return;

    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      .${HTML_CONTENT_CLASS} {
        font-family: 'Manrope', sans-serif;
        line-height: 1.6;
        max-width: 100%;
        word-wrap: break-word;
        color: var(--olive-12);
      }
      .${HTML_CONTENT_CLASS} img {
        max-width: 100%;
        height: auto;
      }
      .${HTML_CONTENT_CLASS} pre {
        background-color: var(--olive-3);
        padding: 1em;
        border-radius: var(--radius-2);
        overflow-x: auto;
        font-family: monospace;
      }
      .${HTML_CONTENT_CLASS} code:not(pre > code) {
        font-family: monospace;
        background-color: var(--olive-3);
        padding: 0.2em 0.4em;
        border-radius: var(--radius-1);
      }
      .${HTML_CONTENT_CLASS} table {
        border-collapse: collapse;
        margin-bottom: 1em;
        width: auto;
      }
      .${HTML_CONTENT_CLASS} td,
      .${HTML_CONTENT_CLASS} th {
        padding: 0.5em;
        text-align: left;
        border: 1px solid var(--olive-6);
      }
      .${HTML_CONTENT_CLASS} th {
        background-color: var(--olive-3);
        font-weight: 600;
      }
      .${HTML_CONTENT_CLASS} a {
        color: var(--accent-9);
        text-decoration: underline;
      }
      .${HTML_CONTENT_CLASS} blockquote {
        border-left: 4px solid var(--olive-6);
        padding-left: 1em;
        margin-left: 0;
        color: var(--olive-11);
      }
      .${HTML_CONTENT_CLASS} h1,
      .${HTML_CONTENT_CLASS} h2,
      .${HTML_CONTENT_CLASS} h3,
      .${HTML_CONTENT_CLASS} h4,
      .${HTML_CONTENT_CLASS} h5,
      .${HTML_CONTENT_CLASS} h6 {
        margin-top: 1.5em;
        margin-bottom: 0.8em;
      }
      .${HTML_CONTENT_CLASS} p {
        margin-bottom: 0.8em;
      }
      .${HTML_CONTENT_CLASS} ul,
      .${HTML_CONTENT_CLASS} ol {
        margin-left: 1.5em;
        margin-bottom: 1em;
      }
      .${HTML_CONTENT_CLASS} li {
        margin-bottom: 0.25em;
      }
      .${HTML_CONTENT_CLASS} hr {
        border: none;
        border-top: 1px solid var(--olive-6);
        margin: 1.5em 0;
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
        <Text size="2" color="gray">Loading HTML...</Text>
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
          padding: '1rem 1.5rem',
          backgroundColor: isDark ? 'var(--slate-2)' : 'white',
          color: isDark ? 'var(--slate-12)' : undefined,
        }}
      />
      {/* Content is mounted programmatically into containerRef */}
    </Box>
  );
}
