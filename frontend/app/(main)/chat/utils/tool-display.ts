/**
 * How a step names its tool (TOOLNAMES), for the timeline, the steps header,
 * the activity drawer and streaming status: one registry (`TOOL_DISPLAY`,
 * `contracts/harness/ext/pipeshub/tools/tool-display.json`) read by the same
 * code as the Slack bot's `slack-bot/src/utils/tool-display.ts`; both pass
 * `contracts/harness/vectors/tool-display-names.json`. A built-in's action
 * reads from i18n `toolNames.<key>`, the registry's English being the fallback.
 */

import { TOOL_DISPLAY } from '@/lib/contracts/harness/generated/ext/pipeshub/policy-data';
import { i18n } from '@/lib/i18n';
import type { MessagePart } from '../types';

type Builtin = (typeof TOOL_DISPLAY.builtins)[number];

const BUILTINS = new Map<string, Builtin>(TOOL_DISPLAY.builtins.flatMap((b) => b.tools.map((t) => [t, b] as const)));
const TOOLSETS = new Map(TOOL_DISPLAY.toolsets.map((t) => [t.prefix, t.name] as const));
const HIDDEN: ReadonlySet<string> = new Set(TOOL_DISPLAY.hiddenToolsets);
const ACRONYMS = new Map(TOOL_DISPLAY.acronyms.map((a) => [a.toLowerCase(), a] as const));
const NO_TOOL = 'Used a tool';
const SEP = ' · ';

/** The five `load_skill`/`skill_search`/... tools (`agent_loop_lib/tools/builtin/data/skills.py`): ordinary
 * `TOOL_CALL_*` frames, told apart by exact name (no `{app}__` prefix on these built-ins). */
const SKILL_TOOLS: ReadonlySet<string> = new Set(['skills_list', 'skill_search', 'load_skill', 'load_skill_resource', 'skill_manage']);

/** True for the built-in skill tools — used to pick the skill icon/label
 * ahead of the generic search-like heuristic (`skill_search` must not get
 * the magnifying-glass treatment) and to gate the raw SKILL.md/file
 * `resultPreview` fallback off for skill tool calls. */
