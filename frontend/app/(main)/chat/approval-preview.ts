/**
 * What an approval card shows about a held call (UX1): the action in human
 * terms and a readable, redacted preview of what would be sent. Pure, so the
 * card and its tests share one rule.
 */

import {
  REDACT_ONLY_PATTERN_SOURCES,
  SECRET_KEY_NAME_FLAGS,
  SECRET_KEY_NAME_SOURCE,
  SECRET_MASK,
  VENDOR_PATTERN_SOURCES,
} from '@/lib/contracts/harness/vectors/token-patterns';
import { RULE_LAYERS, type RuleLayer } from '@/lib/contracts/harness/generated/core/enums';
import { displaySafe, type DisplayMode } from '@/lib/utils/display-safe';
import type { ApprovalEffect, ApprovalReason } from './types';

export interface ApprovalAction {
  /** "Send message": the tool's display name, or its name made readable. */
  action: string;
  /** "Slack": the app the tool belongs to, when its name says so. */
  app: string | null;
}

export interface PreviewField {
  key: string;
  label: string;
  /** The value as text; secrets replaced by `redactedValue`. */
  value: string;
  redacted: boolean;
  /** The value was longer than the preview shows; never for a destination (`DESTINATION_KEYS`). */
  truncated: boolean;
  /** The whole value (already redacted), for "Show all". */
  full: string;
}

export const PREVIEW_CHARS = 280;
export const REDACTED = SECRET_MASK;

const APP_NAMES: Readonly<Record<string, string>> = {
  slack: 'Slack',
  teams: 'Microsoft Teams',
  outlook: 'Outlook',
  gmail: 'Gmail',
  drive: 'Google Drive',
  calendar: 'Google Calendar',
  onedrive: 'OneDrive',
  sharepoint: 'SharePoint',
  jira: 'Jira',
  jira_data_center: 'Jira',
  confluence: 'Confluence',
  confluence_data_center: 'Confluence',
  github: 'GitHub',
  clickup: 'ClickUp',
  salesforce: 'Salesforce',
  zoom: 'Zoom',
  lumos: 'Lumos',
};

