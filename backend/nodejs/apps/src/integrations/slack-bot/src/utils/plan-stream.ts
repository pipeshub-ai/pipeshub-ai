import {
  NO_UNFURL_OPTIONS,
  SLACK_STREAM_MESSAGE_CHAR_LIMIT,
  type SlackBlock,
  type TypedSlackClient,
} from '../helpers';
import { groupToolsetLabel, humanizeToolName } from './tool-display';

/** Title of the plan block that holds the steps. */
export const SLACK_PLAN_TITLE = 'Thinking';
/** Task and plan chunks are reported to allow 256 characters; narration rows stay under it. */
export const MAX_TASK_TITLE_CHARS = 250;
/** A task's `details` is documented by the Agents SDK as at most 256 characters: one summary line. */
export const MAX_TASK_DETAILS_CHARS = 256;
/**
 * Room for one row's per-call breakdown in `output`. Slack publishes no
 * threshold for msg_blocks_too_long, so rows stay small; a row Slack still
 * refuses is shrunk, never split.
 */
export const MAX_TASK_OUTPUT_CHARS = 2500;
const MAX_REASONING_CHARS = 2500;
const MAX_TASK_SOURCES = 10;
const APPROVAL_OUTPUT = 'Waiting for your approval';
/** The row that says the agent is still working between steps. */
export const THINKING_TITLE = 'Thinking…';
export const WRITING_TITLE = 'Writing the answer';
/** The dashboard's chips for a call that needed approval. */
const APPROVAL_CHIPS: Record<string, string> = {
  allowed: 'Approved',
  declined: 'Declined',
  expired: 'Expired',
};
const SQL_QUERY_TOOL_NAMES = new Set([
  'sql__execute_sql_query',
  'execute_sql_query',
]);
const MAX_ARG_VALUE_CHARS = 120;
const MAX_ARGS_CHARS = 300;
/** A fence line per CommonMark: up to 3 spaces, then 3+ backticks or tildes. */
const FENCE_LINE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
/** Room kept free in a full message to close a code fence before moving on. */
const FENCE_ROOM = 24;

/**
 * The fence still open at the end of `text`, if any, per CommonMark: a block
 * opened by N backticks (or tildes) closes only on a line of at least N of the
 * same character with nothing after it but spaces.
 */
export function openFence(
  text: string,
): { marker: string; opener: string } | null {
  let open: string | null = null;
  let opener = '';
  for (const line of text.split('\n')) {
    const match = FENCE_LINE.exec(line);
    if (!match) continue;
    const [, marker = '', rest = ''] = match;
    if (open === null) {
      // A backtick fence's info string can't hold a backtick.
      if (marker.startsWith('`') && rest.includes('`')) continue;
      open = marker;
      opener = line.trim();
    } else if (
      marker[0] === open[0] &&
      marker.length >= open.length &&
      rest.trim() === ''
    ) {
      open = null;
    }
  }
  return open === null ? null : { marker: open, opener };
}
/** Citation placeholders the backend gives the model instead of real URLs. */
const PLACEHOLDER_HOST = /^ref\d+\.xyz$/i;

/** The statuses Slack documents for task_update (no `pending`). */
export type SlackTaskStatus = 'in_progress' | 'complete' | 'error';

export interface SlackUrlSource {
  type: 'url';
  url: string;
  text: string;
}

export interface SlackTaskChunk {
  type: 'task_update';
  id: string;
  title: string;
  status: SlackTaskStatus;
  details?: string;
  output?: string;
  sources?: SlackUrlSource[];
}

export type SlackStreamChunk =
  | SlackTaskChunk
  | { type: 'plan_update'; title: string }
  | { type: 'markdown_text'; text: string };

/**
 * Text cut into rows of at most `limit` characters, at sentence ends where it
 * can, else at spaces: every word is kept, nothing is cut off with an ellipsis.
 */
export function splitIntoRows(
  text: string,
  limit = MAX_TASK_TITLE_CHARS,
): string[] {
  const rows: string[] = [];
  let rest = text.replace(/\s+/g, ' ').trim();
  while (rest.length > limit) {
    const window = rest.slice(0, limit + 1);
    const sentenceEnd = Math.max(
      ...['. ', '! ', '? ', '; '].map((end) => window.lastIndexOf(end)),
    );
    const space = window.lastIndexOf(' ');
    const cut =
      sentenceEnd > limit / 3 ? sentenceEnd + 1 : space > 0 ? space : limit;
    rows.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) rows.push(rest);
  return rows;
}

/** Name segments that say a key holds a secret. */
const SECRET_SEGMENTS = new Set([
  'token',
  'secret',
  'password',
  'passwd',
  'pwd',
  'key',
  'credential',
  'credentials',
  'auth',
  'authorization',
  'apikey',
  'privatekey',
]);

/**
 * Whether a key name says it holds a secret, read by its segments (split at
 * `_ - .`, spaces and camelCase): api_key, apiKey, x-api-key and secret_key
 * do; monkey, keyboard, author and authority don't.
 */
export function isSecretKey(name: string): boolean {
  return name
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .split(/[\s_.-]+/)
    .some((segment) => SECRET_SEGMENTS.has(segment.toLowerCase()));
}

const KEY_NAME = String.raw`[A-Za-z][A-Za-z0-9_.-]*`;
/**
 * A key name starts only where one can't continue: a plain `\b` would also
 * try every letter inside a long `a.a.a…` run, which turns quadratic.
 */
