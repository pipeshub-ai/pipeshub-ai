import type { InboxItem } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-item';
import type { InboxPreferences } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-preferences';
import type { InboxNotification, InboxRowAction } from './view-models';
import type { Words } from './words';
import { formatDayMonth, formatTime as clockOf, formatWeekdayTime } from './dates';
import type { TFunction } from 'i18next';
import { interactionTitle } from '@/chat/interaction-wording';

/**
 * The one Inbox feed (`GET /api/v1/agent-harness/inbox`, ADR-INBOX-01) as the design's rows (`inbox-panel.tsx`,
 * design.md §6A.17, §6B.23–25): asks first on the accent ground, then notices; each row named by what it is about,
 * with how it went under it, the mark of the kind of thing it is, and its actions in the one vocabulary (the yes
 * filled, the maybe outlined, the no in red).
 *
 * Words are the reader's language, by `kind`, from the item's typed `subject` (ADR-INBOX-01 A1): the names in it (a
 * chat's title, a workflow's name, the question asked) are data and kept as they are; everything around them is worded
 * here. The server's `title` and `description` are its English fallback, used as they are for an item without a
 * subject this client knows (a notice written before subjects, or a newer `subjectVersion`). Nothing is read out of
 * the English. Pure: no React, no store.
 */

/** `waiting` is Needs you: the asks only, and the server reads no notices for it (BG-1). */
export type InboxFilter = 'all' | 'unread' | 'waiting' | 'mentions' | 'workflows' | 'shared' | 'connectors' | 'skills';
/** The filters an Inbox of notices offers as chips; Needs you has its own page. */
export const INBOX_FILTERS: ReadonlyArray<InboxFilter> = ['all', 'unread', 'mentions', 'workflows', 'shared', 'connectors', 'skills'];
export type InboxView = 'inbox' | 'archived';

/** The subject shape this client words; an item of a later version reads as its `title` and `description`. */
export const KNOWN_SUBJECT_VERSION = 1;

export type InboxSubject = InboxItem['subject'];

export interface InboxRowWords {
  title: string;
  description: string;
}

/** The item's subject when this client knows its shape; null means word the row from `title`. */
export function subjectOf(item: InboxItem): InboxSubject | null {
  const version = (item as { subjectVersion?: unknown }).subjectVersion;
  const subject = (item as { subject?: unknown }).subject;
  if (version !== KNOWN_SUBJECT_VERSION || !subject || typeof subject !== 'object') return null;
  return subject as InboxSubject;
}

/**
 * An approval's title in the approval card's own words ("Create ticket in E2Esaas?", ADR-INBOX-02): one wording for
 * the chat and Needs you, from the tool's id. A server before `tool` sends the name and app only.
 */
function approvalTitle(action: NonNullable<InboxSubject['action']>, t: Words): string {
  if (action.tool) {
    return interactionTitle(t as unknown as TFunction, {
      toolName: action.tool,
      displayName: action.name,
      ...(action.destination
        ? { reason: { rule: '', sources: [], moreSources: 0, conversationGrant: false, destination: action.destination } }
        : {}),
    });
  }
  return action.app ? t('ui.inbox.words.approvalTitleIn', { what: action.name, app: action.app }) : t('ui.inbox.words.approvalTitle', { what: action.name });
}