const titleCase = (text: string): string =>
  text
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word, index) => (index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(' ');

/**
 * `slack__send_message` → Send message, Slack; an MCP tool `mcp_<server>_<tool>` → its server. A display name is a
 * third party's text (an MCP server's title): shown display-safe, so it cannot hide or reorder what it says.
 */
export function approvalAction(toolName: string, displayName?: string): ApprovalAction {
  const [prefix, rest] = toolName.includes('__') ? toolName.split('__', 2) : [null, toolName];
  const app = prefix ? (APP_NAMES[prefix] ?? displaySafe(titleCase(prefix))) : null;
  const named = displayName?.trim();
  return { action: named ? displaySafe(named) : displaySafe(titleCase(rest ?? toolName)), app };
}

/** The first-party apps a card names; an MCP server named like one is marked as a server (SEC-B-04). */
export const FIRST_PARTY_APPS: readonly string[] = Object.keys(APP_NAMES)
  .map((key) => APP_NAMES[key])
  .concat(['PipesHub']);

const appKey = (name: string): string => name.normalize('NFKC').toLowerCase().replace(/[^a-z0-9]+/g, '');

/** A tool of one of PipesHub's own toolsets (`slack__send_message`), whose app is that toolset's. */
export const isFirstPartyTool = (toolName: string): boolean => {
  const at = toolName.indexOf('__');
  return at > 0 && Object.prototype.hasOwnProperty.call(APP_NAMES, toolName.slice(0, at));
};

/** Whether `name` reads as one of PipesHub's own apps, whatever its case, spacing or punctuation. */
export const passesForFirstParty = (name: string): boolean =>
  FIRST_PARTY_APPS.some((app) => appKey(app) === appKey(name) && appKey(name) !== '');

/**
 * The arguments that say where a call's content goes (`contracts/harness/vectors/display-safe.json`
 * `destinationArgs`, the worker's own list): the preview never cuts one, so no recipient hides behind "Show all".
 */
export const DESTINATION_KEYS: readonly string[] = [
  'channel', 'channels', 'channel_name', 'channel_id', 'user', 'mail_to', 'to_recipients', 'to',
  'recipients', 'mail_cc', 'cc_recipients', 'cc', 'mail_bcc', 'bcc_recipients', 'bcc', 'project_key',
  'space_key', 'space_id', 'page_id', 'parent_page_id', 'repo', 'repository', 'chat_id', 'team_id',
  'conversation_id', 'webhook_url', 'url',
];

// The shared vector's secret key names: a value under one is masked whole, as Node's approvals queue masks it.
const SECRET_KEY = new RegExp(SECRET_KEY_NAME_SOURCE, SECRET_KEY_NAME_FLAGS);
// The shared vector (contracts/harness/vectors/token-patterns.json): what the gateways refuse, the card masks.
const SECRET_VALUE: readonly RegExp[] = [...VENDOR_PATTERN_SOURCES, ...REDACT_ONLY_PATTERN_SOURCES].map(
  ({ source }) => new RegExp(source, 'g'),
);
// Content first: the person decides on what is said, then on the details.
const CONTENT_KEYS: readonly string[] = [
  'message', 'text', 'body', 'mail_body', 'content', 'comment', 'description', 'summary', 'subject',
  'mail_subject', 'title', 'page_body_html',
];

/** How a value under `key` is shown: a message body reads as prose, anything else as an identifier. */
const modeFor = (key: string): DisplayMode => (CONTENT_KEYS.indexOf(key.toLowerCase()) === -1 ? 'identifier' : 'prose');

/**
 * Text a decision surface shows, from a call or a reason (SEC-B-04, ADR-TEXT-01): masked, then display-safe, so a
 * bidi control or a zero-width character is a visible marker and a lookalike host is in punycode.
 */
export function safeText(text: string, mode: DisplayMode = 'identifier'): string {
  return displaySafe(redactText(text).text, mode);
}

export function redactText(text: string): { text: string; redacted: boolean } {
  let redacted = false;
  let out = text;
  for (const pattern of SECRET_VALUE) {
    out = out.replace(pattern, () => {
      redacted = true;
      return REDACTED;
    });
  }
  return { text: out, redacted };
}

/**
 * A JSON value as the card shows it: every secret masked (a key named like one masks its whole value, a string by
 * shape) and every string display-safe, a body as prose and anything else as an identifier.
 */
function redactValue(value: unknown, key = ''): { value: unknown; redacted: boolean } {
  if (key !== '' && SECRET_KEY.test(key)) return { value: REDACTED, redacted: true };
  if (typeof value === 'string') {
    const { text, redacted } = redactText(value);
    return { value: displaySafe(text, modeFor(key)), redacted };
  }
  if (Array.isArray(value)) {
    const items = value.map((item) => redactValue(item, key));
    return { value: items.map((i) => i.value), redacted: items.some((i) => i.redacted) };
  }
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).map(([k, v]) => {
      const out = redactValue(v, k);
      return { key: safeText(k), ...out };
    });
    return {
      value: Object.fromEntries(entries.map((e) => [e.key, e.value])),
      redacted: entries.some((e) => e.redacted),
    };
  }
  return { value, redacted: false };
}

function parseArgs(args: string): unknown {
  try {
    return JSON.parse(args);
  } catch {
    return args;
  }
}

/** The call's arguments as one readable, redacted text: pretty JSON when they parse, the journaled text otherwise. */
export function redactedArgs(args: string): string {
  const { value } = redactValue(parseArgs(args));
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function asText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value) && value.every((v) => typeof v === 'string' || typeof v === 'number')) {
    return value.join(', ');
  }
  return JSON.stringify(value, null, 2) ?? String(value);
}

/**
 * The call's arguments as labelled fields: content first, secrets redacted
 * by key or by shape, long values cut at `limit` with the full text kept.
 * Arguments that are not a JSON object are one field, redacted the same way.
 */
