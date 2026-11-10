import type { DateFilterType } from '@/app/components/ui/date-range-picker';

/** Local midnight of a picker value (`YYYY-MM-DD`, optionally followed by a time). */
function startOfLocalDay(value: string): Date {
  const [y, m, d] = value.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d);
}

function nextLocalDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1);
}

/**
 * A date filter as epoch-ms bounds for the users API: `from` inclusive, `to` exclusive.
 * "Before" and "after" exclude the chosen day; "between" includes both ends.
 */
export function dateFilterToRange(
  afterDate?: string,
  beforeDate?: string,
  dateType?: DateFilterType
): { from?: string; to?: string } {
  const ms = (date: Date) => String(date.getTime());
  if (dateType === 'on' && afterDate) {
    const day = startOfLocalDay(afterDate);
    return { from: ms(day), to: ms(nextLocalDay(day)) };
  }
  if (dateType === 'before' && beforeDate) {
    return { to: ms(startOfLocalDay(beforeDate)) };
  }
  if (dateType === 'after' && afterDate) {
    return { from: ms(nextLocalDay(startOfLocalDay(afterDate))) };
  }
  if (afterDate && beforeDate) {
    return {
      from: ms(startOfLocalDay(afterDate)),
      to: ms(nextLocalDay(startOfLocalDay(beforeDate))),
    };
  }
  return {};
}
