/**
 * Shared tool name display utilities used by both streaming status messages
 * and the agent activity timeline.
 */

import type { MessagePart } from '../types';

/**
 * The five `load_skill`/`skill_search`/... tools (see
 * `backend/python/app/agent_loop_lib/tools/builtin/data/skills.py`) go over
 * AG-UI as ordinary `TOOL_CALL_*` frames — there is no protocol-level
 * "skill" event. This map is the frontend half of the same present/past
 * tense split that file's `display_name`/`summarize_args`/`summarize_result`
 * establish server-side; it is also the ONLY source of skill labels for
 * chats persisted before that change (no `displayName` on the part), so it
 * must stay exact-name-keyed (no `{app}__` prefix on these built-ins) and
 * never assume `displayName` is present.
 */
const SKILL_TOOL_LABELS: Record<string, { present: string; past: string }> = {
  skills_list: { present: 'Listing skills', past: 'Listed skills' },
  skill_search: { present: 'Searching skills', past: 'Searched skills' },
  load_skill: { present: 'Loading skill', past: 'Loaded skill' },
  load_skill_resource: { present: 'Loading skill file', past: 'Loaded skill file' },
  skill_manage: { present: 'Managing skill', past: 'Managed skill' },
};

/** True for the built-in skill tools — used to pick the skill icon/label
 * ahead of the generic search-like heuristic (`skill_search` must not get
 * the magnifying-glass treatment) and to gate the raw SKILL.md/file
 * `resultPreview` fallback off for skill tool calls. */
export function isSkillTool(toolName: string | undefined): boolean {
  return !!toolName && Object.prototype.hasOwnProperty.call(SKILL_TOOL_LABELS, toolName);
}

/** MCP tools are named `mcp_{server}_{tool}`; toolset tools carry an `{app}__` prefix instead. */
export function isMcpTool(toolName: string | undefined): boolean {
  return !!toolName && toolName.startsWith('mcp_') && !toolName.includes('__');
}

const WEB_TOOLS = new Set(['web_search', 'fetch_url']);

/** The web search and page-reading tools, with or without their `dynamic__` prefix. */
export function isWebTool(toolName: string | undefined): boolean {
  return !!toolName && WEB_TOOLS.has(toolName.replace(/^dynamic__/, ''));
}

/** Drops any `{app}__` namespace prefix, splits on separators, title-cases. */
export function humanizeToolName(name: string): string {
  const segment = name.includes('__') ? name.slice(name.lastIndexOf('__') + 2) : name;
  const words = segment.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return 'Used a tool';
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
}

/** True for the UI questionnaire tool, whose result is rendered as the card
 *  rather than as prose — callers use this to keep the empty-answer fallback
 *  out of a turn that is only a question. */
export function isAskUserQuestionTool(toolName: string | undefined): boolean {
  return typeof toolName === 'string' && toolName.includes('ask_user_question');
}

/**
 * A resumed reply's parts, added to the row it carries on.
 * - A call the row already has (the call an approval card stopped on, now settled) replaces it
 *   where it is, inside a sub-agent too, and keeps the card's details.
 * - Resume can re-emit the same ask_user_question call — the first one is kept.
 */
export function appendResumeParts(existing: MessagePart[], incoming: MessagePart[]): MessagePart[] {
  const hasAsk = existing.some((p) => p.type === 'tool_call' && isAskUserQuestionTool(p.toolName));
  let merged = existing;
  const extra: MessagePart[] = [];
  for (const part of incoming) {
    if (hasAsk && part.type === 'tool_call' && isAskUserQuestionTool(part.toolName)) continue;
    const replaced = part.type === 'tool_call' && part.toolCallId ? replaceToolCall(merged, part) : null;
    if (replaced) merged = replaced;
    else extra.push(part);
  }
  return extra.length ? [...merged, ...extra] : merged;
}

