'use client';

/**
 * Renders the agent-activity transcript (`MessagePart[]` — see "Parts-Based
 * Agent Message Transcript" / "Cursor-Style Agent Transparency" plans) as a
 * vertical timeline of narration text, collapsible thinking blocks,
 * tool-call cards (individually or grouped), and nested sub-agent groups.
 * Used both while streaming (`ChatSlot.streamingParts`) and after reload
 * (`ConversationMessage.parts`) — same components, same shape, so a message
 * never visually changes when the live stream hands off to the persisted
 * transcript.
 */
import React, { useState, useRef, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import type { MessagePart } from '../../types';
import { parseArtifactMarkers, parseDownloadMarkers } from '../../utils/parse-download-markers';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { createMarkdownComponents } from './answer-content';
import { processMarkdownContent } from '../../utils/process-markdown-content';
import {
  extractToolsetLabel,
  toolActivityLabel as toolActivityLabelUtil,
} from '../../utils/tool-display';

interface AgentActivityTimelineProps {
  parts: MessagePart[];
  /** Nested rendering (inside a SubAgentGroup) shows every `text` part,
   * including the delegate's own `isFinal` answer — there's no separate
   * "answer" slot for a sub-agent's turn like there is for the root. */
  isNested?: boolean;
  /** While the root message is still streaming, the trailing (last) `text`
   * part is an in-progress preamble, not settled narration yet — hide it
   * until either it's superseded (a later part arrives) or the run ends
   * (at which point it's either replaced by `isFinal` or just narration). */
  isStreaming?: boolean;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
}

/**
 * Root-level text filtering:
 * - The one part marked `isFinal` (the actual answer, mirrors Python's
 *   `TranscriptCollector.replace_final_text`) is never shown here —
 *   `AnswerContent` renders it.
 * - Every other root `text` part is narration and shown, UNLESS it's the
 *   last part in the array while still streaming (still being written —
 *   its content duplicates the live answer buffer).
 */
function filterRootParts(parts: MessagePart[], isStreaming: boolean): MessagePart[] {
  const lastIndex = parts.length - 1;
  return parts.filter((part, idx) => {
    if (part.type !== 'text') return true;
    if (part.isFinal) return false;
    if (isStreaming && idx === lastIndex) return false;
    return true;
  });
}

type RenderItem =
  | { kind: 'part'; part: MessagePart; key: string }
  | { kind: 'toolGroup'; parts: MessagePart[]; key: string };

/** Groups consecutive `tool_call` parts so a burst of activity (e.g. several
 * searches in a row) collapses into one summary row instead of a wall of
 * individual cards. A lone tool call (not adjacent to another) renders
 * directly, without a group wrapper. */
function groupConsecutiveToolCalls(parts: MessagePart[]): RenderItem[] {
  const items: RenderItem[] = [];
  let i = 0;
  while (i < parts.length) {
    const part = parts[i];
    if (part.type === 'tool_call') {
      let j = i;
      while (j < parts.length && parts[j].type === 'tool_call') j += 1;
      const group = parts.slice(i, j);
      if (group.length === 1) {
        items.push({ kind: 'part', part: group[0], key: `part-${group[0].toolCallId ?? i}` });
      } else {
        items.push({ kind: 'toolGroup', parts: group, key: `group-${group[0].toolCallId ?? i}` });
      }
      i = j;
    } else {
      items.push({ kind: 'part', part, key: `part-${part.type}-${part.runId ?? i}` });
      i += 1;
    }
  }
  return items;
}

export function AgentActivityTimeline({ parts, isNested = false, isStreaming = false, citationMaps, citationCallbacks }: AgentActivityTimelineProps) {
  const visible = isNested ? parts : filterRootParts(parts, isStreaming);
  if (visible.length === 0) return null;
  const items = groupConsecutiveToolCalls(visible);

  return (
    <Box style={{ marginBottom: isNested ? 'var(--space-2)' : 'var(--space-3)' }}>
      <Flex direction="column" gap="2">
        {items.map((item) =>
          item.kind === 'toolGroup' ? (
            <ToolCallGroup key={item.key} parts={item.parts} />
          ) : (
            <AgentActivityPart key={item.key} part={item.part} citationMaps={citationMaps} citationCallbacks={citationCallbacks} />
          ),
        )}
      </Flex>
    </Box>
  );
}

function AgentActivityPart({ part, citationMaps, citationCallbacks }: { part: MessagePart; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks }) {
  switch (part.type) {
    case 'reasoning':
      return <ThinkingBlock content={part.content ?? ''} />;
    case 'tool_call':
      return <ToolCallCard part={part} />;
    case 'sub_agent':
      return <SubAgentGroup part={part} citationMaps={citationMaps} citationCallbacks={citationCallbacks} />;
    case 'text':
      return part.content ? <NarrationText content={part.content} citationMaps={citationMaps} citationCallbacks={citationCallbacks} /> : null;
    default:
      return null;
  }
}

// Backend appends "---\nConfidence: High" to raw LLM output then strips it
// via normalizedAnswer, but narration parts (flushed before a tool call) keep
// the raw text. Strip the trailer here so it doesn't render as prose.
const CONFIDENCE_TRAILER_RE = /(?:\n*-{3,}\s*\n)?\s*Confidence:\s*(?:Very High|High|Medium|Low)\s*$/i;

function NarrationText({ content, citationMaps, citationCallbacks }: { content: string; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks }) {
  const { text: withoutArtifacts } = parseArtifactMarkers(content);
  const { text: afterDownloads } = parseDownloadMarkers(withoutArtifacts);
  const withoutConfidence = afterDownloads.replace(CONFIDENCE_TRAILER_RE, '');

  // After stripping markers, lines like `- ::artifact[...]{...}` become
  // bare `- ` or `- \n`. Remove empty list items / headings so they don't
  // render as blank bullets or orphaned headers.
  const withoutEmptyLines = withoutConfidence
    .replace(/^[ \t]*[-*+][ \t]*$/gm, '')
    .replace(/^#{1,6}\s*$/gm, '')
    .replace(/\n{3,}/g, '\n\n');

  const cleanContent = processMarkdownContent(withoutEmptyLines);
  if (!cleanContent.trim()) return null;

  const citMapsRef = useRef(citationMaps);
  citMapsRef.current = citationMaps;
  const citCbRef = useRef(citationCallbacks);
  citCbRef.current = citationCallbacks;

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const components = useMemo(() => createMarkdownComponents(citMapsRef, citCbRef), []);

  return (
    <Box className="narration-text" style={{ color: 'var(--slate-12)', fontSize: 'var(--font-size-2)', lineHeight: 1.6 }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {cleanContent}
      </ReactMarkdown>
    </Box>
  );
}

/** Collapsible chain-of-thought block — collapsed by default, same
 * disclosure pattern `AskUserQuestionCard` uses for its answers list. */
export function ThinkingBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!content.trim()) return null;

  return (
    <Box
      style={{
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--slate-a2)',
        overflow: 'hidden',
      }}
    >
      <Flex
        align="center"
        gap="2"
        role="button"
        onClick={() => setExpanded((prev) => !prev)}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <MaterialIcon name="psychology" size={ICON_SIZES.PRIMARY} color="var(--slate-10)" />
        <Text size="1" weight="medium" style={{ color: 'var(--slate-10)', flex: 1 }}>
          Thinking
        </Text>
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          size={ICON_SIZES.PRIMARY}
          color="var(--slate-9)"
        />
      </Flex>
      {expanded && (
        <Box
          style={{
            padding: '0 var(--space-3) var(--space-3)',
            color: 'var(--slate-11)',
            fontSize: 'var(--font-size-1)',
            whiteSpace: 'pre-wrap',
          }}
        >
          {content}
        </Box>
      )}
    </Box>
  );
}

