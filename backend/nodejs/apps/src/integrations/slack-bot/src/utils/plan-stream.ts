import {
  NO_UNFURL_OPTIONS,
  SLACK_STREAM_MESSAGE_CHAR_LIMIT,
  type SlackBlock,
  type TypedSlackClient,
} from '../helpers';
import {
  extractToolsetLabel,
  groupToolsetLabel,
  isWebTool,
  toolGroupLabel,
} from './tool-display';

/** Title of the plan block that holds the steps. */
export const SLACK_PLAN_TITLE = 'Thinking';
/** Task and plan chunks are reported to allow 256 characters; narration rows stay under it. */
export const MAX_TASK_TITLE_CHARS = 250;
/** A task's `details` is documented by the Agents SDK as at most 256 characters: one summary line. */
export const MAX_TASK_DETAILS_CHARS = 256;
/**
 * Room for one row's per-call breakdown in `output`. Slack publishes no
 * threshold for msg_blocks_too_long, so rows stay small and a longer list
 * continues in a "(continued)" row: no call is dropped or cut in half.
 */
export const MAX_TASK_OUTPUT_CHARS = 2500;
const MAX_REASONING_CHARS = 2500;
const MAX_TASK_SOURCES = 10;
const APPROVAL_OUTPUT = 'Waiting for your approval';
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
/** `"key": "value with spaces"` / `key='…'`: masked to the end of the quoted value. */
const QUOTED_VALUE = new RegExp(
  String.raw`(["']?)\b(${KEY_NAME})\1(\s*[:=]\s*)(["'])(?:\\.|(?!\4)[^\\])*\4`,
  'g',
);
/** `?key=…&` in a URL: masked to the next parameter. */
const QUERY_VALUE = new RegExp(
  String.raw`([?&;])(${KEY_NAME})=([^&#\s]*)`,
  'g',
);
/** `key: value` / `key=value` unquoted. */
const PLAIN_KEY = new RegExp(String.raw`\b(${KEY_NAME})(\s*[:=]\s*)`, 'g');
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
    if (!link || seen.has(link.href)) continue;
    seen.add(link.href);
    const title = typeof text === 'string' ? text.trim() : '';
    sources.push({
      type: 'url',
      url: link.href,
      text: clip(
        title === '' ? link.hostname.replace(/^www\./, '') : title,
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
}

/**
 * Turns agent activity into rows of Slack's plan the way the dashboard's
 * thinking trail shows it: the agent's text as rows of its own, tool calls
 * with no text between them in one row ("Explored 3 searches", "Ran 2
 * tools"), each sub-agent in a row with its own calls, and reasoning in a row
 * of its own. A row's details is a one-line summary; its output lists every
 * call with the server's Arguments and Result summaries.
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
  private reasoning = '';
  private finishedReasoning = '';
  private seq = 0;

  /** Text came between steps: the next tool call starts a new row. */
  endGroup(): void {
    this.openGroup = null;
  }

  /** What the agent wrote before a step, as its own rows in the plan, in full. */
  narration(text: string): SlackTaskChunk[] {
    this.endGroup();
    return splitIntoRows(text).map((title) =>
      this.put({
        type: 'task_update',
        id: `note-${String(++this.seq)}`,
        title,
        status: 'complete',
      }),
    );
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
    const text = clip(this.finishedReasoning, MAX_REASONING_CHARS);
    this.finishedReasoning = '';
    if (!text) return null;
    this.endGroup();
    return this.put({
      type: 'task_update',
      id: `reasoning-${String(++this.seq)}`,
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
    this.openIdsByKey.set(key, [...(this.openIdsByKey.get(key) ?? []), id]);
    const agentGroup = meta.agent
      ? this.agentGroups.get(meta.agent)
      : undefined;
    let group = agentGroup;
    if (group === undefined) {
      if (this.openGroup === null) {
        this.openGroup = `tools-${String(++this.seq)}`;
        this.groups.set(this.openGroup, { calls: [] });
      }
      group = this.openGroup;
    }
    this.groups.get(group)?.calls.push({
      id,
      toolName,
      label,
      status: 'running',
      sources: [],
      ...(meta.approvalAnswer ? { approvalAnswer: meta.approvalAnswer } : {}),
    });
    this.groupOf.set(id, group);
    return this.render(group);
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
      group = `agent-${String(++this.seq)}`;
      this.agentGroups.set(role, group);
      this.groups.set(group, { calls: [], agent: { role, status: 'running' } });
    }
    const entry = this.groups.get(group);
    if (entry?.agent && phase !== 'started') entry.agent.status = phase;
    const rows = this.render(group);
    if (phase !== 'started') this.agentGroups.delete(role);
    return rows;
  }

  /** Running rows among `ids` when the reply stops: Slack would leave them spinning. A wait for approval ends as said, not failed. */
  failRunning(ids: ReadonlySet<string>): SlackTaskChunk[] {
    const failed: SlackTaskChunk[] = [];
    for (const task of this.tasks.values()) {
      if (task.status !== 'in_progress' || !ids.has(task.id)) continue;
      if (this.waiting.has(task.id)) {
        // Nothing more happens in this reply: no spinner left behind, and it says what it waits for.
        failed.push(
          this.put({
            ...task,
            status: 'complete',
            title: `${APPROVAL_OUTPUT}: ${task.title}`,
          }),
        );
      } else {
        failed.push(this.put({ ...task, status: 'error' }));
      }
    }
    return failed;
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

  /** One call's lines, the way the dashboard's expanded call shows them. */
  private callLines(call: ToolCall, heading: string | null): string[] {
    const lines: string[] = [];
    const failed = call.status === 'failed' || call.status === 'blocked';
    if (heading !== null) {
      const mark =
        call.status === 'running' || call.status === 'awaiting_approval'
          ? '•'
          : failed
            ? '✗'
            : '✓';
      const chip = call.approvalAnswer
        ? APPROVAL_CHIPS[call.approvalAnswer]
        : call.status === 'awaiting_approval'
          ? 'Needs approval'
          : undefined;
      lines.push(`${mark} ${heading}${chip ? ` · ${chip}` : ''}`);
    }
    const args = call.args ?? (call.raw ? compactArgs(call.raw) : '');
    if (args) lines.push(`Arguments: ${redactSecrets(args)}`);
    const query = sqlQuery(call.toolName, call.raw);
    if (query) lines.push(`Query: ${redactSecrets(query)}`);
    if (call.status === 'running') {
      if (call.progress)
        lines.push(`Progress: ${redactSecrets(call.progress)}`);
    } else if (call.status === 'awaiting_approval') {
      // A lone call already says it in its summary line.
      if (heading !== null) lines.push(`Result: ${APPROVAL_OUTPUT}`);
    } else if (call.status === 'blocked') {
      lines.push(
        call.result ? `Blocked: ${redactSecrets(call.result)}` : 'Blocked',
      );
    } else if (call.status === 'failed') {
      const reason = call.result?.replace(/^Failed:\s*/i, '');
      lines.push(
        reason ? `Result: Failed: ${redactSecrets(reason)}` : 'Result: Failed',
      );
    } else if (call.result) {
      lines.push(`Result: ${redactSecrets(call.result)}`);
    }
    // A line per field, no leading spaces: Slack could show indented lines as code.
    return lines;
  }

  /** One row for a group of calls (or a sub-agent), titled and listed like the dashboard's. */
  private render(group: string): SlackTaskChunk[] {
    const entry = this.groups.get(group);
    if (!entry) return [];
    const { calls, agent } = entry;
    const tag = agent
      ? undefined
      : groupToolsetLabel(calls.map((call) => call.toolName));
    const single = !agent && calls.length === 1 ? calls[0] : undefined;
    const label = agent
      ? agent.role
      : single
        ? single.label
        : toolGroupLabel(calls.map((call) => call.toolName));

    const running = calls.filter((call) => call.status === 'running').length;
    const failedCount = calls.filter(
      (call) => call.status === 'failed' || call.status === 'blocked',
    ).length;
    const awaiting = calls.some((call) => call.status === 'awaiting_approval');
    const status: SlackTaskStatus =
      agent?.status === 'running' || (!agent && running > 0) || awaiting
        ? 'in_progress'
        : agent?.status === 'failed' || failedCount > 0
          ? 'error'
          : 'complete';
    if (awaiting && running === 0) this.waiting.add(group);
    else this.waiting.delete(group);

    // One summary line; progress counts only while something runs.
    const summary = [
      calls.length > 1 || agent
        ? `${String(calls.length)} ${calls.length === 1 ? 'call' : 'calls'}`
        : '',
      running > 0 && (calls.length > 1 || agent)
        ? `${String(running)} running`
        : '',
      failedCount > 0 ? `${String(failedCount)} failed` : '',
      awaiting ? APPROVAL_OUTPUT : '',
    ]
      .filter(Boolean)
      .join(' · ');

    // Every call is listed, however many; a call never splits across rows.
    const blocks = single
      ? [this.callLines(single, null).join('\n')]
      : calls.map((call) => {
          // A call's own app, unless the row's title already names it.
          const own = extractToolsetLabel(call.toolName);
          const callTag = own === tag ? undefined : own;
          const fetched =
            isWebTool(call.toolName) &&
            !/search/i.test(call.toolName) &&
            call.sources[0]
              ? ` (${new URL(call.sources[0].url).hostname})`
              : '';
          return this.callLines(
            call,
            `${callTag ? `${callTag}: ` : ''}${call.label}${fetched}`,
          ).join('\n');
        });
    const pages: string[] = [];
    for (const block of blocks.filter((b) => b !== '')) {
      const last = pages.length - 1;
      const page = pages[last];
      if (
        page !== undefined &&
        page.length + 2 + block.length <= MAX_TASK_OUTPUT_CHARS
      ) {
        pages[last] = `${page}\n\n${block}`;
      } else {
        pages.push(clip(block, MAX_TASK_OUTPUT_CHARS));
      }
    }
    const sources = calls
      .flatMap((call) => call.sources)
      .slice(0, MAX_TASK_SOURCES);
    const title = tag ? `${tag}: ${label}` : label;

    return [
      this.put({
        type: 'task_update',
        id: group,
        title,
        status,
        ...(summary ? { details: clip(summary, MAX_TASK_DETAILS_CHARS) } : {}),
        ...(pages[0] ? { output: pages[0] } : {}),
        ...(sources.length > 0 ? { sources } : {}),
      }),
      ...pages.slice(1).map((output, index) => {
        const id = `${group}-more-${String(index + 1)}`;
        if (status === 'in_progress' && awaiting) this.waiting.add(id);
        return this.put({
          type: 'task_update',
          id,
          title: `${title} (continued)`,
          status,
          output,
        });
      }),
    ];
  }

  private put(task: SlackTaskChunk): SlackTaskChunk {
    this.tasks.set(task.id, task);
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

  async tasks(chunks: SlackTaskChunk[]): Promise<void> {
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
   * Slack found the rows too big: each row's output moves half into a
   * "(continued)" row, then, if that still fails, the rows go without their
   * output. A row is never lost.
   */
  private async retrySmaller(chunks: SlackTaskChunk[]): Promise<void> {
    const halves = chunks.flatMap((chunk): SlackTaskChunk[] => {
      const output = chunk.output ?? '';
      if (output.length < 2) return [chunk];
      const middle = output.lastIndexOf('\n\n', Math.floor(output.length / 2));
      const cut = middle > 0 ? middle : Math.floor(output.length / 2);
      return [
        { ...chunk, output: output.slice(0, cut).trim() },
        {
          type: 'task_update',
          id: `${chunk.id}-overflow`,
          title: `${chunk.title} (continued)`,
          status: chunk.status,
          output: output.slice(cut).trim(),
        },
      ];
    });
    for (const attempt of [
      halves,
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
    if (!this.ts || this.stopped || this.unavailable) return nothingToDeliver;
    const chunks: SlackStreamChunk[] = [...this.plan.failRunning(this.taskIds)];
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
  private async openWith(chunks: SlackStreamChunk[]): Promise<boolean> {
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
    }
    this.everOpened = true;
    this.chars = 0;
    this.text = '';
    this.logs[this.earlier.length] = [];
    this.taskIds = new Set();
    this.track(opening);
    return true;
  }

  private currentLog(): ({ text: string } | { task: string })[] {
    const index = this.earlier.length;
    this.logs[index] ??= [];
    return this.logs[index];
  }

  private track(chunks: SlackStreamChunk[]): void {
    for (const chunk of chunks) {
      if (chunk.type === 'plan_update') continue;
      if (chunk.type === 'task_update') {
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
    await this.pace();
    await this.client.apiCall('chat.appendStream', {
      channel: this.channel,
      ts: this.ts,
      chunks,
    });
  }

  /** The current message expired: keep it, and continue in a new one. */
  private retire(): void {
    if (this.ts) this.earlier.push(this.ts);
    this.ts = null;
  }

  private async rollover(): Promise<void> {
    if (this.ts) {
      // A code fence left open would swallow the rest of this message's text.
      const fence = openFence(this.text);
      const close: SlackStreamChunk[] = fence
        ? [{ type: 'markdown_text', text: `\n${fence.marker}\n` }]
        : [];
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
