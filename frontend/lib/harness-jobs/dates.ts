/**
 * Every date and time the job screens show, written by the reader's locale. Screens call these and never build
 * their own `Intl.DateTimeFormat` or `toLocale*String`: before this, People wrote "9 Nov 2026" (a hard-coded
 * day-month-year in English) beside "Nov 9, 2026" elsewhere. The gate in `__tests__/dates.test.ts` holds it.
 */

type When = string | number | Date;

function toDate(value: When): Date | null {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** An unknown or malformed locale falls back to English rather than throwing in a render. */
function format(locale: string | undefined, options: Intl.DateTimeFormatOptions, date: Date | number): string {
  try {
    return new Intl.DateTimeFormat(locale, options).format(date);
  } catch {
    return new Intl.DateTimeFormat('en', options).format(date);
  }
}

const DATE: Intl.DateTimeFormatOptions = { dateStyle: 'medium' };
const DATE_TIME: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' };
const TIME: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' };

/** "Nov 9, 2026" in en-US, "9 Nov 2026" in en-GB. Empty for an unreadable value. */
export function formatDate(value: When, locale: string | undefined): string {
  const date = toDate(value);
  return date ? format(locale, DATE, date) : '';
}

/** "Nov 9, 2026, 8:00 AM". Empty for an unreadable value. */
export function formatDateTime(value: When, locale: string | undefined): string {
  const date = toDate(value);
  return date ? format(locale, DATE_TIME, date) : '';
}

/** "Nov 9": a date near enough that its year goes without saying. */
export function formatDayMonth(value: When, locale: string | undefined): string {
  const date = toDate(value);
  return date ? format(locale, { day: 'numeric', month: 'short' }, date) : '';
}

/** "8:00 AM". */
export function formatTime(value: When, locale: string | undefined): string {
  const date = toDate(value);
  return date ? format(locale, TIME, date) : '';
}

/** "Mon 8:00 AM": a time within the coming or past week. */
export function formatWeekdayTime(value: When, locale: string | undefined): string {
  const date = toDate(value);
  return date ? format(locale, { weekday: 'short', ...TIME }, date) : '';
}

/** "Mon, Nov 9, 8:00 AM", with the year only when it is not this one. */
export function formatWeekdayDateTime(value: When, locale: string | undefined, withYear: boolean): string {
  const date = toDate(value);
  if (!date) return '';
  return format(locale, { weekday: 'short', month: 'short', day: 'numeric', ...(withYear ? { year: 'numeric' } : {}), ...TIME }, date);
}

/** A schedule's clock time ("9:30 AM"), independent of any day or time zone. */
export function formatClock(hour: number, minute: number, locale: string | undefined): string {
  return format(locale, { ...TIME, timeZone: 'UTC' }, Date.UTC(2026, 0, 1, hour, minute));
}

/** A cron weekday's name ("Sunday" for 0 and 7). 2026-01-04 was a Sunday. */
export function formatWeekdayName(day: number, locale: string | undefined): string {
  return format(locale, { weekday: 'long', timeZone: 'UTC' }, Date.UTC(2026, 0, 4 + (day % 7)));
}
