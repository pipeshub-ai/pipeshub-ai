import { isValidId } from '../../../libs/repositories/ids';

export const NOTIFICATION_RETENTION_DAYS = 30;
export const DEFAULT_PAGE_SIZE = 20;
export const MAX_PAGE_SIZE = 50;

const RETENTION_SECONDS = NOTIFICATION_RETENTION_DAYS * 24 * 60 * 60;

export interface NotificationCursor {
  createdAt: Date;
  _id: string;
}

export interface PaginatedNotificationResult<T> {
  notifications: T[];
  hasMore: boolean;
  cursor: string | null;
}

export class InvalidNotificationCursorError extends Error {
  constructor(message = 'Invalid cursor') {
    super(message);
    this.name = 'InvalidNotificationCursorError';
  }
}

export function retentionCutoff(now: Date = new Date()): Date {
  return new Date(now.getTime() - RETENTION_SECONDS * 1000);
}

export interface NotificationAuthContext {
  userId: string;
  orgId: string;
}

// Narrows the untyped (Record<string, any>) req.user into validated ids so
// callers never pass unchecked values into a query.
export function resolveNotificationAuthContext(
  user: Record<string, unknown> | undefined,
): NotificationAuthContext | null {
  const userId = toIdString(user?.userId);
  const orgId = toIdString(user?.orgId);
  if (!userId || !orgId) {
    return null;
  }
  return { userId, orgId };
}

export function clampPageSize(raw: unknown): number {
  const parsed = typeof raw === 'string' ? parseInt(raw, 10) : Number(raw);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return DEFAULT_PAGE_SIZE;
  }
  return Math.min(Math.floor(parsed), MAX_PAGE_SIZE);
}

export function encodeCursor(cursor: NotificationCursor): string {
  const payload = {
    c: cursor.createdAt.toISOString(),
    i: cursor._id,
  };
  return Buffer.from(JSON.stringify(payload)).toString('base64url');
}

export function decodeCursor(raw: string): NotificationCursor {
  if (!raw || typeof raw !== 'string') {
    throw new InvalidNotificationCursorError();
  }
  try {
    const decoded = Buffer.from(raw, 'base64url').toString('utf8');
    const payload = JSON.parse(decoded) as { c?: string; i?: string };
    if (!payload.c || !payload.i || !isValidId(payload.i)) {
      throw new InvalidNotificationCursorError();
    }
    const createdAt = new Date(payload.c);
    if (Number.isNaN(createdAt.getTime())) {
      throw new InvalidNotificationCursorError();
    }
    return { createdAt, _id: payload.i };
  } catch (err) {
    if (err instanceof InvalidNotificationCursorError) {
      throw err;
    }
    throw new InvalidNotificationCursorError();
  }
}

export function toIdString(value: unknown): string | null {
  if (value == null || typeof (value as { toString?: () => string }).toString !== 'function') {
    return null;
  }
  const asString = String(value);
  return isValidId(asString) ? asString : null;
}

function toDate(value: unknown): Date | null {
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    return value;
  }
  if (typeof value === 'string' || typeof value === 'number') {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return date;
    }
  }
  return null;
}

export function paginateResults<T>(
  rows: T[],
  limit: number,
): PaginatedNotificationResult<T> {
  const hasMore = rows.length > limit;
  const notifications = hasMore ? rows.slice(0, limit) : rows;
  const last = notifications[notifications.length - 1] as
    | { createdAt?: unknown; _id?: unknown }
    | undefined;
  const createdAt = last ? toDate(last.createdAt) : null;
  const objectId = last ? toIdString(last._id) : null;
  const cursor =
    hasMore && createdAt && objectId
      ? encodeCursor({ createdAt, _id: objectId })
      : null;
  return { notifications, hasMore, cursor };
}
