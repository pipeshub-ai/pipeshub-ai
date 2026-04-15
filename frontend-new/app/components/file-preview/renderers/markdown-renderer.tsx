'use client';

import { useState, useEffect, useRef } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useThemeAppearance } from '@/app/components/theme-provider';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import type { PreviewCitation } from '../types';
import { useTextHighlighter } from '../use-text-highlighter';

/**
 * Extended sanitize schema that allows common GitHub-readme HTML patterns
 * (align attributes, anchors with name, img with alt/src/width/height, etc.)
 * while still stripping scripts, iframes, forms, and event handlers.
 */
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    '*': [...(defaultSchema.attributes?.['*'] ?? []), 'className', 'align', 'id'],
    img: [...(defaultSchema.attributes?.['img'] ?? []), 'src', 'alt', 'width', 'height'],
    a: [...(defaultSchema.attributes?.['a'] ?? []), 'href', 'name', 'target', 'rel'],
  },
};

interface MarkdownRendererProps {
  fileUrl: string;
  fileName: string;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

export function MarkdownRenderer({ fileUrl, fileName: _fileName, citations, activeCitationId, onHighlightClick }: MarkdownRendererProps) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const [content, setContent] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRenderedRef = useRef(false);

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

    const fetchContent = async () => {
      try {
        const response = await fetch(fileUrl);
        if (!response.ok) {
          throw new Error('Failed to fetch markdown content');
        }
        const text = await response.text();
        setContent(text);
        setError(null);
      } catch (err) {
        console.error('Error loading markdown file:', err);
        setError(err instanceof Error ? err.message : 'Failed to load file');
      } finally {
        setIsLoading(false);
      }
    };

