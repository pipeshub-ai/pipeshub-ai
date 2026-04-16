'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box, Text, Heading } from '@radix-ui/themes';
import { InlineCitationBadge } from './response-tabs/citations';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';

interface AnswerContentProps {
  content: string;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
}

/**
 * Parse a single text string, replacing `[N]` markers with InlineCitationBadge components.
 */
function parseInlineCitations(
  text: string,
  citationMaps?: CitationMaps,
  citationCallbacks?: CitationCallbacks,
): React.ReactNode[] {
  const citationRegex = /\[{1,2}(\d+)\]{1,2}/g;
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match;

  while ((match = citationRegex.exec(text)) !== null) {
    // Text before the marker
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    const chunkIndex = parseInt(match[1], 10);
    const citationId = citationMaps?.citationsOrder[chunkIndex];
    const citation = citationId ? citationMaps?.citations[citationId] : undefined;

    parts.push(
      <InlineCitationBadge
        key={`cite-${match.index}`}
        chunkIndex={chunkIndex}
        citation={citation}
        callbacks={citationCallbacks}
      />,
    );

    let afterIndex = match.index + match[0].length;
    const nextChar = text[afterIndex];
    // If the citation marker appears immediately before punctuation (e.g. "reports[1]."),
    // move the punctuation before the badge so it reads "reports. [badge]" instead of "reports [badge]."
    if (nextChar && /^[.!?;:,]/.test(nextChar)) {
      const badge = parts.pop()!;
      parts.push(nextChar);
      parts.push(badge);
      afterIndex += 1;
    }

    lastIndex = afterIndex;
  }

  // Remaining text
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}

/**
 * Recursively walk React children, replacing `[N]` citations in any string segments.
 */
function processChildren(
  children: React.ReactNode,
  citationMaps?: CitationMaps,
  citationCallbacks?: CitationCallbacks,
): React.ReactNode {
  if (typeof children === 'string') {
    return parseInlineCitations(children, citationMaps, citationCallbacks);
  }

  if (Array.isArray(children)) {
    return children.map((child, i) => {
      if (typeof child === 'string') {
        const parsed = parseInlineCitations(child, citationMaps, citationCallbacks);
        // Wrap in a fragment with key if we produced multiple nodes
        return parsed.length === 1 ? parsed[0] : <React.Fragment key={i}>{parsed}</React.Fragment>;
      }
      return child;
    });
  }

  return children;
}

