/**
 * Reusable time-grouping utility.
 *
 * Default buckets (local date):
 *   - Today
 *   - Yesterday
 *   - Previous 7 days — 7 local calendar days before the start of yesterday
 *   - Older
 *
 * The caller provides epoch-ms via `getTimestamp`. For chat sidebars, use
 * `getConversationLastActivityMs` from `conversation-activity`.
 *
 * When a bucket is disabled, items merge to the next enabled lower bucket, then
 * the next higher one (see {@link resolveBucket}).
 */

// ── Public types ──

export const TIME_GROUP_KEYS = [
  'Today',
  'Yesterday',
  'Previous 7 Days',
  'Older',
] as const;
export type TimeGroupKey = (typeof TIME_GROUP_KEYS)[number];

export interface EnabledGroups {
  today?: boolean;
  yesterday?: boolean;
  previous7Days?: boolean;
  older?: boolean;
}

export interface GroupByTimeOptions {
  /**
   * Which groups to emit. All four default to on.
   *
   * When a group is disabled its items fall into the next *enabled* bucket
   * below it (i.e. towards "Older"). If no lower bucket is enabled either,
   * items are placed in the nearest enabled bucket above.
   */
  enabledGroups?: EnabledGroups;
}

// ── Time boundaries (local wall calendar; avoids DST 7×24h drift) ──

function startOfLocalDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/**
 * @returns { todayStart, yesterdayStart, prev7Start } in epoch ms, local time.
 * "Previous 7" covers [prev7Start, yesterdayStart).
 */
function getTimeBoundariesMs(): { todayStart: number; yesterdayStart: number; prev7Start: number } {
  const now = new Date();
  const todayStart = startOfLocalDay(now);

  const y = new Date(todayStart);
  y.setDate(y.getDate() - 1);
  const yesterdayStart = startOfLocalDay(y);

  const p7 = new Date(yesterdayStart);
  p7.setDate(p7.getDate() - 7);
  const prev7Start = p7.getTime();

  return { todayStart, yesterdayStart, prev7Start };
}

// ── Raw bucket (before disabled-group resolution) ──

function rawBucket(
  ts: number,
  todayStart: number,
  yesterdayStart: number,
  prev7Start: number,
): TimeGroupKey {
  if (ts >= todayStart) return 'Today';
  if (ts >= yesterdayStart) return 'Yesterday';
  if (ts >= prev7Start) return 'Previous 7 Days';
  return 'Older';
}

// ── Enabled flags ──

type EnabledFour = {
  today: boolean;
  yesterday: boolean;
  previous7Days: boolean;
  older: boolean;
};

function resolveEnabled(options?: GroupByTimeOptions): EnabledFour {
  return {
    today: options?.enabledGroups?.today ?? true,
    yesterday: options?.enabledGroups?.yesterday ?? true,
    previous7Days: options?.enabledGroups?.previous7Days ?? true,
    older: options?.enabledGroups?.older ?? true,
  };
}

/**
 * If the raw bucket is disabled, try a lower (Older) enabled bucket, then a higher one.
 */
function resolveBucket(
  raw: TimeGroupKey,
  enabled: EnabledFour,
): TimeGroupKey | null {
  const bucketEnabled: Record<TimeGroupKey, boolean> = {
    Today: enabled.today,
    Yesterday: enabled.yesterday,
    'Previous 7 Days': enabled.previous7Days,
    Older: enabled.older,
  };

  if (bucketEnabled[raw]) return raw;

  const idx = TIME_GROUP_KEYS.indexOf(raw);
  for (let i = idx + 1; i < TIME_GROUP_KEYS.length; i += 1) {
    if (bucketEnabled[TIME_GROUP_KEYS[i]]) return TIME_GROUP_KEYS[i];
  }
  for (let i = idx - 1; i >= 0; i -= 1) {
    if (bucketEnabled[TIME_GROUP_KEYS[i]]) return TIME_GROUP_KEYS[i];
  }
  return null;
}

// ── Public API ──

/**
 * Group items into time buckets.
 */
export function groupByTime<T>(
  items: T[],
  getTimestamp: (item: T) => number,
  options?: GroupByTimeOptions,
): Record<TimeGroupKey, T[]> {
  const enabled = resolveEnabled(options);
  const { todayStart, yesterdayStart, prev7Start } = getTimeBoundariesMs();

  const groups = {} as Record<TimeGroupKey, T[]>;
  for (const key of TIME_GROUP_KEYS) {
    const keyOn: Record<TimeGroupKey, boolean> = {
      Today: enabled.today,
      Yesterday: enabled.yesterday,
      'Previous 7 Days': enabled.previous7Days,
      Older: enabled.older,
    };
    if (keyOn[key]) groups[key] = [];
  }

  for (const item of items) {
    const rawTs = getTimestamp(item);
    const ts = Number.isFinite(rawTs) ? rawTs : 0;
    const raw = rawBucket(ts, todayStart, yesterdayStart, prev7Start);
    const bucket = resolveBucket(raw, enabled);
    if (bucket) {
      groups[bucket].push(item);
    }
  }

  return groups;
}

/**
 * Only non-empty buckets, in default section order, optionally sorting by newest first.
 */
export function getNonEmptyGroups<T>(
  groups: Record<TimeGroupKey, T[]>,
  sortValueDesc?: (item: T) => number,
): Array<[TimeGroupKey, T[]]> {
  return TIME_GROUP_KEYS
    .filter((key) => (groups[key]?.length ?? 0) > 0)
    .map((key) => {
      const arr = groups[key] as T[];
      if (!sortValueDesc) {
        return [key, arr] as [TimeGroupKey, T[]];
      }
      return [key, [...arr].sort((a, b) => sortValueDesc(b) - sortValueDesc(a))] as [
        TimeGroupKey,
        T[],
      ];
    });
}
