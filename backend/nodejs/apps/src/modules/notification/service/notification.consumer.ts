import {
  IMessageConsumer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject } from 'inversify';
import { getNotificationRepository } from '../../../libs/repositories/notification';
import { forOrg } from '../../../libs/context/orgContext';
import {
  DuplicateEnvelope,
  withDeliveryRecorded,
} from '../../../libs/context/envelope';
import { isRetryableStoreError } from '../../../libs/repositories/errors';
import { isNotYetVisible } from '../../../libs/repositories/notification/dates';
import { ValidationError } from '../../../libs/errors/validation.error';
import { metricsBackend } from '../../../libs/services/telemetry/metrics-backend';
import { NotificationService } from './notification.service';
import { resolveNotificationRecipientUserIds } from '../utils/notification-recipient.resolver';
import {
  buildNotificationDocForUser,
  toBrokerMessage,
} from '../utils/notification-payload.resolver';

// A notification type is a short dotted token (`mail.deliveryFailed`); anything else is not printed.
const NOTIFICATION_TYPE = /^[A-Za-z0-9._-]{1,64}$/;

/** What may be logged about a message: its type and size, never its value (it can quote recipients and error text). */
function messageFacts(value: unknown): { type?: string; size_bytes?: number } {
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

@injectable()
export class NotificationConsumer {
  constructor(
    @inject('MessageConsumer') private readonly consumer: IMessageConsumer,
    @inject('Logger') private readonly logger: Logger,
    @inject(NotificationService)
    private readonly notificationService: NotificationService,
  ) {}

  async start(): Promise<void> {
    if (!this.consumer.isConnected()) {
      await this.consumer.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.disconnect();
    }
  }

  isConnected(): boolean {
    return this.consumer.isConnected();
  }

  async subscribe(topics: string[], fromBeginning = false): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.subscribe(topics, fromBeginning);
    }
  }

  async consume<INotification>(
    handler: (message: StreamMessage<INotification>) => Promise<void>,
  ): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.consume(
        async (message: StreamMessage<INotification>) => {
          let redeliver = false;
          try {
            const event = toBrokerMessage(message.value);
            if (!event) {
              this.logger.warn(
                'Notification event skipped: invalid orgId or type',
                messageFacts(message.value),
              );
              return;
            }

            // The org comes from the event, bound before any repository is touched.
            await forOrg(event.orgId, 'notification-consumer', async () => {
              const recipientUserIds =
                await resolveNotificationRecipientUserIds(
                  event.orgId,
                  event.recipientUserIds,
                  event.recipientRoles,
                );

              if (recipientUserIds.length === 0) {
                this.logger.warn('Notification event skipped: no recipients', {
                  org_id: event.orgId,
                  ...messageFacts(event),
                });
                return;
              }

              // A supplied id names one stored row; copied to several recipients it would reach only the first. Refuse the whole event.
              if (event._id != null && recipientUserIds.length > 1) {
                notificationEventRefused.inc({
                  reason: 'id_with_many_recipients',
                });
                throw new ValidationError('Notification event refused', [
                  {
                    field: '_id',
                    code: 'id_with_many_recipients',
                    message: `an event that carries _id must have exactly one recipient, got ${recipientUserIds.length}`,
                  },
                ]);
              }

              const docs = recipientUserIds.map((userOid) =>
                buildNotificationDocForUser(event, String(userOid)),
              );
              // On PostgreSQL the rows and the inbox record commit together, so a redelivery cannot store them twice.
              const savedDocs = await withDeliveryRecorded(() =>
                getNotificationRepository().insertMany(docs),
              );

              const savedIds: string[] = [];
              const dispatchedUserIds: string[] = [];

              for (const saved of savedDocs) {
                // stored, but a scheduled one is not pushed now and not queued; it is read from the list at its time
                if (isNotYetVisible(saved.visibleAt)) continue;
                const userId = saved.assignedTo;
                this.notificationService.sendToUser(
                  userId,
                  'newNotification',
                  saved,
                );
                savedIds.push(saved._id);
                dispatchedUserIds.push(userId);
              }

              this.logger.debug('Notification saved and dispatched', {
                org_id: event.orgId,
                ...messageFacts(event),
                recipientCount: dispatchedUserIds.length,
                notificationIds: savedIds,
              });
            });
          } catch (error) {
            // A store that is frozen or away is not a bad message: let the broker deliver it again.
            if (isRetryableStoreError(error)) {
              redeliver = true;
              throw error;
            }
            // Another delivery of the event stored it first: the consumer acknowledges this one.
            if (error instanceof DuplicateEnvelope) throw error;
            // A refused event is the producer's fault (WARNING); anything else is ours.
            const level = error instanceof ValidationError ? 'warn' : 'error';
            this.logger[level]('Failed to process notification message', {
              ...messageFacts(message.value),
              error,
            });
          } finally {
            if (!redeliver) await handler(message);
          }
        },
      );
    } else {
      this.logger.error(
        'Cannot consume notifications: MessageConsumer is not connected',
      );
      throw new Error('MessageConsumer is not connected');
    }
  }
}