/** What a row says, in the reader's language. */
export function inboxWords(item: InboxItem, t: Words, locale?: string): InboxRowWords {
  const keep = { title: item.title, description: item.description };
  const s = subjectOf(item);
  if (!s) return keep;
  switch (item.kind) {
    case 'approval':
      if (s.connect) return { title: t('ui.inbox.words.connectTitle', { what: s.connect }), description: t('ui.inbox.words.connectWaiting') };
      if (s.action)
        return { title: approvalTitle(s.action, t), description: t('ui.inbox.words.approvalWaiting') };
      return keep;
    case 'question':
      return { title: t('ui.inbox.words.questionTitle'), description: s.question ?? item.description };
    case 'memory_review':
      return {
        title: t(item.pointer.type === 'memory' && item.pointer.agentKey ? 'ui.inbox.words.memoryAgentTitle' : 'ui.inbox.words.memoryOwnTitle'),
        description: s.memory ?? item.description,
      };
    case 'skill_proposal':
      return s.skill ? { title: t('ui.inbox.words.skillTitle', { name: s.skill }), description: item.description } : keep;
    case 'artefact_review':
      return s.skill
        ? {
            title: t('ui.inbox.words.skillReviewTitle', { name: s.skill }),
            description: item.severity === 'warning' ? t('ui.inbox.words.skillReviewFlagged') : item.description,
          }
        : keep;
    case 'run_finished':
    case 'run_not_finished': {
      const outcome = s.outcome ?? (item.kind === 'run_not_finished' ? 'not_finished' : undefined);
      const key = outcome === 'finished' ? 'ui.inbox.words.finished' : outcome === 'cut_short' ? 'ui.inbox.words.finishedEarly' : outcome === 'not_finished' ? 'ui.inbox.words.notFinished' : null;
      return { title: s.chat ?? item.title, description: key ? t(key) : item.description };
    }
    case 'schedule_ran':
    case 'schedule_failed':
    case 'schedule_waiting': {
      const title = s.workflow ?? item.title;
      switch (s.outcome) {
        case 'ran':
          return { title, description: t('ui.inbox.words.workflowRan') };
        case 'failed':
          return { title, description: t('ui.inbox.words.workflowFailed') };
        case 'refused':
          return { title, description: t(s.reason ? `ui.inbox.words.workflowRefusedWhy.${s.reason}` : 'ui.inbox.words.workflowRefused') };
        case 'waiting': {
          const by = s.deadlineAt !== undefined ? formatTime(s.deadlineAt, locale) : null;
          return { title, description: by ? t('ui.inbox.words.workflowWaitingBy', { time: by }) : t('ui.inbox.words.workflowWaiting') };
        }
        default:
          return { title, description: item.description };
      }
    }
    case 'share': {
      const agent = item.pointer.type === 'conversation' && Boolean(item.pointer.agentKey);
      return {
        title: t(agent ? 'ui.inbox.words.sharedAgentChat' : 'ui.inbox.words.sharedChat', { name: s.sharedBy ?? t('ui.inbox.words.colleague') }),
        description: s.chat ? t('ui.inbox.words.sharedOpen', { title: s.chat }) : t('ui.inbox.words.sharedOpenList'),
      };
    }
    case 'mention':
      return { title: t('ui.inbox.words.mentionTitle'), description: s.chat ? t('ui.inbox.words.mentionIn', { chat: s.chat }) : item.description };
    case 'connector':
    case 'system':
      // Another service's own words: its writer's, shown as it wrote them.
      return keep;
  }
}

/** The mark of the kind of thing a row is about (design.md §6B.25), and whether it reports a failure. */
export function inboxMark(item: InboxItem): { icon: string; tone: 'accent' | 'danger' | 'warning' | 'quiet' } {
  switch (item.kind) {
    case 'approval':
      return { icon: 'front_hand', tone: 'accent' };
    case 'question':
      return { icon: 'help', tone: 'accent' };
    case 'skill_proposal':
      return { icon: 'extension', tone: 'accent' };
    case 'artefact_review':
      return { icon: 'policy', tone: item.severity === 'warning' ? 'warning' : 'accent' };
    case 'memory_review':
      return { icon: 'psychology', tone: 'accent' };
    case 'run_finished':
      return { icon: 'assistant', tone: 'accent' };
    case 'run_not_finished':
      return { icon: 'assistant', tone: 'danger' };
    case 'schedule_ran':
      return { icon: 'account_tree', tone: 'accent' };
    case 'schedule_failed':
      return { icon: 'account_tree', tone: item.severity === 'warning' ? 'warning' : 'danger' };
    case 'schedule_waiting':
      return { icon: 'account_tree', tone: 'warning' };
    case 'share':
      return { icon: 'assistant', tone: 'accent' };
    case 'mention':
      return { icon: 'alternate_email', tone: 'accent' };
    case 'connector':
    case 'system':
      return item.severity === 'error'
        ? { icon: 'error_outline', tone: 'danger' }
        : item.severity === 'warning'
          ? { icon: 'warning_amber', tone: 'warning' }
          : item.severity === 'success'
            ? { icon: 'check', tone: 'quiet' }
            : { icon: 'info', tone: 'quiet' };
  }
}

const RUN_KINDS = new Set<InboxItem['kind']>(['run_finished', 'run_not_finished', 'schedule_ran', 'schedule_failed', 'schedule_waiting']);

/** Where following the row goes: its in-app link, or nowhere when the server had none (`/`). */
export function inboxHref(item: InboxItem): string | undefined {
  return item.link && item.link !== '/' ? item.link : undefined;
}

