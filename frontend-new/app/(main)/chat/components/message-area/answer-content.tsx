'use client';

import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box, Flex, Text, Heading } from '@radix-ui/themes';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import { InlineCitationBadge, InlineCitationGroup } from './response-tabs/citations';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { useChatStore } from '../../store';

interface ParsedArtifactMarker {
  fileName: string;
  url: string;
  mimeType: string;
  documentId: string;
  recordId: string;
}

/** Strip ``::artifact`` and ``::download_conversation_task`` markers, returning cleaned text and parsed artifacts. */
function stripArtifactMarkers(content: string): { text: string; artifacts: ParsedArtifactMarker[] } {
  const artifacts: ParsedArtifactMarker[] = [];

  const artifactRegex = /::artifact\[([^\]]+)\]\(([^)]+)\)\{([^}]*)\}/g;
  let text = content.replace(artifactRegex, (_, fileName, url, meta) => {
    const parts = meta.split('|');
    artifacts.push({
      fileName: fileName?.trim() || 'Download',
      url: (url ?? '').trim(),
      mimeType: parts[0] || 'application/octet-stream',
      documentId: parts[1] || '',
      recordId: parts[2] || '',
    });
    return '';
  });

  const downloadRegex = /::download_conversation_task\[([^\]]+)\]\(([^)]+)\)/g;
  text = text.replace(downloadRegex, (_, fileName, url) => {
    artifacts.push({
      fileName: fileName?.trim() || 'Download',
      url: (url ?? '').trim(),
      mimeType: 'text/csv',
      documentId: '',
      recordId: '',
    });
    return '';
  });

  return { text: text.trimEnd(), artifacts };
}

async function handleArtifactDownload(art: ParsedArtifactMarker): Promise<void> {
  if (art.recordId) {
    await KnowledgeBaseApi.streamDownloadRecord(art.recordId, art.fileName);
  } else if (art.url) {
    const link = document.createElement('a');
    link.href = art.url;
    link.download = art.fileName;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    document.body.appendChild(link);
    link.click();
    link.remove();
  }
}

async function handleArtifactPreview(art: ParsedArtifactMarker): Promise<void> {
  if (art.recordId) {
    const blob = await KnowledgeBaseApi.streamRecord(art.recordId);
    const objectUrl = URL.createObjectURL(blob);
    useChatStore.getState().setPreviewFile({
      id: art.recordId,
      url: objectUrl,
      name: art.fileName,
      type: art.mimeType,
      size: 0,
    });
  } else if (art.url) {
    useChatStore.getState().setPreviewFile({
      id: art.documentId || art.fileName,
      url: art.url,
      name: art.fileName,
      type: art.mimeType,
      size: 0,
    });
  }
}

/** Loads an image via the Record API and displays it once ready. */
function ArtifactImage({ art, style }: { art: ParsedArtifactMarker; style?: React.CSSProperties }) {
  const [src, setSrc] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!art.recordId) {
      setSrc(art.url || undefined);
      return;
    }
    let revoked = false;
    KnowledgeBaseApi.streamRecord(art.recordId)
      .then((blob) => {
        if (revoked) return;
        setSrc(URL.createObjectURL(blob));
      })
      .catch(() => {
        if (!revoked && art.url) setSrc(art.url);
      });
    return () => {
      revoked = true;
      if (src?.startsWith('blob:')) URL.revokeObjectURL(src);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [art.recordId, art.url]);

  return <img src={src} alt={art.fileName} style={style} />;
}

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
  const { text: cleanContent, artifacts: inlineArtifacts } = stripArtifactMarkers(content);

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
      <li style={{ marginBottom: '0', lineHeight: '20px', color: 'var(--gray-12)' }}>
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
          fontSize: '14px',
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
          fontSize: '14px',
          lineHeight: 1.6,
        }}
      >
        {children}
      </blockquote>
    ),
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
      const matchedArtifact = href
        ? inlineArtifacts.find((a) => a.url && href.includes(a.url))
          ?? inlineArtifacts.find((a) => typeof children === 'string' && a.fileName === children)
        : undefined;

      if (matchedArtifact) {
        return (
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              handleArtifactPreview(matchedArtifact);
            }}
            style={{
              color: 'var(--accent-11)',
              textDecoration: 'underline',
              fontSize: '14px',
              cursor: 'pointer',
            }}
          >
            {children}
          </a>
        );
      }

      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            color: 'var(--accent-11)',
            textDecoration: 'underline',
            fontSize: '14px',
          }}
        >
          {children}
        </a>
      );
    },
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
            fontSize: '14px',
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
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{cleanContent}</ReactMarkdown>
      {inlineArtifacts.length > 0 && (
        <Flex gap="2" wrap="wrap" style={{ marginTop: 'var(--space-2)' }}>
          {inlineArtifacts.map((art, idx) => {
            const isImage = art.mimeType.startsWith('image/') && !art.mimeType.includes('svg');
            if (isImage) {
              return (
                <Box
                  key={idx}
                  onClick={() => handleArtifactPreview(art)}
                  style={{ cursor: 'pointer' }}
                >
                  <Box
                    style={{
                      borderRadius: 'var(--radius-2)',
                      border: '1px solid var(--slate-6)',
                      overflow: 'hidden',
                      maxWidth: 320,
                    }}
                  >
                    <ArtifactImage
                      art={art}
                      style={{ maxWidth: '100%', height: 'auto', display: 'block' }}
                    />
                    <Flex align="center" gap="1" style={{ padding: '4px 8px', backgroundColor: 'var(--slate-2)' }}>
                      <span className="material-icons-outlined" style={{ fontSize: 14, color: 'var(--slate-9)' }}>image</span>
                      <Text size="1" style={{ color: 'var(--slate-11)' }}>{art.fileName}</Text>
                    </Flex>
                  </Box>
                </Box>
              );
            }
            return (
              <Box
                key={idx}
                onClick={() => handleArtifactDownload(art)}
                style={{ textDecoration: 'none', cursor: 'pointer' }}
              >
                <Flex
                  align="center"
                  gap="2"
                  style={{
                    padding: '6px 12px',
                    borderRadius: 'var(--radius-2)',
                    border: '1px solid var(--slate-6)',
                    backgroundColor: 'var(--slate-2)',
                    cursor: 'pointer',
                  }}
                >
                  <span className="material-icons-outlined" style={{ fontSize: 16, color: 'var(--accent-11)' }}>
                    download
                  </span>
                  <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
                    {art.fileName}
                  </Text>
                </Flex>
              </Box>
            );
          })}
        </Flex>
      )}
    </Box>
  );
}
