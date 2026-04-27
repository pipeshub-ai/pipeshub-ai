/**
 * Expands rolling-window datetime operators (`last_*_days`) to `is_after` + epoch ms
 * in the shape the connector service expects.
 *
 * Parity with legacy account UI:
 * `frontend/src/sections/accountdetails/connectors/hooks/use-connector-config.ts`
 * (`processFilterFields` + `convertRelativeDateToAbsolute` + `normalizeDatetimeValueForSave`).
 */

const RELATIVE_DAY_MAP: Record<string, number> = {
  last_7_days: 7,
  last_14_days: 14,
  last_30_days: 30,
  last_90_days: 90,
  last_180_days: 180,
  last_365_days: 365,
};

/**
 * Start of local calendar day, N calendar days before today (local).
 * Matches legacy `dayjs().subtract(n, 'day').startOf('day').valueOf()`.
 */
export function computeLastNDaysEpochStartMs(days: number): number {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - days);
  return d.getTime();
}

function isDatetimeFilterRow(row: Record<string, unknown>): boolean {
  const t = row.type;
  if (t === undefined || t === null) return true;
  if (typeof t === 'string' && !t.trim()) return true;
  if (typeof t === 'string') return t.toLowerCase() === 'datetime';
  return false;
}

/** If `row` is a datetime filter with a `last_*_days` operator, return an `is_after` row with epoch bounds. */
export function expandRelativeDatetimeFilterRow(row: unknown): unknown {
  if (!row || typeof row !== 'object' || Array.isArray(row)) return row;
  const r = row as Record<string, unknown>;
  const opRaw = typeof r.operator === 'string' ? r.operator.trim().toLowerCase() : '';
  if (!opRaw.startsWith('last_')) return row;
  if (!isDatetimeFilterRow(r)) return row;

  const days = RELATIVE_DAY_MAP[opRaw];
  if (days === undefined) return row;

  const start = computeLastNDaysEpochStartMs(days);
  return {
    ...r,
    operator: 'is_after',
    value: { start, end: null },
    type: typeof r.type === 'string' && r.type.trim() ? r.type : 'datetime',
  };
}

/** Walk connector filter `values` map and expand each row in place (shallow copy of the map). */
export function expandRelativeDatetimeFiltersForSave(
  filters: Record<string, unknown> | undefined | null
): Record<string, unknown> {
  if (!filters || typeof filters !== 'object') return {};
  const out: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(filters)) {
    out[key] = expandRelativeDatetimeFilterRow(val);
  }
  return out;
}
