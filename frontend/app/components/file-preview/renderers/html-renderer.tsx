'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import type { PreviewCitation } from '../types';
import { useTextHighlighter } from '../use-text-highlighter';
import { sanitizeHtmlPreview } from './sanitize-html-preview';

interface HtmlRendererProps {
  fileUrl: string;
  fileName: string;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

export function HtmlRenderer({ fileUrl, fileName, citations, activeCitationId, onHighlightClick }: HtmlRendererProps) {
  const [srcDoc, setSrcDoc] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [contentReady, setContentReady] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const { applyHighlights, clearHighlights, scrollToHighlight } = useTextHighlighter({
    citations,
    activeCitationId,
    onHighlightClick,
  });

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
        setSrcDoc(sanitizeHtmlPreview(rawHtml));
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

  const handleIframeLoad = useCallback(() => {
    setContentReady(true);
  }, []);

  useEffect(() => {
    if (!contentReady || !citations?.length) return;
    const root = iframeRef.current?.contentDocument?.body;
    if (!root) return;

    applyHighlights(root);
    return () => { clearHighlights(); };
  }, [contentReady, citations, applyHighlights, clearHighlights]);

  useEffect(() => {
    if (!activeCitationId || !contentReady) return;
    const root = iframeRef.current?.contentDocument?.body;
    if (!root) return;

    if (citations?.length) {
      applyHighlights(root);
    }

    const attemptScroll = (attempts: number) => {
      if (attempts <= 0) return;
      const el = root.querySelector(`.highlight-${CSS.escape(activeCitationId)}`);
      if (el) {
        scrollToHighlight(activeCitationId, root);
      } else if (attempts > 1) {
        setTimeout(() => attemptScroll(attempts - 1), 100);
      }
    };

    attemptScroll(3);
  }, [activeCitationId, contentReady, scrollToHighlight, citations, applyHighlights]);

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
      <iframe
        ref={iframeRef}
        className="file-preview-scroll-area"
        title={fileName || 'HTML preview'}
        srcDoc={srcDoc}
        sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        onLoad={handleIframeLoad}
        style={{
          width: '100%',
          height: '100%',
          border: 'none',
          display: 'block',
          backgroundColor: 'white',
        }}
      />
    </Box>
  );
}