/** A row's actions, in weight order (§6B.23): what settles it in place keeps the panel open; a pointer leaves. */
export function inboxActions(item: InboxItem, t: Words): InboxRowAction[] {
  const href = inboxHref(item);
  if (item.archived) return [{ id: 'restore', label: t('ui.inbox.action.restore'), weight: 'secondary', leaves: false }];
  switch (item.kind) {
    case 'approval':
      return [{ id: 'openAsk', label: t('ui.inbox.action.reviewInChat'), weight: 'primary', leaves: true }];
    case 'question':
      return [{ id: 'openAsk', label: t('ui.inbox.action.answerInChat'), weight: 'primary', leaves: true }];
    case 'memory_review':
      return href ? [{ id: 'open', label: t('ui.inbox.action.reviewMemory'), weight: 'secondary', leaves: true }] : [];
    case 'artefact_review':
      // The decision needs the diff and the scan's findings, so it is made in the review panel, never in place.
      return href ? [{ id: 'open', label: t('ui.inbox.action.reviewSkill'), weight: 'primary', leaves: true }] : [];
    case 'skill_proposal':
      return [
        { id: 'approveSkill', label: t('ui.inbox.action.approve'), weight: 'primary', leaves: false },
        { id: 'editSkill', label: t('ui.inbox.action.edit'), weight: 'secondary', leaves: true },
        { id: 'rejectSkill', label: t('ui.inbox.action.reject'), weight: 'no', leaves: false },
      ];
    case 'share':
    case 'mention':
      return href ? [{ id: 'open', label: t('ui.inbox.action.openChat'), weight: 'secondary', leaves: true }] : [];
    case 'connector':
    case 'system':
      return href ? [{ id: 'open', label: t('ui.inbox.action.open'), weight: 'secondary', leaves: true }] : [];
    default: {
      const out: InboxRowAction[] = [];
      if (RUN_KINDS.has(item.kind) && href) out.push({ id: 'viewRun', label: t('ui.inbox.action.viewRun'), weight: 'secondary', leaves: true });
      if (item.pointer.type === 'schedule')
        out.push(
          item.muted
            ? { id: 'unmuteWorkflow', label: t('ui.inbox.action.unmuteWorkflow'), weight: 'secondary', leaves: false }
            : { id: 'muteWorkflow', label: t('ui.inbox.action.muteWorkflow'), weight: 'secondary', leaves: false },
        );
      return out;
    }
  }
}

/** "now", "5 min ago", "yesterday": the reader's own words for how long ago, from the platform's clock words. */
export function relativeTime(at: number, now: number, locale: string | undefined, t: Words): string {
  const seconds = Math.round((at - now) / 1000);
  if (Math.abs(seconds) < 60) return t('ui.inbox.justNow');
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto', style: 'short' });
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return rtf.format(minutes, 'minute');
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return rtf.format(hours, 'hour');
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 7) return rtf.format(days, 'day');
  return formatDayMonth(at, locale);
}

function formatTime(at: number, locale: string | undefined): string | null {
  if (!Number.isFinite(at)) return null;
  return formatWeekdayTime(at, locale);
}

/** One feed item as the design's row. */
export function inboxRow(item: InboxItem, now: number, t: Words, locale?: string): InboxNotification {
  const words = inboxWords(item, t, locale);
  const mark = inboxMark(item);
  const href = inboxHref(item);
  return {
    id: item.id,
    severity: item.severity,
    title: words.title,
    description: words.description,
    time: relativeTime(item.at, now, locale, t),
    unread: item.unread,
    kind: item.kind,
    waiting: item.waiting,
    archived: item.archived,
    icon: mark.icon,
    tone: mark.tone,
    ...(href ? { href } : {}),
    actions: inboxActions(item, t),
    ...(item.pointer.type === 'schedule' ? { workflow: words.title, muteId: item.pointer.scheduleId, muted: item.muted === true } : {}),
    ...('runId' in item.pointer && item.pointer.runId ? { runId: item.pointer.runId } : {}),
  };
}

/** The panel's rows: asks first, then notices, as the feed answered (asks are only on its first page). */
export function inboxRows(items: ReadonlyArray<InboxItem>, now: number, t: Words, locale?: string): InboxNotification[] {
  const rows = items.map((item) => inboxRow(item, now, t, locale));
  return [...rows.filter((r) => r.waiting), ...rows.filter((r) => !r.waiting)];
}

/** The notices "Archive read" takes: read notices only, never an ask (an ask is not news you have read). */
export function readNoticeIds(items: ReadonlyArray<InboxItem>): string[] {
  return items.filter((i) => !i.waiting && !i.unread && !i.archived && i.id.startsWith('n:')).map((i) => i.id);
}

export type PauseChoice = 'hour' | 'tomorrow' | 'resumed' | 'off';

/** Paused, and until when: null when not paused; `Infinity` until the person resumes. */
export function pausedUntil(prefs: Pick<InboxPreferences, 'pause'> | null, now: number): number | null {
  const pause = prefs?.pause ?? null;
  if (pause === null) return null;
  if ('untilResumed' in pause) return Infinity;
  return pause.until > now ? pause.until : null;
}

/** "5:30 PM", "tomorrow at 9:00 AM", or "you resume them", in the reader's language. */
export function pausedLabel(until: number, now: number, locale: string | undefined, t: Words): string {
  if (until === Infinity) return t('ui.inbox.pause.untilResumedLabel');
  const time = clockOf(until, locale);
  return new Date(until).toDateString() === new Date(now).toDateString() ? time : t('ui.inbox.pause.tomorrowAt', { time });
}

/** The badge's words for the glyph's accessible name. */
export function inboxGlyphLabel(badge: number, paused: boolean, t: Words): string {
  if (paused) return badge > 0 ? t('ui.inbox.glyphPausedCount', { count: badge }) : t('ui.inbox.glyphPaused');
  return badge > 0 ? t('ui.topBar.inboxUnread', { count: badge }) : t('ui.topBar.inbox');
}
