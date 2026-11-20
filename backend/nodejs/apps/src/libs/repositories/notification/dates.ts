/**
 * Scheduled notifications: a notification may carry `visibleAt`. Until that instant it is not listed, counted, changed or
 * pushed; at and after it, it behaves like any other. The same helpers serve both document stores.
 */
import { NOTIFICATION_RETENTION_DAYS } from '../../../modules/notification/utils/notification-api.utils';
import type { NewNotification } from './types';

/** A date from a Date or an ISO string (a message from the broker carries strings). Null when absent; throws when unusable. */
export function castDate(value: unknown, field: string): Date | null {
  if (value === undefined || value === null) return null;
  const d =
    value instanceof Date
      ? value
      : typeof value === 'string' || typeof value === 'number'
        ? new Date(value)
        : new Date(NaN);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`notification ${field} is not a valid date`);
  }
  return d;
}

/**
 * The row as stored: `visibleAt` and `createdAt` are dates whatever they arrived as, and a scheduled row's `updatedAt` is its
 * `visibleAt` (not the time of the insert), so the served `updatedAt` never tells a client about a row before it is visible.
 * A scheduled row with no `createdAt` takes its `visibleAt`.
 */
export function prepareNotification(row: NewNotification): NewNotification {
  // a notification has a type: MongoDB's schema requires a non-empty one, and PostgreSQL's NOT NULL alone would store an empty string
  if (typeof row.type !== 'string' || row.type === '') {
    throw new Error('notification type is required');
  }
  const visibleAt = castDate(row.visibleAt, 'visibleAt');
  const createdAt = castDate(row.createdAt, 'createdAt');
  // both stores expire a row by createdAt, so one visible only after that would be dropped unseen
  if (
    visibleAt &&
    createdAt &&
    visibleAt.getTime() - createdAt.getTime() >
      NOTIFICATION_RETENTION_DAYS * 86_400_000
  ) {
    throw new Error(
      'notification visibleAt is past the retention window of its createdAt',
    );
  }
  const out: Record<string, unknown> = { ...row };
  delete out.visibleAt;
  delete out.createdAt;
  delete out.updatedAt;
  if (visibleAt) {
    out.visibleAt = visibleAt;
    out.updatedAt = visibleAt;
  }
  // a scheduled row is created, and so ordered and kept, from the instant it becomes visible, unless the caller says otherwise
  if (createdAt ?? visibleAt) out.createdAt = createdAt ?? visibleAt;
  return out as NewNotification;
}

/** True when `visibleAt` is set and still ahead of `now`. */
export function isNotYetVisible(
  visibleAt: unknown,
  now: Date = new Date(),
): boolean {
  if (visibleAt === undefined || visibleAt === null) return false;
  const d = castDate(visibleAt, 'visibleAt');
  return d !== null && d.getTime() > now.getTime();
}
