'use client';

import { useState, useEffect } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useThemeAppearance } from '@/app/components/theme-provider';
import type { PreviewCitation } from '../types';
import { sanitizePreviewHtml } from '../sandboxed-html';
import { SandboxedHtmlFrame } from '../sandboxed-html-frame';

interface HtmlRendererProps {
  fileUrl: string;
  fileName: string;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

/** Untrusted HTML renders in an isolated frame; see `sandboxed-html.ts`. */
export function HtmlRenderer({ fileUrl, fileName, citations, activeCitationId, onHighlightClick }: HtmlRendererProps) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const [sanitizedHtml, setSanitizedHtml] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
        const response = await fetch(fileUrl);
        if (!response.ok) throw new Error('Failed to fetch file content');
        const rawHtml = await response.text();

        if (cancelled) return;

        setSanitizedHtml(sanitizePreviewHtml(rawHtml));
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
        backgroundColor: isDark ? 'var(--slate-2)' : 'white',
      }}
    >
      {sanitizedHtml && (
        <SandboxedHtmlFrame
          sanitizedHtml={sanitizedHtml}
          title={fileName}
          dark={isDark}
          citations={citations}
          activeCitationId={activeCitationId}
          onHighlightClick={onHighlightClick}
        />
      )}
    </Box>
  );
}
