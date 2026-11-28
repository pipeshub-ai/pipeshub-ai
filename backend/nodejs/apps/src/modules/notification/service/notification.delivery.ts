import { Logger } from '../../../libs/services/logger.service';
import { getNotificationRepository } from '../../../libs/repositories/notification';
import { forOrg } from '../../../libs/context/orgContext';
import { withDeliveryRecorded } from '../../../libs/context/envelope';
import { isNotYetVisible } from '../../../libs/repositories/notification/dates';
import { ValidationError } from '../../../libs/errors/validation.error';
import { metricsBackend } from '../../../libs/services/telemetry/telemetry-impl';
import { NotificationService } from './notification.service';
import { resolveNotificationRecipientUserIds } from '../utils/notification-recipient.resolver';
import {
  NotificationBrokerMessage,
  buildNotificationDocForUser,
} from '../utils/notification-payload.resolver';

// A notification type is a short dotted token (`mail.deliveryFailed`); anything else is not printed.
const NOTIFICATION_TYPE = /^[A-Za-z0-9._-]{1,64}$/;

/** What may be logged about a message: its type and size, never its value (it can quote recipients and error text). */
export function messageFacts(value: unknown): {
  type?: string;
  size_bytes?: number;
} {
  const type = (value as { type?: unknown } | null)?.type;
  let size: number | undefined;
  try {
    // undefined for a value JSON cannot represent, whatever the declared type says
    const text = JSON.stringify(value) as string | undefined;
    size = text === undefined ? undefined : Buffer.byteLength(text);
  } catch {
    size = undefined;
  }
  return {
    ...(typeof type === 'string' && NOTIFICATION_TYPE.test(type) && { type }),
    ...(size !== undefined && { size_bytes: size }),
  };
}

export const notificationEventRefused = metricsBackend.createCounter({
  name: 'notification_event_refused_total',
  help: 'Notification events refused before any write, by reason',
  labelNames: ['reason'],
});

/**
 * Stores one notification per recipient of *event* in its org and pushes each
 * to the user's open sockets. Returns how many were stored. Store errors are
 * thrown to the caller, which decides whether the message is redelivered.
 * Throws DuplicateEnvelope when this delivery's event was already stored for
 * the org (the durable consumer inbox).
 */
export function deliverToOrg(
  event: NotificationBrokerMessage,
  notificationService: NotificationService,
  logger: Logger,
): Promise<number> {
  // The org comes from the event, bound before any repository is touched.
  return forOrg(event.orgId, 'notification-consumer', async () => {
    const recipientUserIds = await resolveNotificationRecipientUserIds(
      event.orgId,
      event.recipientUserIds,
      event.recipientRoles,
    );

    if (recipientUserIds.length === 0) {
      logger.warn('Notification event skipped: no recipients', {
        org_id: event.orgId,
        ...messageFacts(event),
      });
      return 0;
    }

    // A supplied id names one stored row; copied to several recipients it would reach only the first. Refuse the whole event.
    if (event._id != null && recipientUserIds.length > 1) {
      notificationEventRefused.inc({ reason: 'id_with_many_recipients' });
      throw new ValidationError('Notification event refused', [
        {
          field: '_id',
          code: 'id_with_many_recipients',
          message: `an event that carries _id must have exactly one recipient, got ${recipientUserIds.length}`,
        },
      ]);
    }

    const docs = recipientUserIds.map((userOid) =>
      buildNotificationDocForUser(event, userOid),
    );
    // On PostgreSQL the rows and the inbox record for this org commit together, so a redelivery cannot store them twice.
    const savedDocs = await withDeliveryRecorded(
      () => getNotificationRepository().insertMany(docs),
      event.orgId,
    );

    const savedIds: string[] = [];
    const dispatchedUserIds: string[] = [];

    for (const saved of savedDocs) {
      // stored, but a scheduled one is not pushed now and not queued; it is read from the list at its time
      if (isNotYetVisible(saved.visibleAt)) continue;
      const userId = saved.assignedTo;
      notificationService.sendToUser(userId, 'newNotification', saved);
      savedIds.push(saved._id);
      dispatchedUserIds.push(userId);
    }

    logger.debug('Notification saved and dispatched', {
      org_id: event.orgId,
      ...messageFacts(event),
      recipientCount: dispatchedUserIds.length,
      notificationIds: savedIds,
    });
    return savedDocs.length;
  });
}