export function isSkillTool(toolName: string | undefined): boolean {
  return !!toolName && SKILL_TOOLS.has(toolName);
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

export interface ToolDescription {
  /** The tool ("Create ticket"). */
  name: string;
  /** The toolset or MCP server ("Jira", "E2Esaas"); undefined when hidden or unknown. */
  app?: string;
  /** What the step did ("Searched the web", "Create ticket"), in English. */
  label: string;
  /** What the step does while it runs, in English. */
  present: string;
  /** A built-in's i18n key under `toolNames`. */
  key?: string;
  /** The whole step on one line ("Jira · Create issue"); describing the tool again from it gives the same name,
   * app and label. */
  displayName: string;
}

function words(text: string): string[] {
  return text
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .split(/[\s_\-./]+/)
    .filter(Boolean);
}

function word(raw: string, capital: boolean): string {
  const lower = raw.toLowerCase();
  const known = ACRONYMS.get(lower);
  if (known) return known;
  const stem = lower.endsWith('s') ? ACRONYMS.get(lower.slice(0, -1)) : undefined;
  if (stem && stem === stem.toUpperCase()) return `${stem}s`;
  return capital ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
}

/** `source@toolset/tool`, `{app}__tool` (the last `__` wins) or `mcp_{server}_{tool}`, split. */
function split(id: string): { prefix?: string; bare: string; server?: string } {
  const at = id.indexOf('@');
  const slash = id.lastIndexOf('/');
  if (at > 0 && slash > at) return { prefix: id.slice(at + 1, slash), bare: id.slice(slash + 1) };
  const sep = id.lastIndexOf('__');
  if (sep >= 0) {
    const head = id.slice(0, sep);
    const inner = head.lastIndexOf('__');
    return { prefix: inner >= 0 ? head.slice(inner + 2) : head, bare: id.slice(sep + 2) };
  }
  // shortcut: `mcp_{server}_{tool}` splits at the first `_`; a server id with `_` reads wrong. Python sends the
  // server's display name on every MCP call, so this only names calls saved without one.
  const mcp = /^mcp_([^_]+)_(.+)$/.exec(id);
  return mcp ? { server: mcp[1], bare: mcp[2] } : { bare: id };
}

const titleCase = (text: string): string => words(text).map((w) => word(w, true)).join(' ');

function appOf(prefix: string | undefined): string | undefined {
  // An instance id (`3f2a-11c9`) is no name a person knows.
  if (!prefix || HIDDEN.has(prefix) || !/^[a-z0-9_]+$/.test(prefix)) return undefined;
  return TOOLSETS.get(prefix) ?? (titleCase(prefix) || undefined);
}

/** A tool id as words: no source, toolset or `dynamic__` prefix, sentence case, acronyms kept ("Get page by ID"). */
export function humanizeToolName(name: string): string {
  const shown = words(split(name).bare).map((w, i) => word(w, i === 0));
  return shown.length ? shown.join(' ') : NO_TOOL;
}

/** An id ("create_ticket", "getUser", "search") is worded by the humanizer; a name written for people ("Search",
 * "Searched the web") is kept as written. */
const isId = (text: string): boolean =>
  !/\s/.test(text) && (/[_\-./@]|[a-z][A-Z]/.test(text) || text === text.toLowerCase());

export function describeTool(toolName: string | undefined, displayName?: string): ToolDescription {
  const { prefix, bare, server } = split(toolName ?? '');
  const builtin = BUILTINS.get(bare);
  const shown = displayName?.trim() || undefined;
  const dot = shown?.indexOf(SEP) ?? -1;
  const shownApp = shown && dot > 0 ? shown.slice(0, dot).trim() || undefined : undefined;
  const shownTool = shown && dot > 0 ? shown.slice(dot + SEP.length).trim() || undefined : shown;
  const written = shownTool && !shownApp && !isId(shownTool) ? shownTool : undefined;
  const name = shownApp && shownTool ? (isId(shownTool) ? humanizeToolName(shownTool) : shownTool) : humanizeToolName(bare);
  const app = shownApp ?? appOf(prefix) ?? (server ? titleCase(server) : undefined);
  const named = app ? `${app}${SEP}${name}` : name;
  return {
    name,
    ...(app ? { app } : {}),
    label: builtin?.past ?? written ?? name,
    present: builtin?.present ?? (bare ? `Using ${named}` : 'Using a tool'),
    ...(builtin ? { key: builtin.key } : {}),
    displayName: builtin?.past ?? written ?? named,
  };
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


/** The app a step used: the toolset ("GitHub"), or the MCP server its display name names ("E2Esaas"). */
export function extractToolsetLabel(toolName: string | undefined, displayName?: string): string | undefined {
  if (!toolName) return undefined;
  return describeTool(toolName, displayName).app;
}

/** What a failed call's sentence names (ERR-UX): its connector ("Slack"), else the tool. */
export function toolSubject(toolName: string | undefined, displayName?: string): string {
  if (!toolName) return 'The tool';
  const tool = describeTool(toolName, displayName);
  return tool.app ?? tool.name;
}

/** What a step did (timeline rows, the steps header, the activity drawer): a built-in's translated action, else
 * the tool's written display name, else its humanized id. */
export function toolActivityLabel(toolName: string | undefined, displayName?: string): string {
  const tool = describeTool(toolName, displayName);
  return tool.key ? i18n.t(`toolNames.${tool.key}.past`, { defaultValue: tool.label }) : tool.label;
}

const SKILL_NAME = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const MAX_SHOWN_SKILLS = 8;

/** A server's `skills` list (FIX-SKILLSHOW), checked: skill folder names only; undefined when there are none. */
export function skillNames(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const names = value.filter((name): name is string => typeof name === 'string' && SKILL_NAME.test(name)).slice(0, MAX_SHOWN_SKILLS);
  return names.length > 0 ? names : undefined;
}

/** Built-in packs named by a file extension: their words are the format's own name, translated
 * (`agentActivity.skill.names`), since "docx" title-cased reads "Docx". */
const FORMAT_SKILLS: ReadonlySet<string> = new Set(['docx', 'xlsx', 'pptx']);

/** A skill's folder name as words ("pdf" → "PDF", "docx" → "Word document", "brand-guidelines" → "Brand
 * Guidelines"); a plugin prefix dropped. Every place that names a skill (the timeline, the builder) reads it here. */
export function skillDisplayName(name: string): string {
  const bare = name.slice(name.lastIndexOf(':') + 1);
  const format = bare.toLowerCase();
  if (FORMAT_SKILLS.has(format)) return i18n.t(`agentActivity.skill.names.${format}`);
  return titleCase(bare) || name;
}

/** A step that read skills, in words: "Read the PDF skill", "Reading the PDF and Word document skills". */
export function skillReadLabel(skills: readonly string[], running = false): string {
  const shown = skills.map(skillDisplayName);
  if (shown.length === 1) {
    return i18n.t(running ? 'agentActivity.skill.reading' : 'agentActivity.skill.read', { skill: shown[0] });
  }
  const list = new Intl.ListFormat(i18n.language || 'en', { type: 'conjunction' }).format(shown);
  return i18n.t(running ? 'agentActivity.skill.readingMany' : 'agentActivity.skill.readMany', { skills: list });
}

/** What a step does while it runs (streaming status). */
export function toolStatusLabel(toolName: string, displayName?: string): string {
  const tool = describeTool(toolName, displayName);
  if (tool.key) return i18n.t(`toolNames.${tool.key}.present`, { defaultValue: tool.present });
  if (!toolName) return tool.present;
  return i18n.t('toolNames.using', { tool: tool.app ? `${tool.app}${SEP}${tool.name}` : tool.name, defaultValue: tool.present });
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
