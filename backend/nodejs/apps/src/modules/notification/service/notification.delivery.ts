import { Logger } from '../../../libs/services/logger.service';
import { getNotificationRepository } from '../../../libs/repositories/notification';
import { forOrg } from '../../../libs/context/orgContext';
import { isNotYetVisible } from '../../../libs/repositories/notification/dates';
import { ValidationError } from '../../../libs/errors/validation.error';
import { metricsBackend } from '../../../libs/services/telemetry/metrics-backend';
import { NotificationService } from './notification.service';
import { resolveNotificationRecipientUserIds } from '../utils/notification-recipient.resolver';
import {
  NotificationBrokerMessage,
  buildNotificationDocForUser,
} from '../utils/notification-payload.resolver';

export const notificationEventRefused = metricsBackend.createCounter({
  name: 'notification_event_refused_total',
  help: 'Notification events refused before any write, by reason',
  labelNames: ['reason'],
});

/**
 * Stores one notification per recipient of *event* in its org and pushes each
 * to the user's open sockets. Returns how many were stored. Store errors are
 * thrown to the caller, which decides whether the message is redelivered.
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
        orgId: event.orgId,
        type: event.type,
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
    const savedDocs = await getNotificationRepository().insertMany(docs);

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
      orgId: event.orgId,
      type: event.type,
      recipientCount: dispatchedUserIds.length,
      notificationIds: savedIds,
      userIds: dispatchedUserIds,
    });
    return savedDocs.length;
  });
}