export function AnswerContent({
  content,
  citationMaps,
  citationCallbacks,
}: AnswerContentProps) {
  // Custom components for react-markdown
  const components = {
    h1: ({ children }: { children?: React.ReactNode }) => (
      <Heading size="5" weight="bold" style={{ marginTop: 'var(--space-4)', marginBottom: 'var(--space-2)', color: 'var(--slate-12)' }}>
        {children}
      </Heading>
    ),
    h2: ({ children }: { children?: React.ReactNode }) => (
      <Heading size="4" weight="bold" style={{ marginTop: 'var(--space-4)', marginBottom: 'var(--space-2)', color: 'var(--slate-12)' }}>
        {children}
      </Heading>
    ),
    h3: ({ children }: { children?: React.ReactNode }) => (
      <Heading size="3" weight="bold" style={{ marginTop: 'var(--space-3)', marginBottom: 'var(--space-2)', color: 'var(--slate-12)' }}>
        {children}
      </Heading>
    ),
    h4: ({ children }: { children?: React.ReactNode }) => (
      <Text size="3" weight="bold" as="p" style={{ marginTop: 'var(--space-3)', marginBottom: 'var(--space-1)', color: 'var(--slate-12)' }}>
        {children}
      </Text>
    ),
    p: ({ children }: { children?: React.ReactNode }) => (
      <Text size="2" as="p" style={{ marginBottom: 'var(--space-3)', lineHeight: 1.6, color: 'var(--slate-12)' }}>
        {processChildren(children, citationMaps, citationCallbacks)}
      </Text>
    ),
    ul: ({ children }: { children?: React.ReactNode }) => (
      <ul
        style={{
          paddingLeft: 'var(--space-4)',
          marginBottom: 'var(--space-3)',
          listStyleType: 'disc',
        }}
      >
        {children}
      </ul>
    ),
    ol: ({ children }: { children?: React.ReactNode }) => (
      <ol
        style={{
          paddingLeft: 'var(--space-4)',
          marginBottom: 'var(--space-3)',
          listStyleType: 'decimal',
        }}
      >
        {children}
      </ol>
    ),
    li: ({ children }: { children?: React.ReactNode }) => (
      <li style={{ marginBottom: '0', lineHeight: 'var(--line-height-2)', color: 'var(--gray-12)' }}>
        {processChildren(children, citationMaps, citationCallbacks)}
      </li>
    ),
    strong: ({ children }: { children?: React.ReactNode }) => (
      <Text weight="bold" style={{ color: 'var(--slate-12)' }}>
        {children}
      </Text>
    ),
    em: ({ children }: { children?: React.ReactNode }) => (
      <Text style={{ fontStyle: 'italic' }}>{children}</Text>
    ),
    code: ({ children }: { children?: React.ReactNode }) => (
      <code
        style={{
          backgroundColor: 'var(--slate-3)',
          padding: '2px 6px',
          borderRadius: 'var(--radius-1)',
          fontFamily: 'monospace',
          fontSize: 'var(--font-size-2)',
        }}
      >
        {children}
      </code>
    ),
    pre: ({ children }: { children?: React.ReactNode }) => (
      <pre
        style={{
          backgroundColor: 'var(--slate-3)',
          padding: 'var(--space-3)',
          borderRadius: 'var(--radius-2)',
          overflow: 'auto',
          marginBottom: 'var(--space-3)',
          maxHeight: '400px',
        }}
      >
        {children}
      </pre>
    ),
    blockquote: ({ children }: { children?: React.ReactNode }) => (
      <blockquote
        style={{
          borderLeft: '3px solid var(--accent-9)',
          paddingLeft: 'var(--space-3)',
          marginLeft: 0,
          marginTop: 'var(--space-1)',
          marginBottom: '0',
          color: 'var(--slate-11)',
          fontSize: 'var(--font-size-2)',
          lineHeight: 1.6,
        }}
      >
        {children}
      </blockquote>
    ),
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          color: 'var(--accent-11)',
          textDecoration: 'underline',
          fontSize: 'var(--font-size-2)',
        }}
      >
        {children}
      </a>
    ),
    table: ({ children }: { children?: React.ReactNode }) => (
      <Box
        style={{
          overflowX: 'auto',
          overflowY: 'auto',
          maxHeight: '55vh',
          marginBottom: 'var(--space-3)',
          borderRadius: 'var(--radius-2)',
          border: '1px solid var(--slate-6)',
        }}
      >
        <table
          style={{
            minWidth: 'max-content',
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: 'var(--font-size-2)',
          }}
        >
          {children}
        </table>
      </Box>
    ),
    thead: ({ children }: { children?: React.ReactNode }) => (
      <thead
        style={{
          backgroundColor: 'var(--slate-3)',
        }}
      >
        {children}
      </thead>
    ),
    tbody: ({ children }: { children?: React.ReactNode }) => (
      <tbody>{children}</tbody>
    ),
    tr: ({ children }: { children?: React.ReactNode }) => (
      <tr
        style={{
          borderBottom: '1px solid var(--slate-6)',
        }}
      >
        {children}
      </tr>
    ),
    th: ({ children }: { children?: React.ReactNode }) => (
      <th
        style={{
          padding: 'var(--space-2) var(--space-3)',
          textAlign: 'left',
          fontWeight: 600,
          color: 'var(--slate-12)',
          whiteSpace: 'nowrap',
          position: 'sticky',
          top: 0,
          zIndex: 2,
          backgroundColor: 'var(--slate-3)',
          boxShadow: '0 1px 0 var(--slate-6)',
        }}
      >
        <Text size="2" weight="bold">
          {children}
        </Text>
      </th>
    ),
    td: ({ children }: { children?: React.ReactNode }) => (
      <td
        style={{
          padding: 'var(--space-2) var(--space-3)',
          color: 'var(--slate-12)',
          lineHeight: 1.5,
        }}
      >
        <Text size="2">
          {processChildren(children, citationMaps, citationCallbacks)}
        </Text>
      </td>
    ),
  };

  return (
    <Box>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{content}</ReactMarkdown>
    </Box>
  );
}
