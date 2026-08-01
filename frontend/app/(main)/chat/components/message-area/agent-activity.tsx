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
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import type { MessagePart, StatusMessage } from '../../types';
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
  /** Current SSE status ("Thinking...", "Using Jira Search...") — rendered
   * as the last entry in the timeline (instead of a disconnected element
   * below the answer) so the "thinking -> tool -> thinking" flow reads as
   * one continuous progressive disclosure, Claude-style. Root-level only —
   * nested (sub-agent) timelines never receive this. */
  currentStatus?: StatusMessage | null;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
}

/** True when the transcript contains any tool call, reasoning block, or
 * sub-agent delegation — i.e. this is a multi-step ReAct response, not a
 * simple single-shot answer. Drives two decisions: whether unsettled root
 * text streams into the timeline (see `filterRootParts`) instead of
 * `AnswerContent`, and whether the completed activity gets a collapsible
 * summary wrapper (`CollapsibleActivitySection`). */
export function hasMultiStepActivity(parts: MessagePart[] | undefined): boolean {
  if (!parts) return false;
  return parts.some((part) => part.type === 'tool_call' || part.type === 'reasoning' || part.type === 'sub_agent');
}

/**
 * Root-level text filtering:
 * - The one part marked `isFinal` (the actual answer, mirrors Python's
 *   `TranscriptCollector.replace_final_text`) is never shown here —
 *   `AnswerContent` renders it.
 * - Every other root `text` part is narration and shown once `settled`
 *   (`agui-event-handler.ts`'s `LivePartsBuilder.settleLastRootText()`,
 *   called the moment a tool call, reasoning block, or new text turn
 *   proves the text wasn't the final answer).
 * - **Multi-step responses** (tool calls / reasoning already happened, or
 *   are happening): the still-unsettled trailing text is ALSO shown here,
 *   live, via `LiveNarrationText` — once a message has multi-step activity,
 *   `AnswerContent` is suppressed entirely for the duration of the stream
 *   (see `ChatResponse`), so there's no duplicate render and no "yank" of
 *   text jumping from the answer area into the timeline when it settles.
 * - **Simple responses** (no activity at all yet): unsettled text stays
 *   hidden here and is mirrored live in `streamingContent` /
 *   `AnswerContent` instead — showing it here too would duplicate it.
 */
function filterRootParts(parts: MessagePart[], isStreaming: boolean, multiStep: boolean): MessagePart[] {
  return parts.filter((part) => {
    if (part.type !== 'text') return true;
    if (part.isFinal) return false;
    if (isStreaming && multiStep) return true;
    if (isStreaming && !part.settled) return false;
    return true;
  });
}

/** Root-level visible timeline entries for `parts` — the exact same
 * filtering `AgentActivityTimeline` applies internally for a root (non-
 * nested) render. Exposed so callers (`ChatResponse`) can decide whether to
 * render a separator / collapsible wrapper around the timeline without
 * duplicating (and risking drift from) the filtering rules above. */
export function getVisibleRootParts(parts: MessagePart[], isStreaming: boolean): MessagePart[] {
  return filterRootParts(parts, isStreaming, hasMultiStepActivity(parts));
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

/** True when `item` will actually paint something — filters out narration/
 * thinking entries whose `content` is blank before they ever reach a rail
 * row, so the connected line never threads through an empty placeholder
 * (`ThinkingBlock`/`NarrationText`/`LiveNarrationText` would otherwise
 * return `null` from inside an already-committed row). */
/** Enter/Space activates a `role="button"` element the same way a native
 * `<button>` would — every expand/collapse header in this file is a
 * clickable `Flex` (not a real `<button>`, for layout/style reasons), so
 * none of them get free keyboard activation from the browser. */
function handleToggleKeyDown(e: React.KeyboardEvent, toggle: () => void): void {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    toggle();
  }
}

function hasRenderableContent(item: RenderItem): boolean {
  if (item.kind === 'toolGroup') return true;
  const { part } = item;
  if (part.type === 'reasoning' || part.type === 'text') return !!part.content?.trim();
  return true;
}

type TimelineIconSpec =
  | { kind: 'icon'; node: React.ReactNode; bg: string; border: string }
  | { kind: 'dot'; color: string }
  | { kind: 'lottie'; node: React.ReactNode };

function timelineIconForStatus(status: NonNullable<MessagePart['status']>, iconName: string): TimelineIconSpec {
  return {
    kind: 'icon',
    node: (
      <MaterialIcon
        name={iconName}
        size={13}
        color={TOOL_STATUS_COLOR[status]}
        style={status === 'running' ? { animation: 'spin 1s linear infinite' } : undefined}
      />
    ),
    bg: 'var(--slate-a3)',
    border: 'var(--slate-6)',
  };
}

