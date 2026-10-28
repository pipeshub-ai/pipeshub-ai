import { isValidId } from '../../../libs/repositories/ids';
import type { NewNotification } from '../../../libs/repositories/notification';

/** Fields only present on the Kafka event; not stored on per-user notification docs. */
const BROKER_ONLY_FIELDS = new Set(['recipientUserIds', 'recipientRoles']);

export interface NotificationBrokerMessage {
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
  isDeleted?: boolean;
  /** @deprecated Legacy single-assignee events */
  assignedTo?: string | string[];
}

export function toBrokerMessage(raw: unknown): NotificationBrokerMessage | null {
  if (raw == null || typeof raw !== 'object') {
    return null;
  }
  const msg = raw as Record<string, unknown>;
  const orgId = msg.orgId != null ? String(msg.orgId) : '';
  const type = typeof msg.type === 'string' ? msg.type : '';
  if (!isValidId(orgId) || !type) {
    return null;
  }
  return msg as unknown as NotificationBrokerMessage;
}

/**
 * Extracts user ids from the legacy `assignedTo` field on a broker event.
 * Returns an empty array when the field is absent.
 */
export function getLegacyAssignedToUserIds(
  event: NotificationBrokerMessage,
): string[] {
  if (!event.assignedTo) return [];
  const raw = Array.isArray(event.assignedTo) ? event.assignedTo : [event.assignedTo];
  return raw.filter((id) => isValidId(id)).map((id) => String(id));
}

export function buildNotificationDocForUser(
  event: NotificationBrokerMessage,
  assignedTo: string,
): NewNotification {
  const doc: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(event)) {
    if (!BROKER_ONLY_FIELDS.has(key)) {
      doc[key] = value;
    }
  }
  doc.orgId = String(event.orgId);
  doc.assignedTo = assignedTo;
  doc.status = doc.status ?? 'unread';
  doc.isDeleted = false;
  return doc as NewNotification;
}
