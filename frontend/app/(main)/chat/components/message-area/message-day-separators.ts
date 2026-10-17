import { getTimeGroupKey, type TimeGroupKey } from '@/lib/utils/group-by-time';
import type { MessagePair } from './message-pairs';

/** i18n key of each group's label; the same strings the sidebar shows. */
export const GROUP_LABEL_KEY: Record<TimeGroupKey, string> = {
  Today: 'timeGroup.today',
  Yesterday: 'timeGroup.yesterday',
  'Previous 7 Days': 'timeGroup.previous7Days',
  Older: 'timeGroup.older',
};

function parseValid(iso: string | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Which pairs open a new time group ("Today", "Yesterday", "Previous 7 Days",
 * "Older"), keyed by pair key. The groups are the ones the chat sidebar uses, so
 * a chat that is touched once a day shows at most four labels, never a date per
 * day. A pair without a usable `createdAt` (a question that is still being sent)
 * never starts a group and does not break the run it sits in.
 */
export function findGroupStarts(
  pairs: readonly MessagePair[],
  now: Date = new Date(),
): Map<string, TimeGroupKey> {
  const starts = new Map<string, TimeGroupKey>();
  let previousGroup: TimeGroupKey | null = null;
  for (const pair of pairs) {
    const date = parseValid(pair.createdAt);
    if (!date) continue;
    const group = getTimeGroupKey(date.getTime(), now);
    if (group !== previousGroup) starts.set(pair.key, group);
    previousGroup = group;
  }
  return starts;
}