    fetchContent();
  }, [fileUrl]);

  // MutationObserver-based content ready detection + highlight application
  // Matches the upstream demo pattern: observe container for rendered markdown
  // elements, then apply highlights once real content is present.
  useEffect(() => {
    if (!content || isLoading || !containerRef.current) return undefined;

    contentRenderedRef.current = false;

    const observer = new MutationObserver(() => {
      const hasContent = containerRef.current?.querySelector(
        'p, h1, h2, h3, h4, h5, h6, ul, ol, blockquote, table, pre'
      );

      if (hasContent && !contentRenderedRef.current) {
        contentRenderedRef.current = true;
        observer.disconnect();

        if (citations?.length) {
          // Apply highlights once content is confirmed rendered
          requestAnimationFrame(() => {
            applyHighlights(containerRef.current);
          });
        }
      }
    });

    observer.observe(containerRef.current, {
      childList: true,
      subtree: true,
    });

    // Also check immediately in case content already rendered
    const hasContent = containerRef.current.querySelector(
      'p, h1, h2, h3, h4, h5, h6, ul, ol, blockquote, table, pre'
    );
    if (hasContent) {
      contentRenderedRef.current = true;
      observer.disconnect();
      if (citations?.length) {
        requestAnimationFrame(() => {
          applyHighlights(containerRef.current);
        });
      }
    }

    return () => {
      observer.disconnect();
      clearHighlights();
    };
  }, [content, isLoading, citations, applyHighlights, clearHighlights]);

  // Scroll to active citation highlight
  useEffect(() => {
    if (!activeCitationId || !containerRef.current || !contentRenderedRef.current) return;

    // Re-apply highlights to update active state, then scroll
    if (citations?.length) {
      applyHighlights(containerRef.current);
    }

    // Retry scroll with small delay to ensure highlight spans exist
    const attemptScroll = (attempts: number) => {
      if (attempts <= 0 || !containerRef.current) return;

      const el = containerRef.current.querySelector(`.highlight-${CSS.escape(activeCitationId)}`);
      if (el) {
        scrollToHighlight(activeCitationId, containerRef.current);
      } else if (attempts > 1) {
        setTimeout(() => attemptScroll(attempts - 1), 100);
      }
    };

    attemptScroll(3);
  }, [activeCitationId, scrollToHighlight, citations, applyHighlights]);

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <Text size="2" color="gray">
          Loading markdown...
        </Text>
      </Flex>
    );
  }

  if (error) {
    return (
      <Flex direction="column" align="center" justify="center" gap="3" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--red-9)' }}>
          error_outline
        </span>
        <Text size="3" weight="medium" color="red">
          {error}
        </Text>
      </Flex>
    );
  }

  return (
    <Box
      style={{
        width: '100%',
        height: '100%',
        overflow: 'auto',
        backgroundColor: 'var(--slate-2)',
        padding: 'var(--space-4)',
      }}
      className="no-scrollbar"
    >
      <Box
        style={{
          backgroundColor: isDark ? 'var(--slate-2)' : 'white',
          borderRadius: 'var(--radius-3)',
          border: `1px solid ${isDark ? 'var(--slate-4)' : 'var(--slate-6)'}`,
          boxShadow: '0px 12px 32px -16px rgba(0, 0, 0, 0.06), 0px 8px 40px 0px rgba(0, 0, 0, 0.05)',
          padding: 'var(--space-6)',
          maxWidth: '800px',
          margin: '0 auto',
        }}
        ref={containerRef}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema]]}
          components={{
            h1: ({ node: _node, ...props }) => (
              <h1 style={{ fontSize: '32px', fontWeight: 700, marginBottom: 'var(--space-4)', color: 'var(--slate-12)' }} {...props} />
            ),
            h2: ({ node: _node, ...props }) => (
              <h2 style={{ fontSize: '24px', fontWeight: 600, marginTop: 'var(--space-5)', marginBottom: 'var(--space-3)', color: 'var(--slate-12)' }} {...props} />
            ),
            h3: ({ node: _node, ...props }) => (
              <h3 style={{ fontSize: '20px', fontWeight: 600, marginTop: 'var(--space-4)', marginBottom: 'var(--space-2)', color: 'var(--slate-12)' }} {...props} />
            ),
            p: ({ node: _node, ...props }) => (
              <p style={{ marginBottom: 'var(--space-3)', lineHeight: 1.6, color: 'var(--slate-11)' }} {...props} />
            ),
            a: ({ node: _node, href, target, rel, ...props }) => (
              <a
                href={href}
                target={target}
                rel={target === '_blank' ? 'noopener noreferrer' : rel}
                style={{ color: 'var(--accent-9)', textDecoration: 'underline' }}
                {...props}
              />
            ),
            code: ({ node: _node, inline, ...props }: React.ComponentPropsWithoutRef<'code'> & { node?: unknown; inline?: boolean }) =>
              inline ? (
                <code style={{ backgroundColor: 'var(--slate-3)', padding: '2px 6px', borderRadius: 'var(--radius-1)', fontFamily: 'monospace', fontSize: '14px' }} {...props} />
              ) : (
                <code style={{ display: 'block', backgroundColor: 'var(--slate-3)', padding: 'var(--space-3)', borderRadius: 'var(--radius-2)', fontFamily: 'monospace', fontSize: '14px', overflow: 'auto' }} {...props} />
              ),
            ul: ({ node: _node, ...props }) => (
              <ul style={{ marginLeft: 'var(--space-5)', marginBottom: 'var(--space-3)', lineHeight: 1.6 }} {...props} />
            ),
            ol: ({ node: _node, ...props }) => (
              <ol style={{ marginLeft: 'var(--space-5)', marginBottom: 'var(--space-3)', lineHeight: 1.6 }} {...props} />
            ),
            blockquote: ({ node: _node, ...props }) => (
              <blockquote style={{ borderLeft: '4px solid var(--slate-6)', paddingLeft: 'var(--space-3)', marginLeft: 0, marginBottom: 'var(--space-3)', color: 'var(--slate-10)', fontStyle: 'italic' }} {...props} />
            ),
            table: ({ node: _node, ...props }) => (
              <div style={{ overflowX: 'auto', marginBottom: 'var(--space-3)' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', border: '1px solid var(--slate-6)' }} {...props} />
              </div>
            ),
            th: ({ node: _node, ...props }) => (
              <th style={{ backgroundColor: 'var(--slate-2)', padding: 'var(--space-2)', border: '1px solid var(--slate-6)', textAlign: 'left', fontWeight: 600 }} {...props} />
            ),
            td: ({ node: _node, ...props }) => (
              <td style={{ padding: 'var(--space-2)', border: '1px solid var(--slate-6)' }} {...props} />
            ),
            img: ({ node: _node, alt, ...props }) => (
              <img
                {...props}
                alt={alt ?? ''}
                style={{ maxWidth: '100%', height: 'auto', display: 'block', margin: '1em auto', borderRadius: 'var(--radius-2)' }}
                loading="lazy"
              />
            ),
            pre: ({ node: _node, ...props }) => (
              <pre style={{ backgroundColor: 'var(--slate-3)', padding: 'var(--space-3)', borderRadius: 'var(--radius-2)', overflow: 'auto', marginBottom: 'var(--space-3)' }} {...props} />
            ),
          }}
        >
          {content}
        </ReactMarkdown>
      </Box>
    </Box>
  );
}
