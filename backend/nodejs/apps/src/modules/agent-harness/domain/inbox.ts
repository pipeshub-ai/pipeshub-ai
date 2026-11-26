/**
 * The Inbox feed's rules (ADR-INBOX-01): one list per person of what waits on them (approvals, questions, skill
 * proposals, memory reviews) and what happened while they were elsewhere (notices: finished runs, workflow runs,
 * shares, mentions, connector news). This module is pure: kinds, filters, ordering, pause times and cursors.
 */
import type { NotificationRow } from '../../../libs/repositories/notification/types';
import {
  firedScheduleOf,
  workflowRefusalOf,
  type WorkflowRefusal,
} from './schedules';
import type { HarnessRun } from './execution';
import {
  INBOX_ITEM_KINDS,
  type InboxItemKind,
} from '../contracts/generated/ext/pipeshub/enums';

/** What an item is about (the contract's `InboxItemKind`). A waiting kind is an ask; every other kind is a notice. */
export { INBOX_ITEM_KINDS, type InboxItemKind };

export const WAITING_KINDS: ReadonlySet<InboxItemKind> = new Set([
  'approval',
  'question',
  'skill_proposal',
  'artefact_review',
  'memory_review',
]);

export const INBOX_FILTERS = [
  'all',
  'unread',
  'waiting',
  'mentions',
  'workflows',
  'shared',
  'connectors',
  'skills',
] as const;
export type InboxFilter = (typeof INBOX_FILTERS)[number];

export const INBOX_VIEWS = ['inbox', 'archived'] as const;
export type InboxView = (typeof INBOX_VIEWS)[number];

export type InboxSeverity = 'error' | 'success' | 'warning' | 'info';

/** Where an item opens: what it is about, by the ids the routes take. `link` is the in-app path. */
export type InboxPointer =
  | {
      type: 'conversation';
      conversationId: string;
      agentKey?: string;
      runId?: string;
      interactionId?: string;
      callId?: string;
    }
  | {
      type: 'schedule';
      scheduleId: string;
      runId?: string;
      conversationId?: string;
      agentKey?: string;
    }
  | { type: 'skill_candidate'; candidateId: string }
  | { type: 'catalog_item'; itemKind: 'skill'; name: string }
  | { type: 'memory'; memoryId: string; agentKey?: string }
  | { type: 'link'; path: string };

/**
 * What an item is about, by name (UIP3-FU-1, `InboxSubject`): each kind its own fields, so a client words the row in
 * the reader's language; `title` and `description` stay the English fallback.
 */
export interface InboxSubject {
  /** `tool` and `destination` (ADR-INBOX-02): the client words the call from them as its approval card does. */
  action?: {
    name: string;
    app: string | null;
    tool?: string;
    destination?: string;
  };
  connect?: string;
  question?: string;
  skill?: string;
  memory?: string;
  chat?: string;
  workflow?: string;
  sharedBy?: string;
  deadlineAt?: number;
  outcome?:
    | 'finished'
    | 'cut_short'
    | 'not_finished'
    | 'ran'
    | 'failed'
    | 'refused'
    | 'waiting';
  reason?: WorkflowRefusal;
  source?: string;
}

/** The subject's shape; a client that knows a lower one words the row from `title`. */
export const INBOX_SUBJECT_VERSION = 1;

export interface InboxItem {
  /** Stable across reads: `n:` a notice, `a:` an approval, `q:` a question, `s:` a skill proposal, `m:` a memory. */
  id: string;
  kind: InboxItemKind;
  severity: InboxSeverity;
  /** An ask that waits on the person: sorts first, has no read state, and is never paused. */
  waiting: boolean;
  unread: boolean;
  archived: boolean;
  title: string;
  description: string;
  /** When it happened or began waiting, epoch ms. */
  at: number;
  pointer: InboxPointer;
  link: string;
  /** A workflow's notice: whether the person muted that workflow. */
  muted?: boolean;
  subjectVersion: typeof INBOX_SUBJECT_VERSION;
  subject: InboxSubject;
}

/** The person's Inbox settings. */
export interface InboxPreferences {
  mutedSchedules: string[];
  /** Null: not paused. `untilResumed`: paused until the person resumes. */
  pause: { until: number } | { untilResumed: true } | null;
  /** Finished-run notices beyond the Inbox, where the person opted in (and the org allows the channel). */
  runNotices: { email: boolean; slack: boolean };
}

export const DEFAULT_INBOX_PREFERENCES: InboxPreferences = Object.freeze({
  mutedSchedules: [],
  pause: null,
  runNotices: { email: false, slack: false },
});

