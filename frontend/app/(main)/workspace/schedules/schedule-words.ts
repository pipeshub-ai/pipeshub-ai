import type { TFunction } from 'i18next';
import type { PreviewField, ScheduleChangePreview, ScheduleFieldChange } from '@/app/(main)/chat/approval-preview';

/**
 * When a schedule runs, in the reader's words (ADR-SCH-01 D4): "Every weekday at 9:00 AM (America/Los_Angeles)",
 * "Once, today at 3:47 PM". Only the common cron shapes are put in words; any other shows its cron text marked
 * "(cron)", never a guess at what it means.
 */
export type CronShape =
  | { kind: 'everyMinutes'; every: number }
  | { kind: 'hourly'; minute: number }
  | { kind: 'daily'; hour: number; minute: number }
  | { kind: 'weekdays'; hour: number; minute: number }
  | { kind: 'weekly'; day: number; hour: number; minute: number }
  | { kind: 'monthly'; dayOfMonth: number; hour: number; minute: number };

const DAY_NAMES = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];

function int(field: string, min: number, max: number): number | null {
  if (!/^\d{1,2}$/.test(field)) return null;
  const value = Number(field);
  return value >= min && value <= max ? value : null;
}

function dayOfWeek(field: string): number | null {
  const named = DAY_NAMES.indexOf(field.toUpperCase());
  if (named !== -1) return named;
  const value = int(field, 0, 7);
  return value === null ? null : value % 7;
}

const isWeekdays = (field: string): boolean => /^(1-5|MON-FRI)$/i.test(field);

/** The cron's shape when it is one of the common ones; null for anything else. */
export function cronShape(cron: string): CronShape | null {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [m, h, dom, mon, dow] = fields;
  if (mon !== '*') return null;
  if (h === '*' && dom === '*' && dow === '*') {
    if (m === '*') return { kind: 'everyMinutes', every: 1 };
    const step = /^\*\/(\d{1,2})$/.exec(m);
    if (step) {
      // A step counts within the hour (`*/35` fires at :00 and :35), so only a divisor of 60 is a steady interval.
      const every = int(step[1], 1, 59);
      return every === null || 60 % every !== 0 ? null : { kind: 'everyMinutes', every };
    }
    const minute = int(m, 0, 59);
    return minute === null ? null : { kind: 'hourly', minute };
  }
  const minute = int(m, 0, 59);
  const hour = int(h, 0, 23);
  if (minute === null || hour === null) return null;
  if (dom === '*' && dow === '*') return { kind: 'daily', hour, minute };
  if (dom === '*' && isWeekdays(dow)) return { kind: 'weekdays', hour, minute };
  if (dom === '*') {
    const day = dayOfWeek(dow);
    return day === null ? null : { kind: 'weekly', day, hour, minute };
  }
  if (dow === '*') {
    const dayOfMonth = int(dom, 1, 31);
    return dayOfMonth === null ? null : { kind: 'monthly', dayOfMonth, hour, minute };
  }
  return null;
}

function clockTime(hour: number, minute: number, language: string): string {
  const at = new Date(Date.UTC(2000, 0, 2, hour, minute));
  try {
    return new Intl.DateTimeFormat(language, { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' }).format(at);
  } catch {
    return `${hour}:${String(minute).padStart(2, '0')}`;
  }
}

function weekdayName(day: number, language: string): string {
  // 2000-01-02 was a Sunday.
  const at = new Date(Date.UTC(2000, 0, 2 + day, 12));
  try {
    return new Intl.DateTimeFormat(language, { weekday: 'long', timeZone: 'UTC' }).format(at);
  } catch {
    return DAY_NAMES[day];
  }
}

/** "Every weekday at 9:00 AM", or "0 9 1-15 * * (cron)" for a shape not put in words; without the zone. */
export function cronWords(t: TFunction, cron: string, language: string): string {
  const shape = cronShape(cron);
  if (shape === null) return t('schedules.when.cron', { cron: cron.trim() });
  switch (shape.kind) {
    case 'everyMinutes':
      return shape.every === 1 ? t('schedules.when.everyMinute') : t('schedules.when.everyMinutes', { count: shape.every });
    case 'hourly':
      return t('schedules.when.hourly', { minute: String(shape.minute).padStart(2, '0') });
    case 'daily':
      return t('schedules.when.daily', { time: clockTime(shape.hour, shape.minute, language) });
    case 'weekdays':
      return t('schedules.when.weekdays', { time: clockTime(shape.hour, shape.minute, language) });
    case 'weekly':
      return t('schedules.when.weekly', {
        day: weekdayName(shape.day, language),
        time: clockTime(shape.hour, shape.minute, language),
      });
    case 'monthly':
      return t('schedules.when.monthly', { day: shape.dayOfMonth, time: clockTime(shape.hour, shape.minute, language) });
  }
}

/** A zone `Intl` knows, or null: an unknown one is never passed to it (it throws). */
function knownZone(timeZone: string): string | null {
  try {
    new Intl.DateTimeFormat('en', { timeZone });
    return timeZone;
  } catch {
    return null;
  }
}

const readerZone = (): string | null => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
};

/** The calendar date `at` falls on in `timeZone` (null: the reader's), as numbers. */
function calendarDate(at: Date, timeZone: string | null): { year: number; month: number; day: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    ...(timeZone ? { timeZone } : {}),
  }).formatToParts(at);
  const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((p) => p.type === type)?.value);
  return { year: part('year'), month: part('month'), day: part('day') };
}

