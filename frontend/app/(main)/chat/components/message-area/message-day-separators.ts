import type { MessagePair } from './message-pairs';

export type DayLabel =
  | { kind: 'today' }
  | { kind: 'yesterday' }
  | { kind: 'date'; text: string };

function startOfLocalDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function parseValid(iso: string | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Local calendar day of an ISO timestamp, or null when it is missing or unparsable. */
export function localDayKey(iso: string | undefined): string | null {
  const date = parseValid(iso);
  if (!date) return null;
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

/**
 * Which pairs open a new calendar day, keyed by pair key, with the timestamp
 * that opens it. A pair without a usable `createdAt` (a question that is still
 * being sent) never starts a day and does not break the run it sits in.
 */
export function findDayStarts(pairs: readonly MessagePair[]): Map<string, string> {
  const starts = new Map<string, string>();
  let previousDay: string | null = null;
  for (const pair of pairs) {
    const day = localDayKey(pair.createdAt);
    if (day === null || pair.createdAt === undefined) continue;
    if (day !== previousDay) starts.set(pair.key, pair.createdAt);
    previousDay = day;
  }
  return starts;
}

/** "Today" / "Yesterday" for the last two local days, otherwise a short date. */
export function getDayLabel(iso: string, locale: string, now: Date = new Date()): DayLabel | null {
  const date = parseValid(iso);
  if (!date) return null;

  const dayMs = startOfLocalDay(date);
  const todayMs = startOfLocalDay(now);
  const yesterday = new Date(todayMs);
  yesterday.setDate(yesterday.getDate() - 1);

  if (dayMs === todayMs) return { kind: 'today' };
  if (dayMs === startOfLocalDay(yesterday)) return { kind: 'yesterday' };

  const sameYear = date.getFullYear() === now.getFullYear();
  const text = new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    ...(sameYear ? {} : { year: 'numeric' }),
  }).format(date);
  return { kind: 'date', text };
}