const TOOL_STATUS_ICON: Record<NonNullable<MessagePart['status']>, string> = {
  running: 'sync',
  completed: 'check_circle',
  failed: 'warning',
  blocked: 'block',
};

const TOOL_STATUS_COLOR: Record<NonNullable<MessagePart['status']>, string> = {
  running: 'var(--blue-9)',
  completed: 'var(--green-9)',
  failed: 'var(--slate-9)',
  blocked: 'var(--amber-9)',
};


export function toolActivityLabel(part: Pick<MessagePart, 'toolName' | 'displayName'>): string {
  return toolActivityLabelUtil(part.toolName, part.displayName);
}

function extractSourcesFromSummary(summary: string | undefined): string | undefined {
  if (!summary) return undefined;
  const match = summary.match(/^Sources:\s*(.+)$/m);
  return match?.[1] || undefined;
}

function isSearchLike(toolName: string | undefined): boolean {
  return !!toolName && /search/i.test(toolName);
}

/** Renders a server-computed tool summary (e.g. "Retrieved 12 blocks from
 * 5 documents\n- Doc A\n- Doc B") as styled text rather than monospace —
 * summaries are prose/bullet-list, not raw JSON, so they read better with
 * the same lightweight markdown treatment `NarrationText` uses. */
function ToolSummaryText({ content }: { content: string }) {
  return (
    <Box style={{ fontSize: 'var(--font-size-1)', color: 'var(--slate-11)', lineHeight: 1.6 }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <Text size="1" as="p" style={{ margin: '0 0 var(--space-1) 0', color: 'inherit' }}>
              {children}
            </Text>
          ),
          ul: ({ children }) => (
            <ul style={{ paddingLeft: 'var(--space-4)', margin: 0, listStyleType: 'disc' }}>{children}</ul>
          ),
          li: ({ children }) => (
            <li style={{ marginBottom: '2px', lineHeight: 1.5 }}>{children}</li>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </Box>
  );
}

/** One tool call: activity label, status icon, and (when expanded)
 * truncated args / result preview — never the full external tool payload,
 * see MessagePart. */
export function ToolCallCard({ part }: { part: MessagePart }) {
  const [expanded, setExpanded] = useState(false);
  const status = part.status ?? 'running';
  const toolsetLabel = extractToolsetLabel(part.toolName);
  const sourceInfo = extractSourcesFromSummary(part.argsSummary);

  return (
    <Box
      style={{
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--slate-a2)',
        overflow: 'hidden',
      }}
    >
      <Flex
        align="center"
        gap="2"
        role="button"
        onClick={() => setExpanded((prev) => !prev)}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <MaterialIcon
          name={TOOL_STATUS_ICON[status]}
          size={ICON_SIZES.PRIMARY}
          color={TOOL_STATUS_COLOR[status]}
          style={status === 'running' ? { animation: 'spin 1s linear infinite' } : undefined}
        />
        {toolsetLabel && (
          <Text
            size="1"
            weight="medium"
            style={{
              color: 'var(--accent-11)',
              backgroundColor: 'var(--accent-a3)',
              padding: '2px 8px',
              borderRadius: 'var(--radius-2)',
              fontSize: '11px',
              lineHeight: '16px',
              whiteSpace: 'nowrap',
            }}
          >
            {toolsetLabel}
          </Text>
        )}
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--slate-11)',
            flex: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {toolActivityLabel(part)}
          {sourceInfo && (
            <span style={{ color: 'var(--slate-9)', fontWeight: 400 }}>
              {' · '}{sourceInfo}
            </span>
          )}
        </Text>
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          size={ICON_SIZES.PRIMARY}
          color="var(--slate-9)"
        />
      </Flex>
      {expanded && (
        <Box
          style={{
            padding: '0 var(--space-3) var(--space-3)',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-2)',
          }}
        >
          {(part.argsSummary || part.args) && (
            <Box>
              <Text size="1" weight="medium" style={{ color: 'var(--slate-9)' }}>
                Arguments
              </Text>
              {part.argsSummary ? (
                <ToolSummaryText content={part.argsSummary} />
              ) : (
                <Box
                  style={{
                    fontFamily: 'var(--font-mono, monospace)',
                    fontSize: 'var(--font-size-1)',
                    color: 'var(--slate-11)',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {part.args}
                </Box>
              )}
            </Box>
          )}
          {(part.resultSummary || part.resultPreview) && (
            <Box>
              <Text size="1" weight="medium" style={{ color: 'var(--slate-9)' }}>
                {status === 'failed' ? 'Error' : status === 'blocked' ? 'Blocked' : 'Result'}
              </Text>
              {part.resultSummary ? (
                <ToolSummaryText content={part.resultSummary} />
              ) : (
                <Box
                  style={{
                    fontFamily: 'var(--font-mono, monospace)',
                    fontSize: 'var(--font-size-1)',
                    color: 'var(--slate-11)',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {part.resultPreview}
                </Box>
              )}
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}

/** Collapsed-by-default summary row for a burst of >= 2 consecutive tool
 * calls — e.g. "Explored 3 searches" or "Ran 2 tools". Expanding reveals the
 * individual `ToolCallCard`s (which stay collapsed themselves). */
function ToolCallGroup({ parts }: { parts: MessagePart[] }) {
  const [expanded, setExpanded] = useState(false);
  const allSearches = parts.every((part) => isSearchLike(part.toolName));
  const label = allSearches ? `Explored ${parts.length} searches` : `Ran ${parts.length} tools`;
  const anyRunning = parts.some((part) => (part.status ?? 'running') === 'running');
  const anyFailed = parts.some((part) => part.status === 'failed');
  const status: NonNullable<MessagePart['status']> = anyRunning ? 'running' : anyFailed ? 'failed' : 'completed';

  const toolsetLabels = [...new Set(parts.map((p) => extractToolsetLabel(p.toolName)).filter(Boolean))] as string[];
  const groupToolsetLabel = toolsetLabels.length === 1 ? toolsetLabels[0] : undefined;

  return (
    <Box
      style={{
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--slate-a2)',
        overflow: 'hidden',
      }}
    >
      <Flex
        align="center"
        gap="2"
        role="button"
        onClick={() => setExpanded((prev) => !prev)}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <MaterialIcon
          name={allSearches ? 'travel_explore' : TOOL_STATUS_ICON[status]}
          size={ICON_SIZES.PRIMARY}
          color={TOOL_STATUS_COLOR[status]}
          style={status === 'running' ? { animation: 'spin 1s linear infinite' } : undefined}
        />
        {groupToolsetLabel && (
          <Text
            size="1"
            weight="medium"
            style={{
              color: 'var(--accent-11)',
              backgroundColor: 'var(--accent-a3)',
              padding: '2px 8px',
              borderRadius: 'var(--radius-2)',
              fontSize: '11px',
              lineHeight: '16px',
              whiteSpace: 'nowrap',
            }}
          >
            {groupToolsetLabel}
          </Text>
        )}
        <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
          {label}
        </Text>
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          size={ICON_SIZES.PRIMARY}
          color="var(--slate-9)"
        />
      </Flex>
      {expanded && (
        <Box style={{ padding: '0 var(--space-3) var(--space-3)' }}>
          <Flex direction="column" gap="2">
            {parts.map((part, idx) => (
              <ToolCallCard key={part.toolCallId ?? idx} part={part} />
            ))}
          </Flex>
        </Box>
      )}
    </Box>
  );
}

/** A sub-agent's own nested timeline — collapsed by default so the running
 * indicator on the header is the primary signal; expand to see tool calls. */
export function SubAgentGroup({ part, citationMaps, citationCallbacks }: { part: MessagePart; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks }) {
  const [expanded, setExpanded] = useState(false);
  const nested = part.parts ?? [];
  const status = part.status ?? 'completed';

  return (
    <Box
      style={{
        border: `1px solid ${status === 'running' ? 'var(--blue-a5)' : 'var(--accent-a5)'}`,
        borderRadius: 'var(--radius-3)',
        backgroundColor: status === 'running' ? 'var(--blue-a2)' : 'var(--accent-a2)',
        overflow: 'hidden',
      }}
    >
      <Flex
        align="center"
        gap="2"
        role="button"
        onClick={() => setExpanded((prev) => !prev)}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <MaterialIcon name="smart_toy" size={ICON_SIZES.PRIMARY} color="var(--accent-10)" />
        <Text size="1" weight="medium" style={{ color: 'var(--accent-11)', flex: 1 }}>
          {part.roleName || 'Sub-agent'}
        </Text>
        <MaterialIcon
          name={TOOL_STATUS_ICON[status]}
          size={ICON_SIZES.PRIMARY}
          color={TOOL_STATUS_COLOR[status]}
          style={status === 'running' ? { animation: 'spin 1s linear infinite' } : undefined}
        />
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          size={ICON_SIZES.PRIMARY}
          color="var(--accent-9)"
        />
      </Flex>
      {expanded && nested.length > 0 && (
        <Box style={{ padding: '0 var(--space-3) var(--space-3)' }}>
          <AgentActivityTimeline parts={nested} isNested citationMaps={citationMaps} citationCallbacks={citationCallbacks} />
        </Box>
      )}
    </Box>
  );
}
