'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box, Text, Heading } from '@radix-ui/themes';
import { InlineCitationBadge, InlineCitationGroup } from './response-tabs/citations';
import type {
  CitationMaps,
  CitationCallbacks,
  CitationData,
} from './response-tabs/citations';

interface AnswerContentProps {
  content: string;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
}

interface CitationMatch {
  chunkIndex: number;
  citation?: CitationData;
  index: number;
  length: number;
  key: string;
}

/**
 * Emit either an InlineCitationGroup (2+ consecutive same-record markers) or an
 * InlineCitationBadge (single marker) for a run of citation matches.
 */
function emitRun(
  run: CitationMatch[],
  citationCallbacks?: CitationCallbacks,
): React.ReactNode {
  if (run.length >= 2 && run.every((m) => m.citation)) {
    return (
      <InlineCitationGroup
        key={`cite-group-${run[0].key}`}
        items={run.map((m) => ({
          chunkIndex: m.chunkIndex,
          citation: m.citation as CitationData,
        }))}
        callbacks={citationCallbacks}
      />
    );
  }

  const only = run[0];
  return (
    <InlineCitationBadge
      key={`cite-${only.key}`}
      chunkIndex={only.chunkIndex}
      citation={only.citation}
      callbacks={citationCallbacks}
    />
  );
}

/**
 * Parse a single text string, replacing `[N]` markers with citation components.
 * Consecutive markers pointing at the same recordId — separated only by
 * whitespace — are collapsed into a single InlineCitationGroup.
 */
function parseInlineCitations(
  text: string,
  citationMaps?: CitationMaps,
  citationCallbacks?: CitationCallbacks,
): React.ReactNode[] {
  const citationRegex = /\[{1,2}(\d+)\]{1,2}/g;
  const parts: React.ReactNode[] = [];

  const matches: CitationMatch[] = [];
  let m: RegExpExecArray | null;
  while ((m = citationRegex.exec(text)) !== null) {
    const chunkIndex = parseInt(m[1], 10);
    const citationId = citationMaps?.citationsOrder[chunkIndex];
    const citation = citationId ? citationMaps?.citations[citationId] : undefined;
    matches.push({
      chunkIndex,
      citation,
      index: m.index,
      length: m[0].length,
      key: `${m.index}-${chunkIndex}`,
    });
  }

  if (matches.length === 0) {
    return [text];
  }

  let cursor = 0;
  let i = 0;
  while (i < matches.length) {
    const runStart = matches[i];

    // Emit any plain text before this run starts
    if (runStart.index > cursor) {
      parts.push(text.slice(cursor, runStart.index));
    }

    // Build a run of consecutive markers sharing a recordId, separated only
    // by whitespace. Single markers (or markers whose citation data isn't
    // loaded yet) form runs of length 1.
    const run: CitationMatch[] = [runStart];
    const anchorRecordId = runStart.citation?.recordId;

    if (anchorRecordId) {
      while (i + 1 < matches.length) {
        const prev = run[run.length - 1];
        const next = matches[i + 1];
        const nextRecordId = next.citation?.recordId;
        if (!nextRecordId || nextRecordId !== anchorRecordId) break;

        const gap = text.slice(prev.index + prev.length, next.index);
        // Only whitespace between markers counts as "adjacent"
        if (gap.length > 0 && !/^\s*$/.test(gap)) break;

        run.push(next);
        i += 1;
      }
    }

    const runEnd = run[run.length - 1];
    let afterIndex = runEnd.index + runEnd.length;

    // Emit the run, then handle punctuation-swap on the last marker so
    // "reports[1][2]." reads as "reports. [group]"
    const runNode = emitRun(run, citationCallbacks);
    const nextChar = text[afterIndex];
    if (nextChar && /^[.!?;:,]/.test(nextChar)) {
      parts.push(nextChar);
      parts.push(runNode);
      afterIndex += 1;
    } else {
      parts.push(runNode);
    }

    cursor = afterIndex;
    i += 1;
  }

  if (cursor < text.length) {
    parts.push(text.slice(cursor));
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
      <Heading size="4" weight="bold" style={{ marginTop: 'var(--space-3)', marginBottom: 'var(--space-2)', color: 'var(--slate-12)' }}>
        {children}
      </Heading>
    ),
    h4: ({ children }: { children?: React.ReactNode }) => (
      <Heading size="3" weight="bold" style={{ marginTop: 'var(--space-3)', marginBottom: 'var(--space-1)', color: 'var(--slate-12)' }}>
        {children}
      </Heading>
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
      <li style={{ marginBottom: 'var(--space-4)', lineHeight: 'var(--line-height-2)', color: 'var(--gray-12)', fontSize: '14px' }}>
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
          backgroundColor: 'var(--slate-7)',
          fontWeight: 400,
          padding: '2px var(--space-1)', /* was: 2px 6px, delta: -2px side */
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