export function previewPayload(args: string, limit = PREVIEW_CHARS): PreviewField[] {
  const parsed = parseArgs(args);
  const entries: Array<[string, unknown]> =
    parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? Object.entries(parsed as Record<string, unknown>)
      : [['', parsed]];
  const rank = (key: string): number => {
    const at = CONTENT_KEYS.indexOf(key.toLowerCase());
    return at === -1 ? CONTENT_KEYS.length : at;
  };
  return entries
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([key, value]) => {
      const masked = redactValue(value, key);
      const text = asText(masked.value);
      const redacted = masked.redacted;
      const truncated = text.length > limit && DESTINATION_KEYS.indexOf(key.toLowerCase()) === -1;
      const shownKey = redactText(key).text;
      return {
        key: shownKey,
        label: shownKey === '' ? '' : titleCase(displaySafe(shownKey)),
        value: truncated ? `${text.slice(0, limit).trimEnd()}…` : text,
        redacted,
        truncated,
        full: text,
      };
    });
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const optionalText = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;
/** A destination or a source title from a response: display-safe however old the worker that wrote it. */
const optionalSafe = (value: unknown): string | undefined => {
  const text = optionalText(value);
  return text === undefined ? undefined : displaySafe(text);
};

/** The layer a reason names, or undefined for none this page knows. */
export function toRuleLayer(value: unknown): RuleLayer | undefined {
  return (RULE_LAYERS as readonly unknown[]).includes(value) ? (value as RuleLayer) : undefined;
}

/** A reason from a response whose shape the page does not control; `null` when it is not one. */
export function toApprovalReason(value: unknown): ApprovalReason | null {
  if (!isRecord(value) || typeof value.rule !== 'string') return null;
  const sensitivity = value.sensitivity;
  const measured = sensitivity === 'public' || sensitivity === 'private' || sensitivity === 'unknown';
  if (sensitivity !== undefined && !measured) return null;
  const sources = Array.isArray(value.sources) ? value.sources.filter(isRecord) : [];
  const destination = optionalSafe(value.destination);
  const layer = toRuleLayer(value.layer);
  const fieldOrder = Array.isArray(value.fieldOrder)
    ? value.fieldOrder.filter((name): name is string => typeof name === 'string' && name.length > 0).slice(0, 64)
    : [];
  return {
    rule: value.rule,
    ...(layer ? { layer } : {}),
    ...(measured ? { sensitivity } : {}),
    sources: sources.map((source) => {
      const title = optionalSafe(source.title);
      const recordId = optionalText(source.recordId);
      const url = optionalText(source.url);
      return { ...(title ? { title } : {}), ...(recordId ? { recordId } : {}), ...(url ? { url } : {}) };
    }),
    moreSources: typeof value.moreSources === 'number' && value.moreSources > 0 ? value.moreSources : 0,
    ...(destination ? { destination } : {}),
    conversationGrant: value.conversationGrant === true,
    ...(value.ruleGrant === true ? { ruleGrant: true as const } : {}),
    ...(value.editable === true ? { editable: true as const } : {}),
    ...(value.editable === true && fieldOrder.length > 0 ? { fieldOrder } : {}),
    ...editionFields(value),
  };
}

// The reason's own fields; any other is an edition rule's, kept for `approvalAskRules` as the server sent it.
const OWN_REASON_FIELDS: ReadonlySet<string> = new Set([
  'interactionId', 'rule', 'layer', 'toolId', 'sensitivity', 'sources', 'moreSources', 'destination', 'argsDigest',
  'conversationGrant', 'editable', 'fieldOrder', 'ruleGrant', 'edition',
]);

function editionFields(value: Record<string, unknown>): { edition?: Readonly<Record<string, unknown>> } {
  const edition = Object.fromEntries(Object.entries(value).filter(([field]) => !OWN_REASON_FIELDS.has(field)));
  return Object.keys(edition).length > 0 ? { edition } : {};
}

/**
 * The reason as the card shows it: the destination and source titles masked like
 * the arguments. A worker older than QA-11 journaled them as the call had them.
 */
export function maskedReason(reason: ApprovalReason): ApprovalReason {
  const mask = (text: string): string => safeText(text);
  return {
    ...reason,
    sources: reason.sources.map((source) => (source.title ? { ...source, title: mask(source.title) } : source)),
    ...(reason.destination ? { destination: mask(reason.destination) } : {}),
  };
}

