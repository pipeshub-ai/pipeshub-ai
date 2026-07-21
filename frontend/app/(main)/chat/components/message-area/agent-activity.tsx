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
import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import type { MessagePart } from '../../types';

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
}

/**
 * Root-level text filtering:
 * - The one part marked `isFinal` (the actual answer, mirrors Python's
 *   `TranscriptCollector.replace_final_text`) is never shown here —
 *   `AnswerContent` renders it.
 * - Every other root `text` part is narration and shown, UNLESS it's the
 *   last part in the array while still streaming (still being written).
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

export function AgentActivityTimeline({ parts, isNested = false, isStreaming = false }: AgentActivityTimelineProps) {
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
            <AgentActivityPart key={item.key} part={item.part} />
          ),
        )}
      </Flex>
    </Box>
  );
}

function AgentActivityPart({ part }: { part: MessagePart }) {
  switch (part.type) {
    case 'reasoning':
      return <ThinkingBlock content={part.content ?? ''} />;
    case 'tool_call':
      return <ToolCallCard part={part} />;
    case 'sub_agent':
      return <SubAgentGroup part={part} />;
    case 'text':
      return part.content ? <NarrationText content={part.content} /> : null;
    default:
      return null;
  }
}

/** Lightweight markdown-aware renderer for agent narration text (the "Let me
 * check X first..." lines the LLM emits between tool calls). Uses the same
 * `react-markdown` + `remark-gfm` pipeline as `AnswerContent` but with
 * minimal component overrides — narration is secondary to the main answer, so
 * it keeps the same font size / color but doesn't need citation processing,
 * KaTeX, or heading anchors. */
function NarrationText({ content }: { content: string }) {
  return (
    <Box className="narration-text" style={{ color: 'var(--slate-12)', fontSize: 'var(--font-size-2)', lineHeight: 1.6 }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <Text size="2" as="p" style={{ margin: '0 0 var(--space-1) 0', color: 'inherit' }}>
              {children}
            </Text>
          ),
          ul: ({ children }) => (
            <ul style={{ paddingLeft: 'var(--space-4)', margin: '0 0 var(--space-1) 0', listStyleType: 'disc' }}>
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol style={{ paddingLeft: 'var(--space-4)', margin: '0 0 var(--space-1) 0', listStyleType: 'decimal' }}>
              {children}
            </ol>
          ),
          li: ({ children }) => (
            <li style={{ marginBottom: 'var(--space-1)', lineHeight: 1.6, fontSize: '14px', color: 'inherit' }}>
              {children}
            </li>
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-11)' }}>
              {children}
            </a>
          ),
          code: ({ children }) => (
            <code style={{
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.9em',
              padding: '1px 4px',
              borderRadius: 'var(--radius-1)',
              backgroundColor: 'var(--slate-a3)',
            }}>
              {children}
            </code>
          ),
        }}
      >
        {content}
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
  failed: 'error',
  blocked: 'block',
};

const TOOL_STATUS_COLOR: Record<NonNullable<MessagePart['status']>, string> = {
  running: 'var(--blue-9)',
  completed: 'var(--green-9)',
  failed: 'var(--red-9)',
  blocked: 'var(--amber-9)',
};

/** Known tool → human-readable, model-independent activity label. Keyed by
 * the exact LLM-facing `{app}__{tool}` name the agent loop reports (see
 * `ToolCall.name` / `get_tool_name()` in `agent_loop_lib/tools/
 * decorators.py`) — double-underscore-separated, NOT dot-separated. */
const TOOL_ACTIVITY_LABELS: Record<string, string> = {
  web_search: 'Searched the web',
  fetch_url: 'Read a web page',
  web_scrape: 'Read a web page',
  retrieval__search_internal_knowledge: 'Searched the knowledge base',
  knowledgehub__list_files: 'Browsed the knowledge hub',
  dynamic__web_search: 'Searched the web',
  dynamic__fetch_url: 'Read a web page',
  sql__execute_sql_query: 'Ran a SQL query',
  dynamic_fetch_full_record: 'Fetched full document(s)',
  run_code: 'Ran code',
  install_packages: 'Installed packages',
  create_plan: 'Created a plan',
  spawn_agent: 'Delegated to a sub-agent',
  task_complete: 'Marked task complete',
  list_toolsets: 'Listed capabilities',
  fetch_tools: 'Loaded tools',
  search_tools: 'Searched for tools',
};

/** `"custom_connector__do_thing"` / `"jira__search_issues"` -> `"Do Thing"` /
 * `"Search Issues"` — drops any `{app}__` namespace prefix, splits on
 * separators, title-cases each word. */
function humanizeToolName(name: string): string {
  const segment = name.includes('__') ? name.slice(name.lastIndexOf('__') + 2) : name;
  const words = segment.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return 'Used a tool';
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
}

/** Known `{app}__` prefixes → human-friendly connector/toolset display name.
 * Falls back to title-casing the raw prefix for unknown connectors. */
const TOOLSET_DISPLAY_NAMES: Record<string, string> = {
  retrieval: 'Knowledge Base',
  knowledgehub: 'Knowledge Hub',
  dynamic: 'Web',
  sql: 'SQL',
  jira: 'Jira',
  jira_data_center: 'Jira DC',
  slack: 'Slack',
  confluence: 'Confluence',
  confluence_data_center: 'Confluence DC',
  github: 'GitHub',
  google_drive: 'Google Drive',
  google_calendar: 'Google Calendar',
  gmail: 'Gmail',
  clickup: 'ClickUp',
  one_drive: 'OneDrive',
  sharepoint: 'SharePoint',
  teams: 'Teams',
  outlook: 'Outlook',
  zoom: 'Zoom',
  salesforce: 'Salesforce',
  mariadb: 'MariaDB',
  redshift: 'Redshift',
  coding_sandbox: 'Code',
  database_sandbox: 'Database',
  image_generator: 'Image',
  artifacts: 'Artifacts',
  lumos: 'Lumos',
};

/** Extracts the toolset/connector prefix from a namespaced tool name and
 * returns its human-friendly display name. Returns `undefined` for tools
 * without a namespace prefix. */
function extractToolsetLabel(toolName: string | undefined): string | undefined {
  if (!toolName || !toolName.includes('__')) return undefined;
  const prefix = toolName.slice(0, toolName.lastIndexOf('__'));
  if (!prefix) return undefined;
  if (TOOLSET_DISPLAY_NAMES[prefix]) return TOOLSET_DISPLAY_NAMES[prefix];
  const words = prefix.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return undefined;
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

/** Derived, model-independent label for a tool-call part — never blank, never
 * the raw `toolName` verbatim for unknown tools (falls back to a humanized
 * version instead). */
export function toolActivityLabel(part: Pick<MessagePart, 'toolName'>): string {
  const { toolName } = part;
  if (!toolName) return 'Used a tool';
  return TOOL_ACTIVITY_LABELS[toolName] ?? humanizeToolName(toolName);
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
        <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
          {toolActivityLabel(part)}
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
export function SubAgentGroup({ part }: { part: MessagePart }) {
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
          <AgentActivityTimeline parts={nested} isNested />
        </Box>
      )}
    </Box>
  );
}