/** A calendar date `days` on from another, by the calendar: a DST change never makes a day 23 or 25 hours long here. */
function dateKey(date: { year: number; month: number; day: number }, days = 0): string {
  return new Date(Date.UTC(date.year, date.month - 1, date.day + days)).toISOString().slice(0, 10);
}

/**
 * "Once, today at 3:47 PM", "Once, tomorrow at 9:00 AM", "Once, Dec 3, 2026, 9:00 AM": the instant in the
 * schedule's zone, with the zone named when it is not the reader's. An unreadable instant is shown as given.
 */
export function onceWords(t: TFunction, at: string, timeZone: string, language: string, now: Date = new Date()): string {
  const instant = new Date(at);
  if (Number.isNaN(instant.getTime())) return t('schedules.when.onceOn', { date: at });
  const zone = knownZone(timeZone);
  const zoned = zone ? { timeZone: zone } : {};
  let words: string;
  try {
    const time = new Intl.DateTimeFormat(language, { hour: 'numeric', minute: '2-digit', ...zoned }).format(instant);
    const day = dateKey(calendarDate(instant, zone));
    const today = calendarDate(now, zone);
    if (day === dateKey(today)) words = t('schedules.when.onceToday', { time });
    else if (day === dateKey(today, 1)) words = t('schedules.when.onceTomorrow', { time });
    else {
      const date = new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short', ...zoned }).format(instant);
      words = t('schedules.when.onceOn', { date });
    }
  } catch {
    words = t('schedules.when.onceOn', { date: instant.toISOString() });
  }
  return zone && zone !== readerZone() ? t('schedules.when.withZone', { when: words, timeZone: zone }) : words;
}

export type ScheduleWhen =
  | { kind: 'once'; at: string; timeZone: string }
  | { kind: 'cron'; cron: string; timeZone: string };

/** A trigger's `when` in words, the cron's zone always named (a cron is read in its own zone). */
export function whenWords(t: TFunction, when: ScheduleWhen, language: string, now: Date = new Date()): string {
  if (when.kind === 'once') return onceWords(t, when.at, when.timeZone, language, now);
  return when.timeZone
    ? t('schedules.when.withZone', { when: cronWords(t, when.cron, language), timeZone: when.timeZone })
    : cronWords(t, when.cron, language);
}

/** The title of the card that asks to make a schedule, on the harness's card and on classic chat's. */
export const SCHEDULE_CREATE_TITLE = 'interaction.titleSchedule';

const NOTICE_CHANNEL_LABELS: Readonly<Record<string, string>> = {
  in_app: 'schedules.create.notifyInApp',
  email: 'schedules.create.notifyEmail',
  slack: 'schedules.create.notifySlack',
};

/** The parts of a schedule call the words are made from; its name and ask are shown by the card, masked. */
export interface ScheduleCallShape {
  when: ScheduleWhen;
  notify: readonly string[];
}

/** A `schedule_create` call in the reader's words, the same on every approval card (ADR-SCH-01 D4, ADR-SCH-02 D5). */
export interface ScheduleCallWords {
  title: string;
  labels: { name: string; when: string; ask: string; notify: string };
  when: string;
  /** "In PipesHub, Email"; null when the call names no channel. */
  notify: string | null;
  where: string;
}

