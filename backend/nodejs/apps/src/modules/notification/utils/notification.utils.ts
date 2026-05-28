import mongoose from 'mongoose';

export const NOTIFICATION_RETENTION_DAYS = 30;
export const DEFAULT_PAGE_SIZE = 20;
export const MAX_PAGE_SIZE = 50;

const RETENTION_SECONDS = NOTIFICATION_RETENTION_DAYS * 24 * 60 * 60;

export interface NotificationCursor {
  createdAt: Date;
  _id: mongoose.Types.ObjectId;
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

export function buildRetentionFilter(userOid: mongoose.Types.ObjectId, notificationStatus: string | null): Record<string, unknown> {
  return {
    assignedTo: userOid,
    isDeleted: false,
    createdAt: { $gte: retentionCutoff() },
    ...(notificationStatus ? { status: notificationStatus } : {}),
  };
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
    i: cursor._id.toString(),
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
    if (!payload.c || !payload.i || !mongoose.isValidObjectId(payload.i)) {
      throw new InvalidNotificationCursorError();
    }
    const createdAt = new Date(payload.c);
    if (Number.isNaN(createdAt.getTime())) {
      throw new InvalidNotificationCursorError();
    }
    return {
      createdAt,
      _id: new mongoose.Types.ObjectId(payload.i),
    };
  } catch (err) {
    if (err instanceof InvalidNotificationCursorError) {
      throw err;
    }
    throw new InvalidNotificationCursorError();
  }
}

export function buildCursorFilter(cursor: NotificationCursor): Record<string, unknown> {
  return {
    $or: [
      { createdAt: { $lt: cursor.createdAt } },
      { createdAt: cursor.createdAt, _id: { $lt: cursor._id } },
    ],
  };
}

function toObjectId(value: unknown): mongoose.Types.ObjectId | null {
  if (value instanceof mongoose.Types.ObjectId) {
    return value;
  }
  if (typeof value === 'string' && mongoose.isValidObjectId(value)) {
    return new mongoose.Types.ObjectId(value);
  }
  if (value != null && typeof (value as { toString?: () => string }).toString === 'function') {
    const asString = String(value);
    if (mongoose.isValidObjectId(asString)) {
      return new mongoose.Types.ObjectId(asString);
    }
  }
  return null;
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
  const objectId = last ? toObjectId(last._id) : null;
  const cursor =
    hasMore && createdAt && objectId
      ? encodeCursor({ createdAt, _id: objectId })
      : null;
  return { notifications, hasMore, cursor };
}
