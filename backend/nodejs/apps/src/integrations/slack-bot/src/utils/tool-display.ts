/**
 * How a step names its tool (TOOLNAMES), from the one registry `TOOL_DISPLAY`
 * (`contracts/harness/ext/pipeshub/tools/tool-display.json`), for Slack's status lines. The web client's
 * `app/(main)/chat/utils/tool-display.ts` is the same code over the same data;
 * both pass `contracts/harness/vectors/tool-display-names.json`.
 */
import { TOOL_DISPLAY } from '../../../../modules/agent-harness/contracts/generated/ext/pipeshub/policy-data';

type Builtin = (typeof TOOL_DISPLAY.builtins)[number];

const BUILTINS = new Map<string, Builtin>(
  TOOL_DISPLAY.builtins.flatMap((b) => b.tools.map((t) => [t, b] as const)),
);
const TOOLSETS = new Map(
  TOOL_DISPLAY.toolsets.map((t) => [t.prefix, t.name] as const),
);
const HIDDEN: ReadonlySet<string> = new Set(TOOL_DISPLAY.hiddenToolsets);
const ACRONYMS = new Map(
  TOOL_DISPLAY.acronyms.map((a) => [a.toLowerCase(), a] as const),
);
const NO_TOOL = 'Used a tool';
const SEP = ' · ';

export interface ToolDescription {
  /** The tool ("Create ticket"). */
  name: string;
  /** The toolset or MCP server ("Jira", "E2Esaas"); undefined when hidden or unknown. */
  app?: string;
  /** What the step did ("Searched the web", "Create ticket"). */
  label: string;
  /** What the step does while it runs. */
  present: string;
  /** A built-in's i18n key under `toolNames`. */
  key?: string;
  /** The whole step on one line ("Jira · Create issue"); describing the tool again from it gives the same name,
   * app and label, so a surface may carry it on. */
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
  const stem = lower.endsWith('s')
    ? ACRONYMS.get(lower.slice(0, -1))
    : undefined;
  if (stem && stem === stem.toUpperCase()) return `${stem}s`;
  return capital ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
}

/** `source@toolset/tool`, `{app}__tool` (the last `__` wins) or `mcp_{server}_{tool}`, split. */
function split(id: string): { prefix?: string; bare: string; server?: string } {
  const at = id.indexOf('@');
  const slash = id.lastIndexOf('/');
  if (at > 0 && slash > at)
    return { prefix: id.slice(at + 1, slash), bare: id.slice(slash + 1) };
  const sep = id.lastIndexOf('__');
  if (sep >= 0) {
    const head = id.slice(0, sep);
    const inner = head.lastIndexOf('__');
    return {
      prefix: inner >= 0 ? head.slice(inner + 2) : head,
      bare: id.slice(sep + 2),
    };
  }
  // shortcut: `mcp_{server}_{tool}` splits at the first `_`; a server id with `_` reads wrong. Python sends the
  // server's display name on every MCP call, so this only names calls saved without one.
  const mcp = /^mcp_([^_]+)_(.+)$/.exec(id);
  const server = mcp?.[1];
  const bare = mcp?.[2];
  return server !== undefined && bare !== undefined
    ? { server, bare }
    : { bare: id };
}

const titleCase = (text: string): string =>
  words(text)
    .map((w) => word(w, true))
    .join(' ');

function appOf(prefix: string | undefined): string | undefined {
  // An instance id (`3f2a-11c9`) is no name a person knows.
  if (!prefix || HIDDEN.has(prefix) || !/^[a-z0-9_]+$/.test(prefix))
    return undefined;
  return TOOLSETS.get(prefix) ?? (titleCase(prefix) || undefined);
}

/** A tool as stable keys: its own name in snake case, its verb (the name's first word) and its app's machine key. */
export interface ToolKey {
  name: string;
  verb: string | null;
  /** The toolset prefix (`jira`) or MCP server; null where `describeTool` names no app. */
  app: string | null;
}

/**
 * The keys a surface words a tool with in its reader's language (UI-B5); `describeTool`'s English is the fallback.
 * Derived from the id alone, so a record kept before the keys existed reads the same.
 */
export function toolKey(toolName: string | undefined): ToolKey {
  const { prefix, bare, server } = split(toolName ?? '');
  const parts = words(bare)
    .map((w) => w.toLowerCase().replace(/[^a-z0-9]/g, ''))
    .filter(Boolean);
  const key = (text: string | undefined): string | null =>
    text !== undefined &&
    /^[a-z0-9][a-z0-9_.-]{0,127}$/.test(text.toLowerCase())
      ? text.toLowerCase()
      : null;
  const app =
    prefix !== undefined && appOf(prefix) !== undefined
      ? key(prefix)
      : key(server);
  return {
    name: parts.length > 0 ? parts.join('_').slice(0, 256) : 'tool',
    verb: parts[0]?.slice(0, 64) ?? null,
    app,
  };
}

/** A tool id as words: no source, toolset or `dynamic__` prefix, sentence case, acronyms kept ("Get page by ID"). */
export function humanizeToolName(name: string): string {
  const shown = words(split(name).bare).map((w, i) => word(w, i === 0));
  return shown.length ? shown.join(' ') : NO_TOOL;
}

/** An id ("create_ticket", "getUser", "search") is worded by the humanizer; a name written for people ("Search",
 * "Searched the web") is kept as written. */
const isId = (text: string): boolean =>
  !/\s/.test(text) &&
  (/[_\-./@]|[a-z][A-Z]/.test(text) || text === text.toLowerCase());

export function describeTool(
  toolName: string | undefined,
  displayName?: string,
): ToolDescription {
  const { prefix, bare, server } = split(toolName ?? '');
  const builtin = BUILTINS.get(bare);
  const shown = displayName?.trim() || undefined;
  const dot = shown?.indexOf(SEP) ?? -1;
  const shownApp =
    shown && dot > 0 ? shown.slice(0, dot).trim() || undefined : undefined;
  const shownTool =
    shown && dot > 0
      ? shown.slice(dot + SEP.length).trim() || undefined
      : shown;
  const written =
    shownTool && !shownApp && !isId(shownTool) ? shownTool : undefined;
  const name =
    shownApp && shownTool
      ? isId(shownTool)
        ? humanizeToolName(shownTool)
        : shownTool
      : humanizeToolName(bare);
  const app =
    shownApp ?? appOf(prefix) ?? (server ? titleCase(server) : undefined);
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

/** Past-tense label for a finished step in Slack's activity ("Jira · Create issue", "Searched the web"). */
export function toolActivityLabel(
  toolName: string | undefined,
  displayName?: string,
): string {
  return describeTool(toolName, displayName).displayName;
}

/** Present-tense label for a running step. */
export function toolStatusLabel(toolName: string, displayName?: string): string {
  return describeTool(toolName, displayName).present;
}