/** One consistent glyph per activity type for the rail node — Claude-style,
 * so the "thinking -> search -> thinking -> ..." order reads at a glance
 * from the icons alone, without opening any card. Search-flavored tool
 * calls (individual or grouped) get the magnifying-glass icon instead of
 * the generic status icon, mirroring how search steps read visually
 * distinct from other tool use. */
function getTimelineIcon(item: RenderItem): TimelineIconSpec {
  if (item.kind === 'toolGroup') {
    const allSearches = item.parts.every((p) => isSearchLike(p.toolName));
    const anyRunning = item.parts.some((p) => (p.status ?? 'running') === 'running');
    const anyFailed = item.parts.some((p) => p.status === 'failed');
    const status: NonNullable<MessagePart['status']> = anyRunning ? 'running' : anyFailed ? 'failed' : 'completed';
    return timelineIconForStatus(status, allSearches ? 'travel_explore' : TOOL_STATUS_ICON[status]);
  }
  const { part } = item;
  switch (part.type) {
    case 'reasoning':
      return {
        kind: 'icon',
        node: <MaterialIcon name="psychology" size={13} color="var(--slate-10)" />,
        bg: 'var(--slate-a3)',
        border: 'var(--slate-6)',
      };
    case 'tool_call': {
      const status = part.status ?? 'running';
      return timelineIconForStatus(status, isSearchLike(part.toolName) ? 'travel_explore' : TOOL_STATUS_ICON[status]);
    }
    case 'sub_agent':
      return timelineIconForStatus(part.status ?? 'completed', 'smart_toy');
    default:
      // Narration (settled or live) — a plain waypoint dot rather than a
      // literal glyph, so free-flowing prose doesn't compete visually with
      // the more "actioned" steps (thinking / tools / sub-agents).
      return { kind: 'dot', color: 'var(--accent-9)' };
  }
}

/** One row of the connected activity timeline: a small icon "node" on a
 * continuous vertical line (rail) to the left, with the actual content
 * (thinking block, tool card, narration, ...) to the right — mirrors how
 * Claude visually threads its reasoning steps together instead of
 * rendering disconnected floating cards, so the read order of a
 * multi-step response is unambiguous at a glance without opening anything.
 * The line is drawn per-row (icon-to-icon-of-the-next-row) rather than as
 * one absolutely-positioned strip, so it naturally keeps pace with rows
 * that grow, shrink, or get inserted while streaming — no height
 * measurement needed. */