// Leading dashes may come first, as in a CLI flag (`--password=…`); a match still starts only once per run.
const KEY_START = String.raw`(?<![A-Za-z0-9_.-])-*`;
/** Text a row can show, plus room for a value cut at the edge: nothing longer is scanned. */
const MAX_REDACT_CHARS = MAX_TASK_OUTPUT_CHARS + 200;
/** `"key": "value with spaces"` / `key='…'`: masked to the end of the quoted value. */
const QUOTED_VALUE = new RegExp(
  String.raw`(["']?)${KEY_START}(${KEY_NAME})\1(\s*[:=]\s*)(["'])(?:\\.|(?!\4)[^\\])*\4`,
  'g',
);
/** `?key=…&` in a URL: masked to the next parameter. */
const QUERY_VALUE = new RegExp(
  String.raw`([?&;])(${KEY_NAME})=([^&#\s]*)`,
  'g',
);
/** `key: value` / `key=value` unquoted. */
const PLAIN_KEY = new RegExp(
  String.raw`${KEY_START}(${KEY_NAME})(\s*[:=]\s*)`,
  'g',
);
/** `Authorization: Basic|Bearer|Token …`: the whole value. */
const AUTH_HEADER =
  /\b(authorization\s*[:=]\s*)["']?(?:basic|bearer|token)\b[^\n"']*/gi;
const BEARER = /\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}/g;
/** `https://user:pass@host`: the user info goes. */
const URL_USERINFO = /\b(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi;
const SECRET_TOKEN =
  /\b(?:xox[abeposr]-[\w-]+|sk-ant-[\w-]+|sk-[A-Za-z0-9_-]{16,}|phpat_[\w-]+|github_pat_\w+|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[\w-]{10,}|AKIA[0-9A-Z]{16}|eyJ[\w-]+\.[\w-]+\.[\w-]+)/g;
const REDACTED = '[redacted]';

/** An unquoted secret value is masked to the end of its line. */
function maskPlainSecrets(text: string): string {
  let out = '';
  let from = 0;
  PLAIN_KEY.lastIndex = 0;
  for (let match = PLAIN_KEY.exec(text); match; match = PLAIN_KEY.exec(text)) {
    const valueAt = match.index + match[0].length;
    const next = text[valueAt] ?? '';
    if (!isSecretKey(match[1] ?? '') || next === '' || /["'[\n]/.test(next)) {
      continue;
    }
    const lineEnd = text.indexOf('\n', valueAt);
    const end = lineEnd === -1 ? text.length : lineEnd;
    out += `${text.slice(from, valueAt)}${REDACTED}`;
    from = end;
    PLAIN_KEY.lastIndex = end;
  }
  return out + text.slice(from);
}

/** Keys, tokens and passwords in what a call shows never reach Slack. */
export function redactSecrets(text: string): string {
  const masked = text
    .slice(0, MAX_REDACT_CHARS)
    .replace(URL_USERINFO, '$1***@')
    .replace(AUTH_HEADER, `$1${REDACTED}`)
    .replace(
      QUOTED_VALUE,
      (match, quote: string, key: string, sep: string, valueQuote: string) =>
        isSecretKey(key)
          ? `${quote}${key}${quote}${sep}${valueQuote}${REDACTED}${valueQuote}`
          : match,
    )
    .replace(QUERY_VALUE, (match, lead: string, key: string) =>
      isSecretKey(key) ? `${lead}${key}=${REDACTED}` : match,
    );
  return maskPlainSecrets(masked)
    .replace(BEARER, `$1 ${REDACTED}`)
    .replace(SECRET_TOKEN, REDACTED);
}

/**
 * A call's raw arguments as compact `key: value` pairs, for a tool the server
 * has no summary for. Nested values are left out: no JSON reaches Slack.
 */
export function compactArgs(raw: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return '';
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return '';
  const pairs = Object.entries(parsed as Record<string, unknown>).flatMap(
    ([key, value]) => {
      if (value === null || value === undefined || value === '') return [];
      if (
        typeof value !== 'string' &&
        typeof value !== 'number' &&
        typeof value !== 'boolean'
      ) {
        return [];
      }
      const text = String(value).replace(/\s+/g, ' ').trim();
      const shown =
        text.length > MAX_ARG_VALUE_CHARS
          ? `${text.slice(0, MAX_ARG_VALUE_CHARS).trimEnd()}…`
          : text;
      return [isSecretKey(key) ? `${key}: [redacted]` : `${key}: ${shown}`];
    },
  );
  const joined = redactSecrets(pairs.join(', '));
  return joined.length > MAX_ARGS_CHARS
    ? `${joined.slice(0, MAX_ARGS_CHARS).trimEnd()}…`
    : joined;
}

/** A SQL call's query, as the dashboard's Query section shows it. */
export function sqlQuery(
  toolName: string,
  raw: string | undefined,
): string | undefined {
  if (!SQL_QUERY_TOOL_NAMES.has(toolName) || !raw) return undefined;
  try {
    const query = (JSON.parse(raw) as { query?: unknown } | null)?.query;
    return typeof query === 'string' && query.trim() ? query.trim() : undefined;
  } catch {
    return undefined;
  }
}

/** What a finished tool call reported: the server's one-line result and its view of what it found. */
export interface SlackToolResult {
  summary?: string;
  view?: unknown;
}

type CallStatus =
  'running' | 'completed' | 'failed' | 'blocked' | 'awaiting_approval';

interface ToolCall {
  id: string;
  toolName: string;
  label: string;
  status: CallStatus;
  args?: string;
  raw?: string;
  approvalAnswer?: string;
  result?: string;
  progress?: string;
  sources: SlackUrlSource[];
}

function clip(text: string, limit = MAX_TASK_DETAILS_CHARS): string {
  const trimmed = text.trim();
  return trimmed.length <= limit
    ? trimmed
    : `${trimmed.slice(0, limit).trimEnd()}…`;
}

function slackErrorCode(error: unknown): string | undefined {
  return (error as { data?: { error?: string } } | undefined)?.data?.error;
}

function asCallStatus(status: string | undefined): CallStatus {
  return status === 'failed' ||
    status === 'blocked' ||
    status === 'awaiting_approval'
    ? status
    : 'completed';
}

/** A real http(s) link from a tool's view, never a citation placeholder. */
function realLink(value: unknown): URL | null {
  if (typeof value !== 'string' || value.length > 2000) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    if (url.username !== '' || url.password !== '') return null;
    if (PLACEHOLDER_HOST.test(url.hostname)) return null;
    return url;
  } catch {
    return null;
  }
}

/** The pages a tool's result view links to (a web search's results, the page read). */
export function viewSources(view: unknown): SlackUrlSource[] {
  if (!view || typeof view !== 'object' || Array.isArray(view)) return [];
  const raw = view as {
    kind?: unknown;
    rows?: unknown;
    url?: unknown;
    fields?: unknown;
  };
  const found: { url: unknown; text: unknown }[] = [];
  if (raw.kind === 'records' && Array.isArray(raw.rows)) {
    for (const row of raw.rows as { url?: unknown; cells?: unknown }[]) {
      const cells = Array.isArray(row.cells) ? (row.cells as unknown[]) : [];
      found.push({ url: row.url, text: cells[0] });
    }
  } else if (raw.kind === 'fields') {
    const fields = Array.isArray(raw.fields)
      ? (raw.fields as { value?: unknown }[])
      : [];
    found.push({ url: raw.url, text: fields[0]?.value });
  }
  const sources: SlackUrlSource[] = [];
  const seen = new Set<string>();
  for (const { url, text } of found) {
    const link = realLink(url);
    if (!link) continue;
    for (const key of [...link.searchParams.keys()]) {
      if (isSecretKey(key)) link.searchParams.delete(key);
    }
    // A token elsewhere in the link (its path, its fragment): the link stays out.
    if (redactSecrets(link.href) !== link.href || seen.has(link.href)) continue;
    seen.add(link.href);
    const title = typeof text === 'string' ? text.trim() : '';
    sources.push({
      type: 'url',
      url: link.href,
      text: clip(
        title === ''
          ? link.hostname.replace(/^www\./, '')
          : redactSecrets(title),
        120,
      ),
    });
    if (sources.length >= MAX_TASK_SOURCES) break;
  }
  return sources;
}

interface ToolGroup {
  calls: ToolCall[];
  /** Set for a sub-agent's row: its calls are listed under it. */
  agent?: { role: string; status: 'running' | 'completed' | 'failed' };
  /** No call joins it any more: text came after it, or its sub-agent ended. */
  closed?: boolean;
  /** Its details, output and sources went out: Slack adds a resent value to the shown one. */
  sealed?: boolean;
}

/** Lines a row's output shows before it sums up the rest. */
const MAX_OUTPUT_LINES = 6;
const MAX_ARGUMENT_CHARS = 80;
const MAX_OUTCOME_CHARS = 90;
const UUID =
  /\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;
const LONG_ID = /\b([A-Za-z0-9]{8})[A-Za-z0-9_-]{16,}\b/g;

function shortIds(text: string): string {
  return text.replace(UUID, '$1').replace(LONG_ID, '$1');
}

/**
 * A call's main argument from the server's summary: a quoted query as
 * quoted, else the summary without the tool's name ("Get Record Download Url
 * — record_id: 8dc0…" gives "record 8dc0d3d5"). Ids are cut to 8 characters.
 */
/** `key: "value"` or a bare `"value"` in a call's summary. */
const QUOTED_ARGUMENT = /(?:([A-Za-z][\w.-]*)\s*[:=]\s*)?"([^"\n]*)"/g;

