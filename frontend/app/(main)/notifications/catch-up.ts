import type { NotificationListItem } from './api';

/**
 * Which notices arrived while the socket was down. The server keeps nothing for a socket that is not there: a push
 * sent then is lost, and only the stored row remains. So the client remembers the newest notice it has seen (by the
 * server's `createdAt`, never its own clock) and, on every reconnect, reads the newest unread page and treats what
 * is newer than that as missed. The first connect after a load only sets the mark: older notices are the inbox's.
 */
let newestSeenAt: number | null = null;
let primed = false;

const timeOf = (n: NotificationListItem): number | null => {
  const t = n.createdAt ? Date.parse(n.createdAt) : NaN;
  return Number.isNaN(t) ? null : t;
};

/** Whether a connect since the load or sign-in has set the mark; the next `takeMissed` is the first when not. */
export function isCatchUpPrimed(): boolean {
  return primed;
}

/** A notice the client has now seen, live or listed. */
export function noteSeen(n: NotificationListItem): void {
  const t = timeOf(n);
  if (t !== null && (newestSeenAt === null || t > newestSeenAt)) newestSeenAt = t;
}

/** The unread rows newer than anything seen before, oldest first; empty on the first connect. Marks them seen. */
export function takeMissed(unread: readonly NotificationListItem[]): NotificationListItem[] {
  const since = newestSeenAt;
  const first = !primed;
  primed = true;
  unread.forEach(noteSeen);
  if (first) return [];
  return unread
    .filter((n) => n.status === 'unread' && n._id)
    .filter((n) => {
      const t = timeOf(n);
      return t !== null && (since === null || t > since);
    })
    .sort((a, b) => (timeOf(a) ?? 0) - (timeOf(b) ?? 0));
}

/** Another person signed in, or the tests start over. */
export function resetCatchUp(): void {
  newestSeenAt = null;
  primed = false;
}