function replaceToolCall(parts: MessagePart[], next: MessagePart): MessagePart[] | null {
  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    if (part.type === 'tool_call' && part.toolCallId === next.toolCallId) {
      const settled = part.approval && !next.approval ? { ...next, approval: part.approval } : next;
      return parts.map((p, j) => (j === i ? settled : p));
    }
    if (part.type === 'sub_agent' && part.parts?.length) {
      const inner = replaceToolCall(part.parts, next);
      if (inner) return parts.map((p, j) => (j === i ? { ...part, parts: inner } : p));
    }
  }
  return null;
}

/** Extracts the toolset/connector prefix display name. MCP tools are named
 * `mcp_{server}_{tool}` with single underscores, so the server part can't be
 * split out reliably; they get a generic "MCP" chip and the server name comes
 * from the backend's `displayName`. */
export function extractToolsetLabel(toolName: string | undefined): string | undefined {
  // Built-in web tools are not a toolset: their `dynamic__` prefix would read as "Dynamic".
  if (!toolName || isWebTool(toolName)) return undefined;
  if (!toolName.includes('__')) return isMcpTool(toolName) ? 'MCP' : undefined;
  const prefix = toolName.slice(0, toolName.lastIndexOf('__'));
  if (!prefix) return undefined;
  const words = prefix.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return undefined;
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

/** Human-friendly label for a tool call (past tense, for timeline).
 *  Uses backend-provided `displayName` when available, falls back to generic humanization. */
export function toolActivityLabel(toolName: string | undefined, displayName?: string): string {
  if (displayName) return displayName;
  if (!toolName) return 'Used a tool';
  return SKILL_TOOL_LABELS[toolName]?.past ?? humanizeToolName(toolName);
}

/** Human-friendly label for a tool call in progress (present tense, for streaming status). */
export function toolStatusLabel(toolName: string, displayName?: string): string {
  if (displayName) return displayName;
  return SKILL_TOOL_LABELS[toolName]?.present ?? `Using ${humanizeToolName(toolName)}`;
}

/** The SQL tool's name in the agent loop (`sql__execute_sql_query`, see
 * `tool_summarizer.py`) and in chat modes (`execute_sql_query`). */
const SQL_QUERY_TOOL_NAMES = new Set(['sql__execute_sql_query', 'execute_sql_query']);

/** A SQL tool call's query. `cutOff`: `text` is only the start of the statement (a
 * prefix such as `... WHERE active = true`), shortened by the server or cut with the args. */
export interface SqlQuery {
  text: string;
  cutOff: boolean;
}

/** What `serialize_tool_args` (`transcript_collector.py`) appends to a value it shortens. */
const SERVER_CUT_NOTE = /… \[truncated, \d+ of \d+ chars\]$/;

/** Reads a JSON string field out of JSON that may be cut off: args streamed by a
 * server that sends no `argsPreview`, or saved before the server shortened values
 * in place. `cutOff` is set when the cut reached the value. */
function extractTruncatedJsonStringField(text: string, key: string): SqlQuery | undefined {
  const match = new RegExp(`"${key}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)(")?`).exec(text);
  if (!match) return undefined;
  // Drop a `\uXX` escape that the cap cut in half, unless its backslash is itself escaped.
  const raw = match[1].replace(/(^|[^\\])((?:\\\\)*)\\u[0-9a-fA-F]{0,3}$/, '$1$2');
  try {
    const value = JSON.parse(`"${raw}"`) as string;
    return value.trim() ? { text: value, cutOff: match[2] === undefined } : undefined;
  } catch {
    return undefined;
  }
}

/** The query of a SQL tool call's raw `args` JSON, shown in the tool card's Query
 * section. Undefined for any other tool, or when no query is left. */
export function extractSqlQuery(toolName: string | undefined, args: string | undefined): SqlQuery | undefined {
  if (!toolName || !SQL_QUERY_TOOL_NAMES.has(toolName) || !args) return undefined;
  try {
    const parsed: unknown = JSON.parse(args);
    const query = (parsed as { query?: unknown } | null)?.query;
    if (typeof query !== 'string' || !query.trim()) return undefined;
    const note = SERVER_CUT_NOTE.exec(query);
    return note ? { text: query.slice(0, note.index), cutOff: true } : { text: query, cutOff: false };
  } catch {
    return extractTruncatedJsonStringField(args, 'query');
  }
}