/** A source's link: the PipesHub record page (access is checked there), else an outside http(s) link. */
export function sourceHref(source: { recordId?: string; url?: string }): string | null {
  if (source.recordId) return `/record/${encodeURIComponent(source.recordId)}/`;
  if (source.url && /^https?:\/\//.test(source.url)) return source.url;
  return null;
}

/** The decision routes bound a denial's reason (Node's `MAX_DENY_REASON_CHARS`); the card's field holds no more. */
export const MAX_DENY_REASON_CHARS = 1000;

/** A held call's effect preview as far as it is well formed (UX-03): a malformed field is left out, never trusted. */
export function approvalEffectOf(value: unknown): ApprovalEffect | undefined {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const data = value as Record<string, unknown>;
  const effect: ApprovalEffect = {};
  if (typeof data.diff === 'string' && data.diff.length > 0) {
    effect.diff = data.diff;
    if (data.diffTruncated === true) effect.diffTruncated = true;
  }
  const cost = data.costEstimate;
  if (cost !== null && typeof cost === 'object' && !Array.isArray(cost)) {
    const usd = (cost as Record<string, unknown>).usd;
    if (typeof usd === 'number' && Number.isFinite(usd) && usd >= 0) effect.costEstimate = { usd, basis: 'declared' };
  }
  return Object.keys(effect).length > 0 ? effect : undefined;
}

export type DiffLineKind = 'file' | 'hunk' | 'add' | 'del' | 'ctx';

export interface DiffLine {
  kind: DiffLineKind;
  /** The line as text, secrets masked. */
  text: string;
}

/** A unified diff as lines to colour, with any secret it carries masked as the arguments' preview masks it. */
export function diffLines(diff: string): DiffLine[] {
  const lines = diff.replace(/\n$/, '').split('\n');
  return lines.map((line) => {
    const kind: DiffLineKind =
      line.startsWith('+++') || line.startsWith('---')
        ? 'file'
        : line.startsWith('@@')
          ? 'hunk'
          : line.startsWith('+')
            ? 'add'
            : line.startsWith('-')
              ? 'del'
              : 'ctx';
    // A diff is code: a bidi control in it reorders what a reviewer reads (Trojan Source), so it is a marker too.
    return { kind, text: displaySafe(redactText(line).text, 'prose') };
  });
}

/** What the diff changes, from its `+++ b/<target>` header; null when it names none. */
export function diffTarget(diff: string): string | null {
  const header = /^\+\+\+ (?:b\/)?(.+)$/m.exec(diff);
  const target = header?.[1]?.trim();
  return target && target !== '/dev/null' ? target : null;
}

/** When a schedule runs, as its card reads it; a webhook's or connector's trigger is not a time and is never one. */
export type ScheduleWhenShown = { kind: 'once'; at: string; timeZone: string } | { kind: 'cron'; cron: string; timeZone: string };

/** `schedule_create`'s arguments (ADR-SCH-01 D2) as its card shows them: each text masked and display-safe. */
export interface ScheduleCallPreview {
  name: PreviewField;
  ask: PreviewField;
  when: ScheduleWhenShown;
  notify: string[];
}

function textField(key: string, value: string, mode: DisplayMode, limit: number): PreviewField {
  const { text, redacted } = redactText(value);
  const full = displaySafe(text, mode);
  const truncated = full.length > limit;
  return { key, label: '', value: truncated ? `${full.slice(0, limit).trimEnd()}…` : full, redacted, truncated, full };
}

function whenOf(value: unknown): ScheduleWhenShown | null {
  if (!isRecord(value)) return null;
  const timeZone = typeof value.timeZone === 'string' ? safeText(value.timeZone) : '';
  if (value.kind === 'once' && typeof value.at === 'string' && !Number.isNaN(Date.parse(value.at))) {
    return { kind: 'once', at: value.at, timeZone };
  }
  if (value.kind === 'cron' && typeof value.cron === 'string' && value.cron.trim() !== '') {
    return { kind: 'cron', cron: safeText(value.cron), timeZone };
  }
  return null;
}

const notifyOf = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every((channel) => typeof channel === 'string')
    ? value.map((channel: string) => safeText(channel))
    : null;

const filledText = (value: unknown): value is string => typeof value === 'string' && value.trim() !== '';

/**
 * The schedule a `schedule_create` call would make, or null when its arguments are not one (the card then lists
 * them as any call's): the name, when it runs, what each run is asked and where its notices go.
 */
export function scheduleCallOf(args: string, limit = PREVIEW_CHARS): ScheduleCallPreview | null {
  const parsed = parseArgs(args);
  if (!isRecord(parsed) || !filledText(parsed.name) || !filledText(parsed.ask)) return null;
  const when = whenOf(parsed.when);
  if (when === null) return null;
  return {
    name: textField('name', parsed.name, 'identifier', limit),
    ask: textField('ask', parsed.ask, 'prose', limit),
    when,
    notify: Array.isArray(parsed.notify)
      ? parsed.notify.filter((channel): channel is string => typeof channel === 'string').map((channel) => safeText(channel))
      : [],
  };
}

/** What `schedule_update` does with no field changed (ADR-SCH-03 D4). */
export type ScheduleAction = 'pause' | 'resume' | 'skip_next' | 'run_now';
const SCHEDULE_ACTIONS: ReadonlySet<string> = new Set<ScheduleAction>(['pause', 'resume', 'skip_next', 'run_now']);

/** A field `schedule_update` changes, before and after; `when` and `notify` stay structured for their words. */
export type ScheduleFieldChange =
  | { field: 'name' | 'ask'; before: PreviewField; after: PreviewField }
  | { field: 'when'; before: ScheduleWhenShown; after: ScheduleWhenShown }
  | { field: 'notify'; before: string[]; after: string[] };

/**
 * What a `schedule_update` or `schedule_delete` call does to the schedule it names (ADR-SCH-03 D5), with the
 * schedule's current values as the server read them before asking.
 */
export type ScheduleChangePreview =
  | { kind: 'action'; action: ScheduleAction; name: PreviewField }
  | { kind: 'edit'; name: PreviewField; changes: ScheduleFieldChange[] }
  | { kind: 'delete'; name: PreviewField; when: ScheduleWhenShown | null; ask: PreviewField | null };

const UPDATE_FIELDS = ['name', 'ask', 'when', 'notify'] as const;

const sameWhen = (a: ScheduleWhenShown, b: ScheduleWhenShown): boolean =>
  a.kind === b.kind && a.timeZone === b.timeZone && (a.kind === 'once' ? a.at === (b as typeof a).at : a.cron === (b as typeof a).cron);

/**
 * The change, or null whenever a before and after can't both be told for certain: no current values, current
 * values of another schedule or of another revision than the call names, a field the current values leave out,
 * arguments that are not a change. The card
 * then lists the call's arguments as any call's, never a guessed "before".
 */
export function scheduleChangeOf(
  tool: 'update' | 'delete',
  args: string,
  current: unknown,
  limit = PREVIEW_CHARS,
): ScheduleChangePreview | null {
  const parsed = parseArgs(args);
  if (!isRecord(parsed) || !filledText(parsed.id) || !isRecord(current)) return null;
  if (current.id !== parsed.id || !filledText(current.name)) return null;
  // The before is the schedule as the call was written against it: one that moved on since is not that "before".
  if (typeof parsed.revision !== 'number' || current.revision !== parsed.revision) return null;
  const name = textField('name', current.name, 'identifier', limit);
  const currentAsk = filledText(current.ask) ? textField('ask', current.ask, 'prose', limit) : null;
  const currentWhen = whenOf(current.when);
  if (tool === 'delete') return { kind: 'delete', name, when: currentWhen, ask: currentAsk };
  const named = UPDATE_FIELDS.filter((field) => parsed[field] !== undefined);
  if (parsed.action !== undefined) {
    if (named.length > 0 || typeof parsed.action !== 'string' || !SCHEDULE_ACTIONS.has(parsed.action)) return null;
    return { kind: 'action', action: parsed.action as ScheduleAction, name };
  }
  if (named.length === 0) return null;
  const changes: ScheduleFieldChange[] = [];
  for (const field of named) {
    const value = parsed[field];
    if (field === 'name' || field === 'ask') {
      const before = field === 'name' ? name : currentAsk;
      if (!filledText(value) || before === null) return null;
      if (value !== current[field]) changes.push({ field, before, after: textField(field, value, field === 'name' ? 'identifier' : 'prose', limit) });
    } else if (field === 'when') {
      const after = whenOf(value);
      if (after === null || currentWhen === null) return null;
      if (!sameWhen(currentWhen, after)) changes.push({ field, before: currentWhen, after });
    } else {
      const after = notifyOf(value);
      const before = notifyOf(current.notify);
      if (after === null || before === null) return null;
      if (after.join(',') !== before.join(',')) changes.push({ field, before, after });
    }
  }
  return changes.length === 0 ? null : { kind: 'edit', name, changes };
}