function TimelineRow({ icon, isLast, children }: { icon: TimelineIconSpec; isLast: boolean; children: React.ReactNode }) {
  return (
    <Flex className="agent-activity-enter">
      <Flex direction="column" align="center" style={{ width: 24, flexShrink: 0 }}>
        {icon.kind === 'dot' ? (
          <Box style={{ width: 8, height: 8, marginTop: 7, borderRadius: '50%', backgroundColor: icon.color, flexShrink: 0 }} />
        ) : icon.kind === 'lottie' ? (
          <Box style={{ width: 22, height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            {icon.node}
          </Box>
        ) : (
          <Flex
            align="center"
            justify="center"
            style={{
              width: 22,
              height: 22,
              borderRadius: '50%',
              backgroundColor: icon.bg,
              border: `1px solid ${icon.border}`,
              flexShrink: 0,
            }}
          >
            {icon.node}
          </Flex>
        )}
        {!isLast && (
          <Box
            style={{
              width: 2,
              flex: 1,
              minHeight: 'var(--space-3)',
              backgroundColor: 'var(--slate-5)',
              marginTop: 'var(--space-1)',
            }}
          />
        )}
      </Flex>
      <Box style={{ flex: 1, minWidth: 0, paddingLeft: 'var(--space-3)', paddingBottom: isLast ? 0 : 'var(--space-3)' }}>
        {children}
      </Box>
    </Flex>
  );
}

export function AgentActivityTimeline({ parts, isNested = false, isStreaming = false, currentStatus = null, citationMaps, citationCallbacks }: AgentActivityTimelineProps) {
  const visible = useMemo(
    () => (isNested ? parts : getVisibleRootParts(parts, isStreaming)),
    [parts, isNested, isStreaming],
  );
  // Don't show status alongside live text with content — the typing cursor
  // already signals progress; showing both is redundant and visually broken.
  const hasLiveTextWithContent = isStreaming && visible.some(
    (p) => p.type === 'text' && !p.settled && !p.isFinal && !!p.content?.trim(),
  );
  const showStatus = !isNested && isStreaming && !!currentStatus && !hasLiveTextWithContent;
  const items = groupConsecutiveToolCalls(visible).filter(hasRenderableContent);
  if (items.length === 0 && !showStatus) return null;
  const rowCount = items.length + (showStatus ? 1 : 0);

  return (
    <Box style={{ marginBottom: isNested ? 'var(--space-2)' : 'var(--space-3)' }}>
      <Flex direction="column">
        {items.map((item, idx) => (
          <TimelineRow key={item.key} icon={getTimelineIcon(item)} isLast={idx === rowCount - 1}>
            {item.kind === 'toolGroup' ? (
              <ToolCallGroup parts={item.parts} />
            ) : (
              <AgentActivityPart part={item.part} isStreaming={isStreaming} citationMaps={citationMaps} citationCallbacks={citationCallbacks} />
            )}
          </TimelineRow>
        ))}
        {/* Status indicator as the LAST timeline entry — keeps the
            "thinking -> tool -> thinking -> ..." flow in one place instead
            of a disconnected element below the (possibly-suppressed)
            answer. See ChatResponse for the simple-response fallback. */}
        {showStatus && (
          <TimelineRow icon={{ kind: 'lottie', node: <LottieLoader variant="thinking" size={18} /> }} isLast>
            <StatusTimelineEntry status={currentStatus} />
          </TimelineRow>
        )}
      </Flex>
    </Box>
  );
}

function AgentActivityPart({ part, isStreaming, citationMaps, citationCallbacks }: { part: MessagePart; isStreaming?: boolean; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks }) {
  switch (part.type) {
    case 'reasoning':
      return <ThinkingBlock content={part.content ?? ''} />;
    case 'tool_call':
      // Top-level rail row — the icon lives on the rail (see
      // `getTimelineIcon`), not duplicated inline on the card itself.
      return <ToolCallCard part={part} showIcon={false} />;
    case 'sub_agent':
      return <SubAgentGroup part={part} citationMaps={citationMaps} citationCallbacks={citationCallbacks} />;
    case 'text': {
      // Unsettled + still streaming: this text is being written right now
      // (a multi-step response's live "candidate" turn) — render it with a
      // typing cursor instead of the plain `NarrationText` treatment used
      // for already-settled narration. Blank content renders nothing —
      // `hasRenderableContent` filters the part out of the timeline and
      // `StatusTimelineEntry` signals progress until the first delta lands.
      const isLive = isStreaming && !part.settled && !part.isFinal;
      if (isLive) {
        return <LiveNarrationText content={part.content ?? ''} citationMaps={citationMaps} citationCallbacks={citationCallbacks} />;
      }
      return part.content ? <NarrationText content={part.content} citationMaps={citationMaps} citationCallbacks={citationCallbacks} /> : null;
    }
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
    <Box className="narration-text agent-activity-enter" style={{ color: 'var(--slate-12)', fontSize: 'var(--font-size-2)', lineHeight: 1.6 }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {cleanContent}
      </ReactMarkdown>
    </Box>
  );
}

/** The still-unsettled trailing text of a multi-step response, rendered
 * live in the timeline (instead of `AnswerContent`) while its fate —
 * narration vs. the final answer — is still undetermined. A trailing
 * blinking cursor marks it as "in progress" so it doesn't read as a
 * completed narration entry (`NarrationText`) or the finished answer;
 * the rail's waypoint dot (see `TimelineRow`) is what visually threads it
 * into the flow, so no in-text accent bar is needed here anymore. */
function LiveNarrationText({ content, citationMaps, citationCallbacks }: { content: string; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks }) {
  const cleanContent = processMarkdownContent(content);

  const citMapsRef = useRef(citationMaps);
  citMapsRef.current = citationMaps;
  const citCbRef = useRef(citationCallbacks);
  citCbRef.current = citationCallbacks;

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const components = useMemo(() => createMarkdownComponents(citMapsRef, citCbRef), []);

  if (!cleanContent.trim()) {
    // No content yet — the StatusTimelineEntry already signals progress,
    // rendering an empty cursor here causes a redundant indicator.
    return null;
  }

  return (
    <Box
      className="live-narration-text agent-activity-enter"
      style={{
        color: 'var(--slate-12)',
        fontSize: 'var(--font-size-2)',
        lineHeight: 1.6,
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {cleanContent}
      </ReactMarkdown>
      <span className="typing-cursor" />
    </Box>
  );
}

/** Status indicator ("Thinking...", "Using Jira Search...") rendered as a
 * timeline entry rather than a standalone element below the answer — see
 * `AgentActivityTimelineProps.currentStatus`. Its Lottie animation is the
 * rail's icon node (see the `showStatus` branch in `AgentActivityTimeline`)
 * so this only needs to render the shimmer text itself. */
function StatusTimelineEntry({ status }: { status: StatusMessage }) {
  return (
    <Text
      size="1"
      weight="medium"
      className="generating-shimmer"
      style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word', minWidth: 0 }}
    >
      <span className="generating-shimmer-base">{status.message}</span>
      <span className="generating-shimmer-overlay" aria-hidden="true">{status.message}</span>
    </Text>
  );
}

/** Collapsible chain-of-thought block — collapsed by default, same
 * disclosure pattern `AskUserQuestionCard` uses for its answers list. */
export function ThinkingBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!content.trim()) return null;

  return (
    <Box
      className="agent-activity-enter"
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
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(e) => handleToggleKeyDown(e, () => setExpanded((prev) => !prev))}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
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
 * see MessagePart. `showIcon` is false when this renders as a top-level
 * timeline row (the rail node already carries the status icon — see
 * `getTimelineIcon`); nested usage inside an expanded `ToolCallGroup` has
 * no rail of its own, so it keeps its own inline icon (the default). */
export function ToolCallCard({ part, showIcon = true }: { part: MessagePart; showIcon?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const status = part.status ?? 'running';
  const toolsetLabel = extractToolsetLabel(part.toolName);
  const sourceInfo = extractSourcesFromSummary(part.argsSummary);

  return (
    <Box
      className="agent-activity-enter"
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
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(e) => handleToggleKeyDown(e, () => setExpanded((prev) => !prev))}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        {showIcon && (
          <MaterialIcon
            name={TOOL_STATUS_ICON[status]}
            size={ICON_SIZES.PRIMARY}
            color={TOOL_STATUS_COLOR[status]}
            style={status === 'running' ? { animation: 'spin 1s linear infinite' } : undefined}
          />
        )}
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

  const toolsetLabels = [...new Set(parts.map((p) => extractToolsetLabel(p.toolName)).filter(Boolean))] as string[];
  const groupToolsetLabel = toolsetLabels.length === 1 ? toolsetLabels[0] : undefined;

  return (
    <Box
      className="agent-activity-enter"
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
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(e) => handleToggleKeyDown(e, () => setExpanded((prev) => !prev))}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
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
      className="agent-activity-enter"
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
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(e) => handleToggleKeyDown(e, () => setExpanded((prev) => !prev))}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <Text size="1" weight="medium" style={{ color: 'var(--accent-11)', flex: 1 }}>
          {part.roleName || 'Sub-agent'}
        </Text>
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

/** Human-readable summary for `CollapsibleActivitySection`'s header, e.g.
 * "Thought for a bit, used 3 tools". Counts are derived directly from the
 * transcript rather than tracked separately, so it never drifts from what's
 * actually rendered underneath. */
export function buildActivitySummary(parts: MessagePart[]): string {
  const toolCount = parts.filter((p) => p.type === 'tool_call').length;
  const thinkCount = parts.filter((p) => p.type === 'reasoning').length;
  const subAgentCount = parts.filter((p) => p.type === 'sub_agent').length;

  const segments: string[] = [];
  if (thinkCount > 0) segments.push(thinkCount === 1 ? 'Thought about this' : `Thought ${thinkCount} times`);
  if (toolCount > 0) segments.push(`Used ${toolCount} ${toolCount === 1 ? 'tool' : 'tools'}`);
  if (subAgentCount > 0) segments.push(`Delegated to ${subAgentCount} sub-${subAgentCount === 1 ? 'agent' : 'agents'}`);
  return segments.length > 0 ? segments.join(' · ') : 'Worked on this';
}

/**
 * Wraps a completed (non-streaming) activity timeline in a collapsible
 * summary — mirrors Claude's collapsed "Thought for Ns" / tool-use history
 * once a response finishes, so the reader's eye lands on the answer instead
 * of re-reading the whole ReAct trace on every reload. Collapsed by default;
 * the user can expand it to inspect the full trace.
 */
export function CollapsibleActivitySection({ parts, children }: { parts: MessagePart[]; children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(true);
  const summary = useMemo(() => buildActivitySummary(parts), [parts]);

  return (
    <Box style={{ marginBottom: 'var(--space-3)' }}>
      <Flex
        align="center"
        gap="1"
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((prev) => !prev)}
        onKeyDown={(e) => handleToggleKeyDown(e, () => setCollapsed((prev) => !prev))}
        style={{
          cursor: 'pointer',
          userSelect: 'none',
          marginBottom: collapsed ? 0 : 'var(--space-2)',
          width: 'fit-content',
        }}
      >
        <MaterialIcon
          name={collapsed ? 'expand_more' : 'expand_less'}
          size={ICON_SIZES.PRIMARY}
          color="var(--slate-9)"
        />
        <Text size="1" style={{ color: 'var(--slate-9)' }}>
          {summary}
        </Text>
      </Flex>
      {!collapsed && children}
    </Box>
  );
}
