/**
 * Calendar days as the viewer sees them: in the browser's time zone, never UTC.
 *
 * Servers send UTC instants, so "Today" must start at the viewer's local midnight. A local day is not always 24
 * hours (a DST change makes it 23 or 25), so days are counted on the calendar, never by dividing elapsed time.
 */

const MS_PER_DAY = 86_400_000;

/** The first instant of the local calendar day `time` falls in. */
export function startOfLocalDay(time: number | Date): number {
  const date = new Date(time);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** The start of the local day `days` calendar days after the day `time` falls in (before it when negative). */
export function addLocalDays(time: number | Date, days: number): number {
  const date = new Date(startOfLocalDay(time));
  date.setDate(date.getDate() + days);
  return date.getTime();
}

/** Local calendar days from `earlier`'s day to `later`'s: 0 on the same day, 1 when `earlier` was yesterday. */
export function localDaysBetween(earlier: number | Date, later: number | Date): number {
  const a = new Date(earlier);
  const b = new Date(later);
  // The wall dates, compared as UTC dates, are a whole number of 24-hour days apart whatever the zone's DST does.
  return Math.round(
    (Date.UTC(b.getFullYear(), b.getMonth(), b.getDate()) - Date.UTC(a.getFullYear(), a.getMonth(), a.getDate())) /
      MS_PER_DAY,
  );
}

export function isSameLocalDay(a: number | Date, b: number | Date): boolean {
  return localDaysBetween(a, b) === 0;
}
