/**
 * Reusable time-grouping utility.
 *
 * Groups any array of items into three fixed time buckets:
 *   - Today
 *   - Previous 7 Days
 *   - Older
 *
 * The caller decides:
 *   1. How to extract a timestamp from each item (`getTimestamp`).
 *   2. Which buckets to include via `enabledGroups` (default: all three).
 *
 * @example
 * ```ts
 * // All three groups
 * const groups = groupByTime(items, (i) => i.updatedAt);
 *
 * // Only "Today" and "Older" (merging 7-day items into Older)
 * const groups = groupByTime(items, (i) => i.updatedAt, {
 *   enabledGroups: { today: true, previous7Days: false, older: true },
 * });
 * ```
 */

// ── Public types ──

/** The three fixed bucket keys. */
export const TIME_GROUP_KEYS = ['Today', 'Previous 7 Days', 'Older'] as const;
export type TimeGroupKey = (typeof TIME_GROUP_KEYS)[number];

/** Toggle individual buckets on/off. Disabled buckets merge into the next enabled one. */
export interface EnabledGroups {
  today?: boolean;
  previous7Days?: boolean;
  older?: boolean;
}

export interface GroupByTimeOptions {
  /**
   * Which groups to emit. Defaults to all three enabled.
   *
   * When a group is disabled its items fall into the next *enabled* bucket
   * below it (i.e. towards "Older"). If no lower bucket is enabled either,
   * items are placed in the nearest enabled bucket above.
   */
  enabledGroups?: EnabledGroups;
}

// ── Implementation ──

/**
 * Assign a raw bucket key based on the timestamp.
 */
function rawBucket(ts: number, todayStart: number, sevenDaysAgoStart: number): TimeGroupKey {
  if (ts >= todayStart) return 'Today';
  if (ts >= sevenDaysAgoStart) return 'Previous 7 Days';
  return 'Older';
}

/**
 * Given a raw bucket and enabled flags, find the actual bucket to put the item in.
 * Strategy: if the raw bucket is disabled, try the next lower bucket; if that's
 * also disabled, try the one above.
 */
function resolveBucket(
  raw: TimeGroupKey,
  enabled: Required<EnabledGroups>,
): TimeGroupKey | null {
  const bucketEnabled: Record<TimeGroupKey, boolean> = {
    'Today': enabled.today,
    'Previous 7 Days': enabled.previous7Days,
    'Older': enabled.older,
  };

  if (bucketEnabled[raw]) return raw;

  // Try downwards first
  const idx = TIME_GROUP_KEYS.indexOf(raw);
  for (let i = idx + 1; i < TIME_GROUP_KEYS.length; i++) {
    if (bucketEnabled[TIME_GROUP_KEYS[i]]) return TIME_GROUP_KEYS[i];
  }
  // Then upwards
  for (let i = idx - 1; i >= 0; i--) {
    if (bucketEnabled[TIME_GROUP_KEYS[i]]) return TIME_GROUP_KEYS[i];
  }

  return null; // all disabled — shouldn't happen in practice
}

/**
 * Group items into time buckets.
 *
 * @param items        Array of items to group.
 * @param getTimestamp  Extractor returning epoch‑ms for each item.
 * @param options       Optional configuration.
 * @returns A `Record<TimeGroupKey, T[]>` containing only the enabled groups.
 */
export function groupByTime<T>(
  items: T[],
  getTimestamp: (item: T) => number,
  options?: GroupByTimeOptions,
): Record<TimeGroupKey, T[]> {
  const enabled: Required<EnabledGroups> = {
    today: options?.enabledGroups?.today ?? true,
    previous7Days: options?.enabledGroups?.previous7Days ?? true,
    older: options?.enabledGroups?.older ?? true,
  };

  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const sevenDaysAgoStart = todayStart - 7 * 24 * 60 * 60 * 1000;

  // Initialise only the enabled buckets
  const groups = {} as Record<TimeGroupKey, T[]>;
  for (const key of TIME_GROUP_KEYS) {
    const keyEnabled: Record<TimeGroupKey, boolean> = {
      'Today': enabled.today,
      'Previous 7 Days': enabled.previous7Days,
      'Older': enabled.older,
    };
    if (keyEnabled[key]) {
      groups[key] = [];
    }
  }

  for (const item of items) {
    const ts = getTimestamp(item);
    const raw = rawBucket(ts, todayStart, sevenDaysAgoStart);
    const bucket = resolveBucket(raw, enabled);
    if (bucket) {
      groups[bucket].push(item);
    }
  }

  return groups;
}

/**
 * Filter a grouped record down to only non-empty buckets, preserving display order.
 */
export function getNonEmptyGroups<T>(
  groups: Record<TimeGroupKey, T[]>,
): Array<[TimeGroupKey, T[]]> {
  return TIME_GROUP_KEYS
    .filter((key) => groups[key]?.length > 0)
    .map((key) => [key, groups[key]] as [TimeGroupKey, T[]]);
}
