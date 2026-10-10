import mongoose from 'mongoose';
import type { EmailIntent } from '../service/notification-email.dispatcher';

/**
 * Fields only present on the Kafka event; not stored on per-user notification docs.
 * `coalesceKey` is deliberately absent: the unique partial index on it needs it stored.
 */
const BROKER_ONLY_FIELDS = new Set([
  'recipientUserIds',
  'recipientRoles',
  'emailIntent',
]);

export interface NotificationBrokerMessage {
  /** Broker partition key; absent means no key. */
  messageKey?: string;
  orgId: string;
  type: string;
  severity?: string;
  status?: string;
  originService?: string;
  title?: string;
  message?: string;
  redirectLink?: string;
  payload?: Record<string, unknown>;
  recipientUserIds?: string[];
  recipientRoles?: string[];
  /** Per recipient; the unique index is on `{assignedTo, dedupeKey}`. */
  dedupeKey?: string;
  /** At most one unread doc per recipient and key; later events increment `payload.count`. */
  coalesceKey?: string;
  emailIntent?: EmailIntent;
  isDeleted?: boolean;
  /** @deprecated Legacy single-assignee events */
  assignedTo?: string | string[];
}

/** The outbox republishes `JSON.stringify(event)`, so Kafka and Redis consumers receive a string. */
function parseJsonObject(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function toBrokerMessage(
  raw: unknown,
): NotificationBrokerMessage | null {
  const value = typeof raw === 'string' ? parseJsonObject(raw) : raw;
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const msg = value as Record<string, unknown>;
  const orgId = msg.orgId != null ? String(msg.orgId) : '';
  const type = typeof msg.type === 'string' ? msg.type : '';
  if (!mongoose.isValidObjectId(orgId) || !type) {
    return null;
  }
  return msg as unknown as NotificationBrokerMessage;
}

/**
 * What may be logged about a broker message that failed: identifiers only. The value itself can
 * carry a user's share note, so it never goes to a log.
 */
export function brokerMessageLogMeta(raw: unknown): {
  type?: string;
  dedupeKey?: string;
  orgId?: string;
  invalidPaths: string[];
} {
  const value = typeof raw === 'string' ? parseJsonObject(raw) : raw;
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    return { invalidPaths: ['value'] };
  }
  const msg = value as Record<string, unknown>;
  const text = (v: unknown): string | undefined =>
    typeof v === 'string' ? v.slice(0, 128) : undefined;
  const invalidPaths: string[] = [];
  if (!mongoose.isValidObjectId(text(msg.orgId) ?? '')) {
    invalidPaths.push('orgId');
  }
  if (typeof msg.type !== 'string' || msg.type === '') {
    invalidPaths.push('type');
  }
  return {
    type: text(msg.type),
    dedupeKey: text(msg.dedupeKey),
    orgId: text(msg.orgId),
    invalidPaths,
  };
}

/**
 * Extracts user ObjectIds from the legacy `assignedTo` field on a broker event.
 * Returns an empty array when the field is absent.
 */
export function getLegacyAssignedToUserIds(
  event: NotificationBrokerMessage,
): mongoose.Types.ObjectId[] {
  if (!event.assignedTo) return [];
  const raw = Array.isArray(event.assignedTo)
    ? event.assignedTo
    : [event.assignedTo];
  return raw
    .filter((id) => mongoose.isValidObjectId(id))
    .map((id) => new mongoose.Types.ObjectId(String(id)));
}

export function buildNotificationDocForUser(
  event: NotificationBrokerMessage,
  assignedTo: mongoose.Types.ObjectId,
): Record<string, unknown> {
  const doc: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(event)) {
    if (!BROKER_ONLY_FIELDS.has(key)) {
      doc[key] = value;
    }
  }
  doc.orgId = new mongoose.Types.ObjectId(String(event.orgId));
  doc.assignedTo = assignedTo;
  doc.status = doc.status ?? 'unread';
  doc.isDeleted = false;
  return doc;
}
