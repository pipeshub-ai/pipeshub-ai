'use client';

import { useState, useEffect, useRef, useLayoutEffect } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus, vs } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { useThemeAppearance } from '@/app/components/theme-provider';
import type { PreviewCitation } from '../types';
import { useTextHighlighter } from '../use-text-highlighter';

interface TextRendererProps {
  fileUrl: string;
  fileName: string;
  fileType?: string;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

// Map file extensions to language identifiers for syntax highlighting
function getLanguageFromExtension(fileName: string): string {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  
  const languageMap: Record<string, string> = {
    // JavaScript/TypeScript
    'js': 'javascript',
    'jsx': 'jsx',
    'ts': 'typescript',
    'tsx': 'tsx',
    'mjs': 'javascript',
    'cjs': 'javascript',
    // Web
    'html': 'html',
    'htm': 'html',
    'css': 'css',
    'scss': 'scss',
    'sass': 'sass',
    'less': 'less',
    'json': 'json',
    'xml': 'xml',
    'svg': 'xml',
    // Python
    'py': 'python',
    'pyw': 'python',
    // Java
    'java': 'java',
    // C/C++
    'c': 'c',
    'h': 'c',
    'cpp': 'cpp',
    'hpp': 'cpp',
    'cc': 'cpp',
    'cxx': 'cpp',
    // C#
    'cs': 'csharp',
    // PHP
    'php': 'php',
    // Ruby
    'rb': 'ruby',
    // Go
    'go': 'go',
    // Rust
    'rs': 'rust',
    // Swift
    'swift': 'swift',
    // Kotlin
    'kt': 'kotlin',
    'kts': 'kotlin',
    // Shell
    'sh': 'bash',
    'bash': 'bash',
    'zsh': 'bash',
    // SQL
    'sql': 'sql',
    // Yaml
    'yml': 'yaml',
    'yaml': 'yaml',
    // Markdown
    'md': 'markdown',
    'markdown': 'markdown',
    // Other
    'txt': 'text',
    'log': 'text',
  };
  
  return languageMap[ext] || 'text';
}

export function TextRenderer({ fileUrl, fileName, fileType: _fileType, citations, activeCitationId, onHighlightClick }: TextRendererProps) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const [content, setContent] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** True after content is shown and one frame has passed (SyntaxHighlighter needs a paint). */
  const [textDomReady, setTextDomReady] = useState(false);
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
          throw new Error('Failed to fetch file content');
        }
        const text = await response.text();
        setContent(text);
        setError(null);
      } catch (err) {
        console.error('Error loading text file:', err);
        setError(err instanceof Error ? err.message : 'Failed to load file');
      } finally {
        setIsLoading(false);
      }
    };

    fetchContent();
  }, [fileUrl]);

  // Flip ready after mount/paint so Prism and plain `<pre>` text nodes exist.
  useEffect(() => {
    if (!content || isLoading) {
      setTextDomReady(false);
      return;
    }
    setTextDomReady(false);
    let cancelled = false;
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        if (!cancelled) setTextDomReady(true);
      });
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, [content, isLoading]);

  // Apply highlights once the text layer has painted (`useTextHighlighter` schedules work in rAF).
  useEffect(() => {
    if (!textDomReady || !containerRef.current || !citations?.length) return;
    applyHighlights(containerRef.current);
    return () => {
      clearHighlights();
    };
  }, [textDomReady, citations, applyHighlights, clearHighlights]);

  // Scroll + active styling after highlights exist (same deferred retry pattern as docx/markdown).
  useEffect(() => {
    if (!activeCitationId || !textDomReady || !citations?.length) return;
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
      const root = containerRef.current;
      if (!root) return;
      applyHighlights(root);

      const attemptScroll = (attempts: number) => {
        if (cancelled) return;
        if (activeCitationIdRef.current !== targetId) return;
        const r = containerRef.current;
        if (attempts <= 0 || !r) return;
        const el = r.querySelector(`.highlight-${CSS.escape(targetId)}`);
        if (el) {
          if (activeCitationIdRef.current !== targetId) return;
          scrollToHighlight(targetId, r);
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
  }, [activeCitationId, textDomReady, scrollToHighlight, citations, applyHighlights]);

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <Text size="2" color="gray">
          Loading file...
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

  const language = getLanguageFromExtension(fileName);
  const isCode = language !== 'text';

  return (
    <Box
      style={{
        width: '100%',
        height: '100%',
        overflow: 'auto',
        backgroundColor: 'var(--slate-2)',
        padding: 'var(--space-4)',
      }}
      className="file-preview-scroll-area"
    >
      <Box
        ref={containerRef}
        style={{
          backgroundColor: isCode ? (isDark ? '#1e1e1e' : '#fafafa') : (isDark ? 'var(--slate-2)' : 'white'),
          borderRadius: 'var(--radius-3)',
          border: `1px solid var(--slate-6)`,
          boxShadow: '0px 12px 32px -16px rgba(0, 0, 51, 0.06), 0px 8px 40px 0px rgba(0, 0, 0, 0.05)',
          overflow: 'hidden',
        }}
      >
        {isCode ? (
          <SyntaxHighlighter
            language={language}
            style={isDark ? vscDarkPlus : vs}
            showLineNumbers
            wrapLines
            customStyle={{
              margin: 0,
              borderRadius: 'var(--radius-3)',
              fontSize: '14px',
              lineHeight: '1.5',
            }}
          >
            {content}
          </SyntaxHighlighter>
        ) : (
          <Box
            style={{
              padding: 'var(--space-4)',
              fontFamily: 'monospace',
              fontSize: '14px',
              lineHeight: '1.5',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              color: 'var(--slate-12)',
            }}
          >
            {content}
          </Box>
        )}
      </Box>
    </Box>
  );
}