export const MAX_MUTED_SCHEDULES = 200;

export interface InboxCounts {
  /** Unread notices. */
  unread: number;
  /** Asks waiting on the person. */
  waiting: number;
  /** What the badge shows: asks always, notices unless paused. */
  badge: number;
  paused: boolean;
  /** Epoch ms the pause ends; null when not paused or paused until resumed. */
  pausedUntil: number | null;
  /** A push only: a source that answers only with the person's own credential was not counted; fetch the count. */
  recount?: boolean;
  /** The person's jobs at work (`isWorkingJob`, CJ14-FU-1); absent when this server cannot count them. */
  working?: number;
}

/** Whether the pause is in force at `now`. */
export function isPaused(
  prefs: Pick<InboxPreferences, 'pause'>,
  now: number,
): boolean {
  const pause = prefs.pause;
  if (pause === null) return false;
  return 'untilResumed' in pause || pause.until > now;
}

export function inboxCounts(
  unread: number,
  waiting: number,
  prefs: Pick<InboxPreferences, 'pause'>,
  now: number,
): InboxCounts {
  const paused = isPaused(prefs, now);
  return {
    unread,
    waiting,
    badge: waiting + (paused ? 0 : unread),
    paused,
    pausedUntil:
      paused && prefs.pause !== null && 'until' in prefs.pause
        ? prefs.pause.until
        : null,
  };
}

export const PAUSE_CHOICES = ['hour', 'tomorrow', 'resumed', 'off'] as const;
export type PauseChoice = (typeof PAUSE_CHOICES)[number];

const HOUR_MS = 3_600_000;
/** "Until tomorrow" ends at this local hour. */
export const TOMORROW_HOUR = 9;

/** Whether `zone` is an IANA time zone this runtime knows. */
export function isTimeZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** The wall-clock fields of `ms` in `zone`. */
function wallClock(
  ms: number,
  zone: string,
): { y: number; m: number; d: number; h: number; min: number; s: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone,
    hourCycle: 'h23',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
  }).formatToParts(new Date(ms));
  const field = (type: string): number =>
    Number(parts.find((p) => p.type === type)?.value ?? '0');
  return {
    y: field('year'),
    m: field('month'),
    d: field('day'),
    h: field('hour'),
    min: field('minute'),
    s: field('second'),
  };
}

/** The instant the wall clock in `zone` reads y-m-d h:00, found from the zone's offset (DST-safe to the hour). */
function zonedInstant(
  y: number,
  m: number,
  d: number,
  h: number,
  zone: string,
): number {
  const guess = Date.UTC(y, m - 1, d, h);
  const offsetAt = (instant: number): number => {
    const w = wallClock(instant, zone);
    return Date.UTC(w.y, w.m - 1, w.d, w.h, w.min, w.s) - instant;
  };
  const first = guess - offsetAt(guess);
  return guess - offsetAt(first);
}

/**
 * When a pause chosen at `now` ends: an hour; tomorrow at 09:00 on the person's own clock (`zone`, UTC when unknown);
 * until resumed; or not at all (`off`).
 */
export function pauseFor(
  choice: PauseChoice,
  now: number,
  zone = 'UTC',
): InboxPreferences['pause'] {
  switch (choice) {
    case 'hour':
      return { until: now + HOUR_MS };
    case 'tomorrow': {
      const tz = isTimeZone(zone) ? zone : 'UTC';
      const today = wallClock(now, tz);
      const next = new Date(Date.UTC(today.y, today.m - 1, today.d + 1));
      return {
        until: zonedInstant(
          next.getUTCFullYear(),
          next.getUTCMonth() + 1,
          next.getUTCDate(),
          TOMORROW_HOUR,
          tz,
        ),
      };
    }
    case 'resumed':
      return { untilResumed: true };
    case 'off':
      return null;
  }
}

/**
 * Which filters list a kind (ADR-INBOX-01 §Filters). `unread` is decided by the item's state, not its kind;
 * `waiting` lists the asks only (Needs you), so it never reads a notice source.
 */
const FILTER_KINDS: Readonly<
  Record<
    Exclude<InboxFilter, 'all' | 'unread' | 'waiting'>,
    ReadonlySet<InboxItemKind>
  >
> = {
  mentions: new Set(['mention']),
  workflows: new Set(['schedule_ran', 'schedule_failed', 'schedule_waiting']),
  shared: new Set(['share']),
  connectors: new Set(['connector']),
  skills: new Set(['skill_proposal', 'artefact_review', 'memory_review']),
};