function mainArgument(summary: string, label: string): string {
  // Redact first: a quoted value cut from its key can no longer be recognised as a secret.
  // Each quoted value is masked on its own, so a secret in it can't take its closing quote; a secret key's value is never shown.
  let quoted: string | undefined;
  for (const match of summary.matchAll(QUOTED_ARGUMENT)) {
    const key = match[1];
    const value = redactSecrets(match[2] ?? '').trim();
    if (
      (key !== undefined && isSecretKey(key)) ||
      value === '' ||
      value === REDACTED
    ) {
      continue;
    }
    quoted = value;
    break;
  }
  const safe = redactSecrets(summary);
  let text = quoted ?? safe;
  if (quoted === undefined) {
    const lower = text.toLowerCase();
    const name = label.toLowerCase();
    if (lower.startsWith(name)) {
      text = text.slice(label.length).replace(/^\s*(?:—|-|:)\s*/, '');
    }
    // The server's generic summary is "<tool name> — <args>": the row's title already names the tool.
    text = text.replace(/^[^—\n]*?\s—\s/, '');
    text = text.replace(/\b([a-z]+)_id:\s*/gi, '$1 ');
  }
  const shortened = shortIds(text).trim();
  if (!shortened) return '';
  const clipped = clip(shortened, MAX_ARGUMENT_CHARS);
  return quoted === undefined ? clipped : `"${clipped}"`;
}

