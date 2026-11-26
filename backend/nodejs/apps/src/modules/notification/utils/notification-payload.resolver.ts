import { isValidId } from '../../../libs/repositories/ids';
import type { NewNotification } from '../../../libs/repositories/notification';
import { orgContextStorage } from '../../../libs/context/orgContext';

/** Fields only present on the Kafka event; not stored on per-user notification docs. */
const BROKER_ONLY_FIELDS = new Set(['recipientUserIds', 'recipientRoles']);

export interface NotificationBrokerMessage {
  orgId: string;
  type: string;
  /** A supplied id: the stored row keeps it, so the event may name only one recipient. */
  _id?: string;
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

/**
 * The notification a consumed message carries. A producer that signs puts the whole notification under
 * `payload.notification` (the part the signature covers) and every top-level field is ignored, so a writer to the
 * broker cannot change what a signed notification says, who gets it or which org it names. A message without it is
 * the older shape (fields at the top level), read as before; `signedLegacy` is true when such a message was signed,
 * since its top-level fields are not covered.
 */
export function notificationOf(raw: unknown): { value: unknown; signedLegacy: boolean } {
  if (raw == null || typeof raw !== 'object') return { value: raw, signedLegacy: false };
  const payload = (raw as Record<string, unknown>).payload;
  if (payload != null && typeof payload === 'object' && !Array.isArray(payload)) {
    const inner = (payload as Record<string, unknown>).notification;
    if (inner != null && typeof inner === 'object' && !Array.isArray(inner)) {
      return { value: inner, signedLegacy: false };
    }
  }
  return { value: raw, signedLegacy: currentEnvelopeOrg() !== null };
}

/** The org a verified envelope bound for this delivery, or null (unsigned message, or no delivery). */
export function currentEnvelopeOrg(): string | null {
  const ctx = orgContextStorage.getStore();
  return ctx?.source === 'envelope' && ctx.orgId ? ctx.orgId : null;
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