export function scheduleCallWords(
  t: TFunction,
  call: ScheduleCallShape,
  language: string,
  now: Date = new Date(),
): ScheduleCallWords {
  return {
    title: t(SCHEDULE_CREATE_TITLE),
    labels: {
      name: t('approvalCard.schedule.name'),
      when: t('approvalCard.schedule.when'),
      ask: t('approvalCard.schedule.ask'),
      notify: t('approvalCard.schedule.notify'),
    },
    when: whenWords(t, call.when, language, now),
    notify: call.notify.length > 0 ? channelWords(t, call.notify) : null,
    where: t('approvalCard.schedule.where'),
  };
}

/** A value a change card shows: masked and display-safe text, cut where it is long ("Show all" gives `full`). */
export type ShownValue = Pick<PreviewField, 'value' | 'full' | 'truncated' | 'redacted'>;

const shown = (text: string): ShownValue => ({ value: text, full: text, truncated: false, redacted: false });

/** A `schedule_update` or `schedule_delete` call in the reader's words, the same on every approval card (ADR-SCH-03 D5). */
export interface ScheduleChangeWords {
  /** "Change Joke in two minutes", "Move Standup summary", "Pause Standup summary", "Delete Joke in two minutes". */
  title: string;
  /** Each changed field: "What it asks: a short joke → a short animal joke". */
  changes: Array<{ field: ScheduleFieldChange['field']; label: string; before: ShownValue; after: ShownValue }>;
  /** What the schedule is, for a call that changes no field (a delete, a pause): when it runs and what it asks. */
  context: Array<{ key: 'when' | 'ask'; label: string; value: ShownValue }>;
  /** What the decision means, in a sentence. */
  note: string;
}

const CHANGE_LABELS: Readonly<Record<ScheduleFieldChange['field'], string>> = {
  name: 'approvalCard.schedule.name',
  ask: 'approvalCard.schedule.change.ask',
  when: 'approvalCard.schedule.when',
  notify: 'approvalCard.schedule.notify',
};

function channelWords(t: TFunction, channels: readonly string[]): string {
  if (channels.length === 0) return t('approvalCard.schedule.change.notifyNone');
  return channels.map((channel) => (NOTICE_CHANNEL_LABELS[channel] ? t(NOTICE_CHANNEL_LABELS[channel]) : channel)).join(', ');
}

function changeSides(t: TFunction, change: ScheduleFieldChange, language: string, now: Date): { before: ShownValue; after: ShownValue } {
  switch (change.field) {
    case 'name':
    case 'ask':
      return { before: change.before, after: change.after };
    case 'when':
      return { before: shown(whenWords(t, change.before, language, now)), after: shown(whenWords(t, change.after, language, now)) };
    case 'notify':
      return { before: shown(channelWords(t, change.before)), after: shown(channelWords(t, change.after)) };
  }
}

export function scheduleChangeWords(
  t: TFunction,
  change: ScheduleChangePreview,
  language: string,
  now: Date = new Date(),
): ScheduleChangeWords {
  const name = change.name.value;
  if (change.kind === 'action') {
    return {
      title: t(`approvalCard.schedule.change.title.${change.action}`, { name }),
      changes: [],
      context: [],
      note: t(`approvalCard.schedule.change.note.${change.action}`),
    };
  }
  if (change.kind === 'delete') {
    const context: ScheduleChangeWords['context'] = [];
    if (change.when !== null) context.push({ key: 'when', label: t('approvalCard.schedule.when'), value: shown(whenWords(t, change.when, language, now)) });
    if (change.ask !== null) context.push({ key: 'ask', label: t('approvalCard.schedule.change.ask'), value: change.ask });
    return { title: t('approvalCard.schedule.change.title.delete', { name }), changes: [], context, note: t('approvalCard.schedule.change.note.delete') };
  }
  const onlyWhen = change.changes.every((c) => c.field === 'when');
  return {
    title: t(onlyWhen ? 'approvalCard.schedule.change.title.move' : 'approvalCard.schedule.change.title.change', { name }),
    changes: change.changes.map((c) => ({ field: c.field, label: t(CHANGE_LABELS[c.field]), ...changeSides(t, c, language, now) })),
    context: [],
    note: t('approvalCard.schedule.change.note.edit'),
  };
}