export function filterLists(
  filter: InboxFilter,
  item: Pick<InboxItem, 'kind' | 'unread' | 'waiting'>,
): boolean {
  if (filter === 'all') return true;
  if (filter === 'unread') return item.waiting || item.unread;
  if (filter === 'waiting') return item.waiting;
  return FILTER_KINDS[filter].has(item.kind);
}

/** Whether a filter can list a waiting item at all (so the feed skips reading those sources). */
export const filterListsWaiting = (filter: InboxFilter): boolean =>
  filter === 'all' ||
  filter === 'unread' ||
  filter === 'waiting' ||
  filter === 'skills';

/** Whether a filter can list notices: the two that list asks only never read the notice sources. */
export const filterListsNotices = (filter: InboxFilter): boolean =>
  filter !== 'skills' && filter !== 'waiting';

/** Asks first, newest first; then notices, newest first; ties by id so a page boundary is stable. */
export function inboxOrder(a: InboxItem, b: InboxItem): number {
  if (a.waiting !== b.waiting) return a.waiting ? -1 : 1;
  if (a.at !== b.at) return b.at - a.at;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/* ---------------- Notices ---------------- */

/** The notice types the harness writes for an interactive run that ended unseen (ADR-INBOX-01 §Finished runs). */
export const RUN_FINISHED_NOTIFICATION = 'AGENT_RUN_FINISHED';
export const RUN_NOT_FINISHED_NOTIFICATION = 'AGENT_RUN_NOT_FINISHED';
export const CHAT_MENTION_NOTIFICATION = 'chatMention';
const CHAT_SHARED = 'chatShared';
const SCHEDULE_RUN_ENDED = 'AGENT_SCHEDULE_RUN_ENDED';
const SCHEDULE_APPROVAL_WAITING = 'AGENT_SCHEDULE_APPROVAL_WAITING';
const SCHEDULE_FIRING_REFUSED = 'AGENT_SCHEDULE_FIRING_REFUSED';
const CONNECTOR_ORIGINS = new Set(['Connector Service', 'Indexing Service']);

const str = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;

/** A notice's kind, by the type its writer gave it (and, for connector news, by its origin). */
export function noticeKind(
  row: Pick<NotificationRow, 'type' | 'originService' | 'payload'>,
): InboxItemKind {
  switch (row.type) {
    case RUN_FINISHED_NOTIFICATION:
      return 'run_finished';
    case RUN_NOT_FINISHED_NOTIFICATION:
      return 'run_not_finished';
    case CHAT_SHARED:
      return 'share';
    case CHAT_MENTION_NOTIFICATION:
      return 'mention';
    case SCHEDULE_RUN_ENDED:
      return row.payload?.['status'] === 'completed'
        ? 'schedule_ran'
        : 'schedule_failed';
    case SCHEDULE_APPROVAL_WAITING:
      return 'schedule_waiting';
    case SCHEDULE_FIRING_REFUSED:
      return 'schedule_failed';
    default:
      return row.originService !== undefined &&
        CONNECTOR_ORIGINS.has(row.originService)
        ? 'connector'
        : 'system';
  }
}

const SEVERITIES: ReadonlySet<string> = new Set([
  'error',
  'success',
  'warning',
  'info',
]);

function noticeSeverity(
  kind: InboxItemKind,
  stored: string | undefined,
): InboxSeverity {
  if (kind === 'run_finished' || kind === 'schedule_ran')
    return stored === 'warning' ? 'warning' : 'success';
  if (kind === 'run_not_finished') return 'error';
  if (kind === 'schedule_failed')
    return stored === 'warning' ? 'warning' : 'error';
  if (kind === 'schedule_waiting') return 'warning';
  if (kind === 'share' || kind === 'mention') return 'info';
  if (stored === 'critical') return 'error';
  return stored !== undefined && SEVERITIES.has(stored)
    ? (stored as InboxSeverity)
    : 'info';
}

function noticePointer(
  kind: InboxItemKind,
  row: Pick<NotificationRow, 'payload' | 'redirectLink'>,
): InboxPointer {
  const payload = row.payload ?? {};
  const conversationId = str(payload['conversationId']);
  const agentKey = str(payload['agentKey']);
  const runId = str(payload['runId']);
  const scheduleId = str(payload['scheduleId']);
  if (
    scheduleId !== undefined &&
    (kind === 'schedule_ran' ||
      kind === 'schedule_failed' ||
      kind === 'schedule_waiting')
  ) {
    return {
      type: 'schedule',
      scheduleId,
      ...(runId !== undefined ? { runId } : {}),
      ...(conversationId !== undefined ? { conversationId } : {}),
      ...(agentKey !== undefined ? { agentKey } : {}),
    };
  }
  if (conversationId !== undefined) {
    return {
      type: 'conversation',
      conversationId,
      ...(agentKey !== undefined ? { agentKey } : {}),
      ...(runId !== undefined ? { runId } : {}),
    };
  }
  return { type: 'link', path: safeLink(row.redirectLink) };
}

/** An in-app path; anything else (an absolute URL a writer put there) reads as the Inbox's own page. */
export function safeLink(link: string | undefined): string {
  return link !== undefined && link.startsWith('/') && !link.startsWith('//')
    ? link
    : '/';
}

/** A subject field: trimmed, clipped to the contract's bound; absent when empty or not text. */
export function subjectText(value: unknown, max: number): string | undefined {
  if (typeof value !== 'string') return undefined;
  const text = value.trim();
  return text.length === 0 ? undefined : clip(text, max);
}

const OUTCOMES: Partial<Record<InboxItemKind, InboxSubject['outcome']>> = {
  schedule_ran: 'ran',
  schedule_waiting: 'waiting',
};

/** A notice's subject, from the payload its writer stored (never from its English words). */
export function noticeSubject(
  kind: InboxItemKind,
  row: Pick<NotificationRow, 'payload' | 'originService' | 'type'>,
): InboxSubject {
  const p = row.payload ?? {};
  const set = <K extends keyof InboxSubject>(
    out: InboxSubject,
    key: K,
    value: InboxSubject[K] | undefined,
  ): void => {
    if (value !== undefined) out[key] = value;
  };
  const out: InboxSubject = {};
  switch (kind) {
    case 'run_finished':
      set(out, 'chat', subjectText(p['chatTitle'], 300));
      out.outcome = p['cutShort'] === true ? 'cut_short' : 'finished';
      break;
    case 'run_not_finished':
      set(out, 'chat', subjectText(p['chatTitle'], 300));
      out.outcome = 'not_finished';
      break;
    case 'schedule_ran':
    case 'schedule_failed':
    case 'schedule_waiting':
      set(out, 'workflow', subjectText(p['scheduleName'], 200));
      out.outcome =
        OUTCOMES[kind] ??
        (row.type === SCHEDULE_FIRING_REFUSED ? 'refused' : 'failed');
      if (out.outcome === 'refused') {
        out.reason = workflowRefusalOf(
          typeof p['reason'] === 'string' ? p['reason'] : '',
        );
      }
      if (
        typeof p['deadlineAt'] === 'number' &&
        Number.isSafeInteger(p['deadlineAt']) &&
        p['deadlineAt'] >= 0
      ) {
        out.deadlineAt = p['deadlineAt'];
      }
      break;
    case 'share':
      set(out, 'sharedBy', subjectText(p['sharerName'], 256));
      set(out, 'chat', subjectText(p['chatTitle'], 300));
      break;
    case 'mention':
      set(out, 'chat', subjectText(p['chatTitle'], 300));
      break;
    case 'approval':
    case 'artefact_review':
    case 'connector':
    case 'memory_review':
    case 'question':
    case 'skill_proposal':
    case 'system':
    default:
      set(out, 'source', subjectText(row.originService, 128));
  }
  return out;
}

const MAX_TITLE = 300;
const MAX_DESCRIPTION = 1_000;
const clip = (text: string, max: number): string =>
  Array.from(text).length <= max
    ? text
    : `${Array.from(text)
        .slice(0, max - 1)
        .join('')}…`;

/** A stored notification as an Inbox item. */
export function noticeItem(
  row: NotificationRow,
  muted: ReadonlySet<string>,
): InboxItem {
  const kind = noticeKind(row);
  const pointer = noticePointer(kind, row);
  const scheduleId =
    pointer.type === 'schedule' ? pointer.scheduleId : undefined;
  return {
    id: `n:${row._id}`,
    kind,
    severity: noticeSeverity(kind, row.severity),
    waiting: false,
    unread: row.status === 'unread',
    archived: row.status === 'archived',
    title: clip(row.title?.trim() || defaultTitle(kind), MAX_TITLE),
    description: clip(row.message?.trim() ?? '', MAX_DESCRIPTION),
    at: row.createdAt.getTime(),
    pointer,
    link: safeLink(row.redirectLink),
    ...(scheduleId !== undefined ? { muted: muted.has(scheduleId) } : {}),
    subjectVersion: INBOX_SUBJECT_VERSION,
    subject: noticeSubject(kind, row),
  };
}

function defaultTitle(kind: InboxItemKind): string {
  switch (kind) {
    case 'run_finished':
      return 'Finished';
    case 'run_not_finished':
      return "Couldn't finish";
    case 'share':
      return 'Shared with you';
    case 'mention':
      return 'You were mentioned';
    case 'schedule_ran':
      return 'A workflow ran';
    case 'schedule_failed':
      return "A workflow couldn't run";
    case 'schedule_waiting':
      return 'A workflow is waiting for you';
    case 'approval':
    case 'question':
    case 'skill_proposal':
    case 'artefact_review':
    case 'memory_review':
    case 'connector':
    case 'system':
      return 'Notice';
  }
}

/** A notice id the routes take (`n:<notification id>`), as the notification id; null for any other item. */
export function noticeIdOf(itemId: string): string | null {
  return /^n:[A-Za-z0-9_-]{1,64}$/.test(itemId) ? itemId.slice(2) : null;
}

/* ---------------- Cursors ---------------- */

export interface NoticeCursor {
  at: number;
  id: string;
}

export function encodeNoticeCursor(cursor: NoticeCursor): string {
  return Buffer.from(JSON.stringify([cursor.at, cursor.id]), 'utf8').toString(
    'base64url',
  );
}

/** Null when the text is not a cursor this module wrote. */
export function decodeNoticeCursor(text: string): NoticeCursor | null {
  try {
    const value: unknown = JSON.parse(
      Buffer.from(text, 'base64url').toString('utf8'),
    );
    if (
      Array.isArray(value) &&
      value.length === 2 &&
      Number.isSafeInteger(value[0]) &&
      (value[0] as number) >= 0 &&
      typeof value[1] === 'string' &&
      /^[A-Za-z0-9_-]{1,64}$/.test(value[1])
    ) {
      return { at: value[0] as number, id: value[1] };
    }
  } catch {
    return null;
  }
  return null;
}

/* ---------------- Finished-run notices ---------------- */

export type RunEndVerdict =
  | {
      notify: false;
      reason:
        | 'child'
        | 'scheduled'
        | 'service_caller'
        | 'not_ended'
        | 'cancelled'
        | 'asked_a_question';
    }
  | { notify: true; finished: boolean; cutShort: boolean };

/**
 * ADR-INBOX-01 §Finished runs, the part decided by the run alone: a person's own root run (not a job, not a
 * workflow's firing, not a service credential's) that ended in a way they did not cause and are not already asked
 * about. Whether they watched it end, or have seen it since, is the caller's to check.
 */
export function runEndVerdict(
  run: Pick<
    HarnessRun,
    'status' | 'outcome' | 'idempotencyKey' | 'parent' | 'input' | 'truncatedBy'
  > &
    Partial<Pick<HarnessRun, 'attribution'>>,
): RunEndVerdict {
  if (run.parent !== undefined) return { notify: false, reason: 'child' };
  if (firedScheduleOf(run) !== null)
    return { notify: false, reason: 'scheduled' };
  if (run.input?.caller !== undefined)
    return { notify: false, reason: 'service_caller' };
  if (run.status === 'cancelled') return { notify: false, reason: 'cancelled' };
  if (run.status === 'failed')
    return { notify: true, finished: false, cutShort: false };
  if (run.status !== 'completed') return { notify: false, reason: 'not_ended' };
  if (run.outcome === 'needs_input')
    return { notify: false, reason: 'asked_a_question' };
  return {
    notify: true,
    finished: true,
    cutShort: run.truncatedBy !== undefined,
  };
}

/** What a finished-run notice says (ADR-INBOX-01 §Words): named by the chat, the outcome in the description. */
export function runNoticeWords(notice: {
  finished: boolean;
  cutShort: boolean;
  chatTitle: string;
}): {
  type: string;
  severity: InboxSeverity;
  title: string;
  message: string;
  /** One word for email subjects and Slack: "Finished" or "Couldn't finish". */
  outcome: string;
} {
  const title = notice.chatTitle.trim() || 'Your chat';
  if (!notice.finished) {
    return {
      type: RUN_NOT_FINISHED_NOTIFICATION,
      severity: 'error',
      title,
      message:
        "Couldn't finish. Open the chat to see what happened and try again.",
      outcome: "Couldn't finish",
    };
  }
  return {
    type: RUN_FINISHED_NOTIFICATION,
    severity: notice.cutShort ? 'warning' : 'success',
    title,
    message: notice.cutShort
      ? 'Finished early: it stopped at its limit and answered with what it had.'
      : 'Finished. The answer is ready.',
    outcome: 'Finished',
  };
}