const BLOCKED_OUTPUT = 'Needs approval in the PipesHub app';
/** The backend's reason for a call held for someone's approval, not run. */
const APPROVAL_HOLD =
  /\bneeds? (?:someone to )?approv|\brequires? approval|\bwas(?:n't| not) run\b/i;
/** The tools that load an app's tools, by the app's id. */
const TOOL_LOADERS = new Set(['fetch_tools']);
/** Titles for the tools that find and load other tools: their names say nothing to a reader. */
const META_TOOL_TITLES: Record<string, string> = {
  list_toolsets: 'Listed apps',
  fetch_tools: 'Loaded tools',
  search_tools: 'Searched for tools',
};

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** An identifier as words in sentence case: "create_ticket" gives "Create ticket". */
function sentenceCase(name: string): string {
  return capitalize(
    name
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .split(/[_\-\s]+/)
      .filter(Boolean)
      .join(' ')
      .toLowerCase(),
  );
}

/** An app's id as its name: "mcp_tracker" gives "Tracker". */
function appName(id: string): string {
  return id
    .replace(/^mcp_/, '')
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map(capitalize)
    .join(' ');
}

/**
 * A row's title, a plain action, and the app it acts in. A server's
 * "Tracker · create_ticket" gives "Create ticket" in "Tracker"; a name with
 * no display name is put in words.
 */
export function rowLabel(
  toolName: string,
  label: string,
): { title: string; app?: string } {
  const meta = META_TOOL_TITLES[toolName.replace(/^dynamic__/, '')];
  if (meta !== undefined) return { title: meta };
  const at = label.indexOf(' · ');
  if (at > 0) {
    const action = label.slice(at + 3).trim();
    return {
      title: /^[\w.-]+$/.test(action) ? sentenceCase(action) : action,
      app: label.slice(0, at).trim(),
    };
  }
  return /^[\w.-]+$/.test(label) || label === humanizeToolName(toolName)
    ? { title: sentenceCase(label) }
    : { title: label };
}

/** The first sentence (or line) of a result. */
function firstSentence(text: string): string {
  const line =
    text
      .split('\n')
      .find((l) => l.trim() !== '')
      ?.trim() ?? '';
  const end = line.search(/[.!?](\s|$)/);
  return end > 0 ? line.slice(0, end) : line;
}

/**
 * Turns agent activity into rows of Slack's plan the way the dashboard's
 * thinking trail shows it: the agent's text as rows of its own, tool calls
 * of one kind with no text between them in one row titled by what they did,
 * each sub-agent in a row with its own calls, and reasoning in a row of its
 * own. A row's details names the app; its output has one line per call.
 */
export class SlackTaskPlan {
  private readonly tasks = new Map<string, SlackTaskChunk>();
  private readonly groups = new Map<string, ToolGroup>();
  private readonly groupOf = new Map<string, string>();
  private readonly openIdsByKey = new Map<string, string[]>();
  private readonly agentGroups = new Map<string, string>();
  /** Rows that wait on someone (an approval): never failed when the reply stops. */
  private readonly waiting = new Set<string>();
  private openGroup: string | null = null;
  private sealedRows: SlackTaskChunk[] = [];
  /** The "Thinking…" row shown in a quiet gap: the next step takes its place. */
  private placeholder: string | null = null;
  /** The placeholder once the answer began: it ends with the reply. */
  private writing: string | null = null;
  private reasoning = '';
  private finishedReasoning = '';
  private seq = 0;

  /**
   * The plan went quiet after a step: a row says the agent is still at it.
   * The next narration, step or answer takes its place, so it never stays
   * behind. None while a step runs or waits, or once the answer began.
   */
  thinking(): SlackTaskChunk[] {
    if (
      this.placeholder !== null ||
      this.writing !== null ||
      this.tasks.size === 0
    ) {
      return [];
    }
    if (
      [...this.tasks.values()].some((task) => task.status === 'in_progress')
    ) {
      return [];
    }
    this.placeholder = `next-${String(++this.seq)}`;
    return [
      this.put({
        type: 'task_update',
        id: this.placeholder,
        title: THINKING_TITLE,
        status: 'in_progress',
      }),
    ];
  }

  /** The answer began: the "Thinking…" row says so, and ends with the reply. */
  answerStarted(): SlackTaskChunk[] {
    const id = this.takePlaceholder();
    if (id === undefined) return [];
    this.writing = id;
    return [
      this.put({
        type: 'task_update',
        id,
        title: WRITING_TITLE,
        status: 'in_progress',
      }),
    ];
  }

  /** Rows that are open without being a step: they move to a new message and end with the reply. */
  private isLiveRow(id: string): boolean {
    return id === this.placeholder || id === this.writing;
  }

  private takePlaceholder(): string | undefined {
    const id = this.placeholder ?? undefined;
    this.placeholder = null;
    return id;
  }

  /** Something else began after the answer started: its row ends. */
  private settleWriting(): SlackTaskChunk[] {
    const id = this.writing;
    if (id === null) return [];
    this.writing = null;
    return [
      this.put({
        type: 'task_update',
        id,
        title: WRITING_TITLE,
        status: 'complete',
      }),
    ];
  }

  /** Text came between steps: the next tool call starts a new row. */
  endGroup(): void {
    if (this.openGroup === null) return;
    const group = this.groups.get(this.openGroup);
    if (group) group.closed = true;
    this.sealedRows.push(...this.render(this.openGroup));
    this.openGroup = null;
  }

  /** Rows sealed since the last call, to send now: a closed row's details and output. */
  takeSealed(): SlackTaskChunk[] {
    const rows = this.sealedRows;
    this.sealedRows = [];
    return rows;
  }

  /** What the agent wrote before a step, as its own rows in the plan, in full. */
  narration(text: string): SlackTaskChunk[] {
    this.endGroup();
    const settled = this.settleWriting();
    return [
      ...settled,
      ...splitIntoRows(redactSecrets(text)).map((title) =>
        this.put({
          type: 'task_update',
          id: this.takePlaceholder() ?? `note-${String(++this.seq)}`,
          title,
          status: 'complete',
        }),
      ),
    ];
  }

  reasoningDelta(delta: string): void {
    this.reasoning += delta;
  }

  /** A reasoning segment ended. Its row is sent with the next step, so a discarded call can still take it back. */
  reasoningDone(): void {
    this.finishedReasoning = [this.finishedReasoning, this.reasoning.trim()]
      .filter(Boolean)
      .join('\n\n');
    this.reasoning = '';
  }

  /** The model call that streamed this reasoning failed and is retried. */
  dropReasoning(): void {
    this.reasoning = '';
    this.finishedReasoning = '';
  }

  /** Finished reasoning as its own row, before the next step or the answer. */
  flushReasoning(): SlackTaskChunk | null {
    const text = clip(
      redactSecrets(this.finishedReasoning),
      MAX_REASONING_CHARS,
    );
    this.finishedReasoning = '';
    if (!text) return null;
    this.endGroup();
    return this.put({
      type: 'task_update',
      id: this.takePlaceholder() ?? `reasoning-${String(++this.seq)}`,
      title: 'Reasoning',
      status: 'complete',
      output: text,
    });
  }

  toolStarted(
    toolName: string,
    label: string,
    toolCallId?: string,
    meta: { agent?: string; approvalAnswer?: string } = {},
  ): SlackTaskChunk[] {
    const key = `tool:${toolName}`;
    const id = toolCallId ?? `${key}-${String(++this.seq)}`;
    // A call that starts again (its approval answered) goes on in its own row.
    const again = this.groupOf.get(id);
    const known =
      again === undefined
        ? undefined
        : this.groups.get(again)?.calls.find((c) => c.id === id);
    if (again !== undefined && known) {
      known.status = 'running';
      if (meta.approvalAnswer) known.approvalAnswer = meta.approvalAnswer;
      this.openIdsByKey.set(key, [...(this.openIdsByKey.get(key) ?? []), id]);
      return this.render(again);
    }
    this.openIdsByKey.set(key, [...(this.openIdsByKey.get(key) ?? []), id]);
    const agentGroup = meta.agent
      ? this.agentGroups.get(meta.agent)
      : undefined;
    let group = agentGroup;
    if (group === undefined) {
      // A row holds one kind of call: a different tool starts a row of its own.
      const open =
        this.openGroup === null ? undefined : this.groups.get(this.openGroup);
      // After a quiet gap the agent took a new turn: its calls take the "Thinking…" row's place.
      if (
        this.placeholder !== null ||
        open?.calls.some((call) => call.toolName !== toolName)
      ) {
        this.endGroup();
      }
      if (this.openGroup === null) {
        this.openGroup =
          this.takePlaceholder() ?? `tools-${String(++this.seq)}`;
        this.groups.set(this.openGroup, { calls: [] });
      }
      group = this.openGroup;
    }
    const settled = this.settleWriting();
    this.groups.get(group)?.calls.push({
      id,
      toolName,
      label,
      status: 'running',
      sources: [],
      ...(meta.approvalAnswer ? { approvalAnswer: meta.approvalAnswer } : {}),
    });
    this.groupOf.set(id, group);
    return [...settled, ...this.render(group)];
  }

  /** A call's arguments: the server's summary, and the raw arguments for what it leaves out. */
  toolArgs(
    toolName: string,
    args: { summary?: string; raw?: string },
    toolCallId?: string,
  ): SlackTaskChunk[] {
    const call = this.findCall(toolName, toolCallId);
    if (!call) return [];
    const summary = args.summary?.trim();
    const changed =
      (summary !== undefined && summary !== '' && summary !== call.args) ||
      (args.raw !== undefined && args.raw !== call.raw);
    if (!changed) return [];
    if (summary) call.args = summary;
    if (args.raw !== undefined) call.raw = args.raw;
    return this.render(this.groupOf.get(call.id) ?? '');
  }

  toolFinished(
    toolName: string,
    label: string,
    status: string | undefined,
    toolCallId?: string,
    result: SlackToolResult = {},
  ): SlackTaskChunk[] {
    const key = `tool:${toolName}`;
    let call = this.findCall(toolName, toolCallId);
    if (!call) {
      // A result with no start: show it as its own call.
      this.toolStarted(toolName, label, toolCallId);
      call = this.findCall(
        toolName,
        toolCallId ?? (this.openIdsByKey.get(key) ?? []).slice(-1)[0],
      );
    }
    if (!call) return [];
    const callId = call.id;
    this.openIdsByKey.set(
      key,
      (this.openIdsByKey.get(key) ?? []).filter((id) => id !== callId),
    );
    call.label = label;
    call.status = asCallStatus(status);
    call.progress = undefined;
    const summary = result.summary?.trim();
    if (summary) call.result = summary;
    call.sources = viewSources(result.view);
    return this.render(this.groupOf.get(callId) ?? '');
  }

  /** What a running tool's server says about its progress. Empty when nothing changed. */
  toolProgress(toolName: string, progress: string): SlackTaskChunk[] {
    const id = this.openIdsByKey.get(`tool:${toolName}`)?.[0];
    const call = id ? this.findCall(toolName, id) : undefined;
    const text = progress.trim();
    if (!call || call.status !== 'running' || !text || call.progress === text) {
      return [];
    }
    call.progress = text;
    return this.render(this.groupOf.get(call.id) ?? '');
  }

  /** A sub-agent's row: its own calls are listed under it, like the dashboard's nested trail. */
  subAgent(
    role: string,
    phase: 'started' | 'completed' | 'failed',
  ): SlackTaskChunk[] {
    this.endGroup();
    let group = this.agentGroups.get(role);
    if (phase === 'started' || group === undefined) {
      group = this.takePlaceholder() ?? `agent-${String(++this.seq)}`;
      this.agentGroups.set(role, group);
      this.groups.set(group, { calls: [], agent: { role, status: 'running' } });
    }
    const entry = this.groups.get(group);
    if (entry?.agent && phase !== 'started') {
      entry.agent.status = phase;
      entry.closed = true;
    }
    const rows = this.render(group);
    if (phase !== 'started') this.agentGroups.delete(role);
    return rows;
  }

  /**
   * The rows among `ids` as the reply stops: every row is sealed with its
   * details and output, and none keeps spinning. A wait for approval ends as
   * said, not failed.
   */
  failRunning(ids: ReadonlySet<string>): SlackTaskChunk[] {
    // The reply ends: a "Thinking…" or "Writing the answer" row ends with it.
    const live = [this.placeholder, this.writing].filter(
      (id): id is string => id !== null && ids.has(id),
    );
    this.placeholder = null;
    this.writing = null;
    const rows: SlackTaskChunk[] = live.map((id) =>
      this.put({
        type: 'task_update',
        id,
        title: WRITING_TITLE,
        status: 'complete',
      }),
    );
    for (const [id, group] of this.groups) {
      if (!ids.has(id)) continue;
      group.closed = true;
      if (!group.sealed || this.tasks.get(id)?.status === 'in_progress') {
        rows.push(...this.render(id, true));
      }
    }
    for (const task of this.tasks.values()) {
      if (
        task.status !== 'in_progress' ||
        !ids.has(task.id) ||
        this.groups.has(task.id)
      ) {
        continue;
      }
      rows.push(
        this.put({
          type: 'task_update',
          id: task.id,
          title: task.title,
          status: 'error',
        }),
      );
    }
    return rows;
  }

  /** A tool or sub-agent row that hasn't sent its details and output yet. */
  isOpen(id: string): boolean {
    const group = this.groups.get(id);
    return this.isLiveRow(id) || (group !== undefined && group.sealed !== true);
  }

  /** A row's latest state, to show it again in a replaced message. */
  current(id: string): SlackTaskChunk | undefined {
    return this.tasks.get(id);
  }

  private findCall(
    toolName: string,
    toolCallId?: string,
  ): ToolCall | undefined {
    const id = toolCallId ?? this.openIdsByKey.get(`tool:${toolName}`)?.[0];
    if (id === undefined) return undefined;
    const group = this.groupOf.get(id);
    return group
      ? this.groups.get(group)?.calls.find((call) => call.id === id)
      : undefined;
  }

  /**
   * One call as one line: mark, its main argument and how it ended, without
   * repeating the tool's name, which the row's title already says.
   */
  private callLine(call: ToolCall, ownLabel: string | undefined): string {
    const failed = call.status === 'failed' || call.status === 'blocked';
    const mark =
      call.status === 'running' || call.status === 'awaiting_approval'
        ? '•'
        : failed
          ? '✗'
          : '✓';
    let argument = mainArgument(
      call.args ?? (call.raw ? compactArgs(call.raw) : ''),
      call.label,
    );
    // Loading an app's tools names the app, not its internal id ("mcp_tracker").
    if (TOOL_LOADERS.has(call.toolName) && /^"[\w.-]+"$/.test(argument)) {
      argument = appName(argument.slice(1, -1));
    }
    const reason = firstSentence(call.result ?? '')
      .replace(/^Failed:\s*/i, '')
      // A tool's raw name ("Create_ticket") reads as words.
      .replace(/\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b/g, (name) =>
        name.replace(/_/g, ' '),
      );
    const outcome = capitalize(
      call.status === 'awaiting_approval'
        ? APPROVAL_OUTPUT
        : call.status === 'blocked'
          ? reason === '' || APPROVAL_HOLD.test(reason)
            ? BLOCKED_OUTPUT
            : reason
          : call.status === 'running'
            ? (call.progress ?? '')
            : failed && !reason
              ? 'Failed'
              : reason,
    );
    const chip = call.approvalAnswer
      ? APPROVAL_CHIPS[call.approvalAnswer]
      : undefined;
    // With nothing else to say, a line names its call rather than showing a bare mark.
    const what =
      [ownLabel, argument].filter(Boolean).join(': ') ||
      (outcome !== '' ? '' : rowLabel(call.toolName, call.label).title);
    const shown = outcome
      ? `${what ? ' → ' : ''}${clip(shortIds(redactSecrets(outcome)), MAX_OUTCOME_CHARS)}`
      : '';
    return `${mark} ${what}${shown}${chip ? ` · ${chip}` : ''}`;
  }

  /**
   * One row for a group of calls (or a sub-agent). Its title (the
   * dashboard's group label) and status may change at any time. Its details,
   * output and sources go out once, when the row is sealed: Slack adds a
   * resent value to what it shows, so a value sent twice would show twice.
   */
  private render(group: string, force = false): SlackTaskChunk[] {
    const entry = this.groups.get(group);
    if (!entry) return [];
    const { calls, agent } = entry;
    const last = calls[calls.length - 1];
    const label = last ? rowLabel(last.toolName, last.label) : undefined;
    // The app the calls belong to; "MCP" names no app, so it isn't shown.
    const toolset = groupToolsetLabel(calls.map((call) => call.toolName));
    const tag = agent
      ? undefined
      : (label?.app ?? (toolset === 'MCP' ? undefined : toolset));
    // What was done, as a plain action: one kind of call per row.
    const title = clip(
      agent ? agent.role : (label?.title ?? ''),
      MAX_TASK_TITLE_CHARS,
    );

    const running = calls.filter((call) => call.status === 'running').length;
    const failedCount = calls.filter(
      (call) => call.status === 'failed' || call.status === 'blocked',
    ).length;
    const awaiting = calls.some((call) => call.status === 'awaiting_approval');
    // An error row only when nothing in it worked; a partial failure shows on its line.
    let status: SlackTaskStatus =
      agent?.status === 'running' || (!agent && running > 0) || awaiting
        ? 'in_progress'
        : agent?.status === 'failed' ||
            (calls.length > 0 && failedCount === calls.length)
          ? 'error'
          : 'complete';
    let shownTitle = title;
    if (force && status === 'in_progress') {
      // The reply ends here: a wait for approval says so; anything else stopped short.
      if (awaiting && running === 0) {
        status = 'complete';
        shownTitle = clip(`${APPROVAL_OUTPUT}: ${title}`, MAX_TASK_TITLE_CHARS);
      } else {
        status = 'error';
      }
    }
    if (awaiting && running === 0) this.waiting.add(group);
    else this.waiting.delete(group);

    const seal =
      !entry.sealed &&
      (force || (entry.closed === true && running === 0 && !awaiting));
    if (!seal) {
      return [
        this.put({ type: 'task_update', id: group, title: shownTitle, status }),
      ];
    }
    entry.sealed = true;

    // A sub-agent's calls can be of several kinds, so each line names its tool.
    const lines = calls.map((call) =>
      this.callLine(
        call,
        agent ? rowLabel(call.toolName, call.label).title : undefined,
      ),
    );
    const shownLines =
      lines.length > MAX_OUTPUT_LINES
        ? [
            ...lines.slice(0, MAX_OUTPUT_LINES),
            `+${String(lines.length - MAX_OUTPUT_LINES)} more`,
          ]
        : lines;
    const sources = calls
      .flatMap((call) => call.sources)
      .slice(0, MAX_TASK_SOURCES);

    return [
      this.put({
        type: 'task_update',
        id: group,
        title: shownTitle,
        status,
        ...(tag ? { details: clip(tag, MAX_TASK_DETAILS_CHARS) } : {}),
        ...(shownLines.length > 0
          ? { output: clip(shownLines.join('\n'), MAX_TASK_OUTPUT_CHARS) }
          : {}),
        ...(sources.length > 0 ? { sources } : {}),
      }),
    ];
  }

  private put(task: SlackTaskChunk): SlackTaskChunk {
    // A later title or status update keeps what the row showed, for a replay in a new message.
    const { details, output, sources } = this.tasks.get(task.id) ?? {};
    this.tasks.set(task.id, {
      ...(details !== undefined ? { details } : {}),
      ...(output !== undefined ? { output } : {}),
      ...(sources !== undefined ? { sources } : {}),
      ...task,
    });
    return task;
  }
}

/**
 * One streamed reply: the steps in Slack's collapsible "Thinking" plan, the
 * answer streaming below it as markdown, then any blocks (sources). It opens
 * on the first step or text, so a run with no step shows no plan. Every call
 * uses `chunks`: Slack rejects a stream that mixes them with top-level
 * `markdown_text`.
 */
export class SlackPlanStream {
  /**
   * Least time between two calls on the stream. appendStream is Tier 4
   * (100+ a minute), so a reply stays near 1.5 calls a second.
   */
  static minIntervalMs = 650;
  /** Quiet time after a step before a "Thinking…" row shows the agent is still working. */
  static gapMs = 2000;
  /** A row in progress this long with nothing new shows how long it has taken… */
  static tickerAfterMs = 10_000;
  /** …refreshed this often (one update, within the stream's rate). */
  static tickerEveryMs = 5000;

  ts: string | null = null;
  private lastCallAt = 0;
  /** Slack refused the first message: the caller posts the answer instead. */
  unavailable = false;
  private stopped = false;
  private chars = 0;
  private text = '';
  private everOpened = false;
  private earlier: string[] = [];
  private taskIds = new Set<string>();
  /** Rows of this message whose details, output and sources were sent: Slack adds a resent value to the shown one. */
  private detailed = new Set<string>();
  /** Rows still open when their message ended: they go on in the next message, sealed there once. */
  private moving: string[] = [];
  private liveness: ReturnType<typeof setInterval> | null = null;
  private lastEventAt = Date.now();
  private lastTickAt = 0;
  private inFlight = 0;
  /** When each row went in progress, and its title without the elapsed time. */
  private progressSince = new Map<string, number>();
  private baseTitles = new Map<string, string>();
  /** Each message's text and steps in order (steps by id), to show them again if part is taken back. */
  private logs: ({ text: string } | { task: string })[][] = [];
  private afterTask = false;
  /** Where the current model call's text began: message index and position in its log. */
  private segment = { message: 0, logLength: 0 };

  constructor(
    private readonly client: TypedSlackClient,
    private readonly channel: string,
    private readonly threadTs: string,
    private readonly recipient: Record<string, unknown>,
    readonly plan: SlackTaskPlan,
  ) {}

  async tasks(all: SlackTaskChunk[]): Promise<void> {
    this.lastEventAt = Date.now();
    // A row settled in an earlier message stays there: sent here it would show again.
    const earlier = this.rowsOfEarlierMessages();
    const chunks = all.filter(
      (chunk) =>
        !earlier.has(chunk.id) ||
        this.taskIds.has(chunk.id) ||
        this.moving.includes(chunk.id),
    );
    if (this.unavailable || this.stopped || chunks.length === 0) return;
    if (!this.ts) {
      await this.openWith(chunks);
      return;
    }
    try {
      await this.append(chunks);
      this.track(chunks);
    } catch (error) {
      const code = slackErrorCode(error);
      if (code === 'msg_blocks_too_long' || code === 'msg_too_long') {
        await this.retrySmaller(chunks);
        return;
      }
      if (code !== 'message_not_in_streaming_state') {
        console.error('Error updating Slack steps:', error);
        return;
      }
      // A stream lasts ten minutes; the steps go on in a new message.
      this.retire();
      await this.openWith(chunks);
    }
  }

  /**
   * Slack found the rows too big: each row's output shrinks to its first
   * lines, then, if that still fails, the rows go without their output. No
   * row is added and none is lost.
   */
  private async retrySmaller(chunks: SlackTaskChunk[]): Promise<void> {
    const shorter = chunks.map((chunk): SlackTaskChunk => {
      const lines = (chunk.output ?? '').split('\n');
      if (lines.length < 2) {
        return chunk.output
          ? {
              ...chunk,
              output: clip(chunk.output, Math.floor(chunk.output.length / 2)),
            }
          : chunk;
      }
      // A row already summing up its last calls counts them in the new sum.
      const summed = /^\+(\d+) more$/.exec(lines[lines.length - 1] ?? '');
      const callLines = summed ? lines.slice(0, -1) : lines;
      const keep = Math.max(1, Math.floor(callLines.length / 2));
      const more = callLines.length - keep + Number(summed?.[1] ?? 0);
      return {
        ...chunk,
        output: [...callLines.slice(0, keep), `+${String(more)} more`].join(
          '\n',
        ),
      };
    });
    for (const attempt of [
      shorter,
      chunks.map(({ output: _output, ...row }) => row),
    ]) {
      try {
        await this.append(attempt);
        this.track(attempt);
        return;
      } catch (error) {
        const code = slackErrorCode(error);
        if (code !== 'msg_blocks_too_long' && code !== 'msg_too_long') {
          console.error('Error updating Slack steps:', error);
          return;
        }
      }
    }
    console.error('Slack refused the steps even without their output');
  }

  /**
   * Appends markdown, moving to a new message when one is full. Text after a
   * step, or with `paragraph`, starts a new paragraph; pieces of the same
   * text join as they are.
   */
  async answer(
    markdown: string,
    options: { paragraph?: boolean } = {},
  ): Promise<void> {
    if (markdown === '') return;
    this.lastEventAt = Date.now();
    let rest = markdown;
    let first = true;
    while (rest.length > 0 && !this.stopped && !this.unavailable) {
      const wantsBreak =
        first && (options.paragraph === true || this.afterTask);
      const gap =
        !wantsBreak ||
        this.ts === null ||
        this.text === '' ||
        this.text.endsWith('\n\n')
          ? ''
          : this.text.endsWith('\n')
            ? '\n'
            : '\n\n';
      const room =
        SLACK_STREAM_MESSAGE_CHAR_LIMIT -
        FENCE_ROOM -
        gap.length -
        (this.ts ? this.chars : 0);
      if (this.ts && room <= 0) {
        await this.rollover();
        continue;
      }
      let piece = rest;
      if (rest.length > room) {
        const newline = rest.slice(0, room).lastIndexOf('\n');
        piece = rest.slice(0, newline > 0 ? newline + 1 : room);
      }
      const sent = gap + piece;
      const chunk: SlackStreamChunk = { type: 'markdown_text', text: sent };
      if (!this.ts) {
        if (!(await this.openWith([chunk]))) return;
      } else {
        try {
          await this.append([chunk]);
          this.track([chunk]);
        } catch (error) {
          if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
            throw error;
          }
          this.retire();
          continue;
        }
      }
      first = false;
      rest = rest.slice(piece.length);
    }
  }

  /**
   * Finishes the reply. False when the text or blocks could not go on the
   * stream (none open, Slack refused, or it had expired): the caller posts them.
   */
  async stop(
    options: { markdown?: string; blocks?: SlackBlock[] } = {},
  ): Promise<boolean> {
    const nothingToDeliver = !options.markdown && !options.blocks?.length;
    this.stopLiveness();
    if (!this.ts || this.stopped || this.unavailable) return nothingToDeliver;
    const settled = this.prepare(this.plan.failRunning(this.taskIds));
    const chunks: SlackStreamChunk[] = [...settled];
    if (options.markdown) {
      chunks.push({ type: 'markdown_text', text: options.markdown });
    }
    try {
      await this.client.apiCall('chat.stopStream', {
        channel: this.channel,
        ts: this.ts,
        ...(chunks.length > 0 ? { chunks } : {}),
        ...(options.blocks && options.blocks.length > 0
          ? { blocks: options.blocks }
          : {}),
      });
      this.stopped = true;
      return true;
    } catch (error) {
      this.stopped = true;
      if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
        console.error('Error stopping Slack stream:', error);
        return false;
      }
      return nothingToDeliver;
    }
  }

  /** The next answer text belongs to a new model call; only its text can be taken back. */
  markSegment(): void {
    this.segment = {
      message: this.earlier.length,
      logLength: this.ts ? this.currentLog().length : 0,
    };
  }

  /**
   * The backend dropped the current model call and will retry it: take its
   * text back. Its messages are deleted and the reply reopens with what came
   * before that call (text and steps, steps as they are now), so only the
   * retry follows.
   */
  async dropSegment(): Promise<void> {
    const all = [...this.earlier, ...(this.ts ? [this.ts] : [])];
    const affected = all.slice(this.segment.message);
    const segmentLog = this.logs[this.segment.message] ?? [];
    const keptLog = segmentLog.slice(0, this.segment.logLength);
    const shown =
      affected.length > 1 ||
      (affected.length === 1 && segmentLog.length > this.segment.logLength);
    if (!shown || this.unavailable) return;
    if (this.ts && !this.stopped) {
      try {
        await this.client.apiCall('chat.stopStream', {
          channel: this.channel,
          ts: this.ts,
        });
      } catch (error) {
        if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
          console.error('Error stopping Slack stream before delete:', error);
        }
      }
    }
    for (const ts of affected) {
      await this.deleteMessage(ts);
    }
    // Only text is taken back: every step of the deleted messages comes back, as it stands now.
    const keptTasks = new Set(
      keptLog.flatMap((entry) => ('task' in entry ? [entry.task] : [])),
    );
    const laterTasks = this.logs
      .slice(this.segment.message)
      .flat()
      .flatMap((entry) => ('task' in entry ? [entry.task] : []))
      .filter((id) => !keptTasks.has(id));
    const replay: SlackStreamChunk[] = [
      ...keptLog,
      ...[...new Set(laterTasks)].map((task) => ({ task })),
    ].flatMap((entry): SlackStreamChunk[] => {
      if ('text' in entry) return [{ type: 'markdown_text', text: entry.text }];
      const task = this.plan.current(entry.task);
      return task ? [task] : [];
    });
    this.earlier = this.earlier.slice(0, this.segment.message);
    this.logs = this.logs.slice(0, this.segment.message);
    this.ts = null;
    this.stopped = false;
    this.text = '';
    if (replay.length > 0) await this.openWith(replay);
    this.markSegment();
  }

  /** Deletes every message of this reply (e.g. the run asks a question instead). */
  async discard(): Promise<void> {
    this.stopLiveness();
    const all = [...this.earlier, ...(this.ts ? [this.ts] : [])];
    if (this.ts && !this.stopped) {
      try {
        await this.client.apiCall('chat.stopStream', {
          channel: this.channel,
          ts: this.ts,
        });
      } catch (error) {
        if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
          console.error('Error stopping Slack stream before delete:', error);
        }
      }
    }
    for (const ts of all) {
      await this.deleteMessage(ts);
    }
    this.ts = null;
    this.stopped = true;
    this.earlier = [];
  }

  private async deleteMessage(ts: string): Promise<void> {
    try {
      await this.client.apiCall('chat.delete', { channel: this.channel, ts });
    } catch (error) {
      if (slackErrorCode(error) !== 'message_not_found') {
        console.error('Error deleting Slack stream message:', error);
      }
    }
  }

  /** Opens a message in plan mode. False (and unavailable) when Slack refuses the first one. */
  private async openWith(fresh: SlackStreamChunk[]): Promise<boolean> {
    const carried = this.moving
      .filter(
        (id) =>
          !fresh.some(
            (chunk) => chunk.type === 'task_update' && chunk.id === id,
          ),
      )
      .flatMap((id) => {
        const task = this.plan.current(id);
        return task ? [task] : [];
      });
    this.moving = [];
    const chunks = [...carried, ...fresh];
    const reopenFence = openFence(this.text);
    // The reply's first message titles its plan once it has a step.
    const titled: SlackStreamChunk[] =
      this.earlier.length === 0 &&
      chunks.some((chunk) => chunk.type === 'task_update') &&
      chunks[0]?.type !== 'plan_update'
        ? [{ type: 'plan_update', title: SLACK_PLAN_TITLE }, ...chunks]
        : chunks;
    const opening: SlackStreamChunk[] =
      reopenFence && titled[0]?.type === 'markdown_text'
        ? [
            { type: 'markdown_text', text: `${reopenFence.opener}\n` },
            ...titled,
          ]
        : titled;
    this.inFlight += 1;
    try {
      await this.pace();
      const result = await this.client.apiCall('chat.startStream', {
        channel: this.channel,
        thread_ts: this.threadTs,
        chunks: opening,
        // Every message, so a step that arrives after a rollover joins a plan, not a stray card.
        task_display_mode: 'plan',
        ...NO_UNFURL_OPTIONS,
        ...this.recipient,
      });
      if (typeof result.ts !== 'string' || !result.ts) {
        throw new Error('Failed to start Slack stream');
      }
      this.ts = result.ts;
    } catch (error) {
      if (this.everOpened) throw error;
      console.error('Error starting Slack stream:', error);
      this.unavailable = true;
      return false;
    } finally {
      this.inFlight -= 1;
    }
    this.everOpened = true;
    this.startLiveness();
    this.chars = 0;
    this.text = '';
    this.logs[this.earlier.length] = [];
    this.taskIds = new Set();
    this.detailed = new Set();
    this.track(opening);
    return true;
  }

  private rowsOfEarlierMessages(): Set<string> {
    return new Set(
      this.logs
        .slice(0, this.earlier.length)
        .flat()
        .flatMap((entry) => ('task' in entry ? [entry.task] : [])),
    );
  }

  private currentLog(): ({ text: string } | { task: string })[] {
    const index = this.earlier.length;
    this.logs[index] ??= [];
    return this.logs[index];
  }

  private track(chunks: SlackStreamChunk[], ticker = false): void {
    for (const chunk of chunks) {
      if (chunk.type === 'plan_update') continue;
      if (chunk.type === 'task_update') {
        if (!ticker) {
          this.baseTitles.set(chunk.id, chunk.title);
          if (chunk.status !== 'in_progress') {
            this.progressSince.delete(chunk.id);
          } else if (!this.progressSince.has(chunk.id)) {
            this.progressSince.set(chunk.id, Date.now());
          }
        }
        if (
          chunk.details !== undefined ||
          chunk.output !== undefined ||
          chunk.sources !== undefined
        ) {
          this.detailed.add(chunk.id);
        }
        if (!this.taskIds.has(chunk.id))
          this.currentLog().push({ task: chunk.id });
        this.taskIds.add(chunk.id);
        this.afterTask = true;
      } else {
        this.currentLog().push({ text: chunk.text });
        this.chars += chunk.text.length;
        this.text += chunk.text;
        this.afterTask = false;
      }
    }
  }

  private async pace(): Promise<void> {
    const wait = this.lastCallAt + SlackPlanStream.minIntervalMs - Date.now();
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
    this.lastCallAt = Date.now();
  }

  private async append(chunks: SlackStreamChunk[]): Promise<void> {
    this.inFlight += 1;
    try {
      await this.pace();
      await this.client.apiCall('chat.appendStream', {
        channel: this.channel,
        ts: this.ts,
        chunks: this.prepare(chunks),
      });
    } finally {
      this.inFlight -= 1;
    }
  }

  /**
   * Keeps a quiet reply visibly alive: after a step, a "Thinking…" row;
   * on a row in progress for a while, its elapsed time in the title. Titles
   * and statuses replace in Slack, so nothing piles up.
   */
  private async pulse(): Promise<void> {
    if (!this.ts || this.stopped || this.unavailable || this.inFlight > 0) {
      return;
    }
    const now = Date.now();
    const opened =
      now - this.lastEventAt >= SlackPlanStream.gapMs
        ? this.plan.thinking()
        : [];
    const refreshed: SlackTaskChunk[] = [];
    if (now - this.lastTickAt >= SlackPlanStream.tickerEveryMs) {
      for (const [id, since] of this.progressSince) {
        const quiet = now - Math.max(since, this.lastEventAt);
        if (!this.taskIds.has(id) || quiet < SlackPlanStream.tickerAfterMs) {
          continue;
        }
        const base = this.baseTitles.get(id) ?? '';
        const seconds = `${String(Math.floor((now - since) / 1000))}s`;
        refreshed.push({
          type: 'task_update',
          id,
          title: clip(
            base.endsWith('…') ? `${base} ${seconds}` : `${base} · ${seconds}`,
            MAX_TASK_TITLE_CHARS,
          ),
          status: 'in_progress',
        });
      }
      if (refreshed.length > 0) this.lastTickAt = now;
    }
    if (opened.length + refreshed.length === 0) return;
    try {
      await this.append([...opened, ...refreshed]);
      this.track(opened);
      this.track(refreshed, true);
    } catch (error) {
      // The stream ended (expired, or an error): the next real update deals with it.
      this.stopLiveness();
      if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
        console.error('Error refreshing Slack steps:', error);
      }
    }
  }

  private startLiveness(): void {
    if (this.liveness) return;
    this.liveness = setInterval(() => {
      void this.pulse();
    }, 1000);
    this.liveness.unref?.();
  }

  private stopLiveness(): void {
    if (this.liveness) clearInterval(this.liveness);
    this.liveness = null;
  }

  /** A row's details, output and sources reach a message once: later sends carry only its title and status. */
  private prepare<T extends SlackStreamChunk>(chunks: T[]): T[] {
    return chunks.map((chunk) => {
      if (chunk.type !== 'task_update' || !this.detailed.has(chunk.id)) {
        return chunk;
      }
      const { details: _d, output: _o, sources: _s, ...row } = chunk;
      return row as T;
    });
  }

  /** The current message ended: keep it, and continue in a new one with its open rows. */
  private retire(): void {
    this.moving = this.openRows();
    if (this.ts) this.earlier.push(this.ts);
    this.ts = null;
  }

  /** Rows of this message whose details and output haven't gone out yet. */
  private openRows(): string[] {
    return [...this.taskIds].filter((id) => this.plan.isOpen(id));
  }

  private async rollover(): Promise<void> {
    if (this.ts) {
      // A code fence left open would swallow the rest of this message's text.
      const fence = openFence(this.text);
      // A row that goes on in the next message ends here by title alone, not spinning.
      const settled: SlackStreamChunk[] = this.openRows().flatMap((id) => {
        const task = this.plan.current(id);
        return task
          ? [{ type: 'task_update', id, title: task.title, status: 'complete' }]
          : [];
      });
      const close: SlackStreamChunk[] = [
        ...settled,
        ...(fence
          ? [{ type: 'markdown_text' as const, text: `\n${fence.marker}\n` }]
          : []),
      ];
      try {
        await this.client.apiCall('chat.stopStream', {
          channel: this.channel,
          ts: this.ts,
          ...(close.length > 0 ? { chunks: close } : {}),
        });
      } catch (error) {
        if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
          throw error;
        }
      }
    }
    this.retire();
  }
}
