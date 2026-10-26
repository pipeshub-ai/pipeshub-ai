import {
  IMessageConsumer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject } from 'inversify';
import { getNotificationRepository } from '../../../libs/repositories/notification';
import { NotificationService } from './notification.service';
import { resolveNotificationRecipientUserIds } from '../utils/notification-recipient.resolver';
import {
  buildNotificationDocForUser,
  toBrokerMessage,
} from '../utils/notification-payload.resolver';

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
          try {
            const event = toBrokerMessage(message.value);
            if (!event) {
              this.logger.warn(
                'Notification event skipped: invalid orgId or type',
                {
                  value: message.value,
                },
              );
              return;
            }

            const recipientUserIds = await resolveNotificationRecipientUserIds(
              event.orgId,
              event.recipientUserIds,
              event.recipientRoles,
            );

            if (recipientUserIds.length === 0) {
              this.logger.warn('Notification event skipped: no recipients', {
                orgId: event.orgId,
                type: event.type,
              });
              return;
            }

            const docs = recipientUserIds.map((userOid) =>
              buildNotificationDocForUser(event, String(userOid)),
            );
            const savedDocs = await getNotificationRepository().insertMany(docs);

            const savedIds: string[] = [];
            const dispatchedUserIds: string[] = [];

            for (const saved of savedDocs) {
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
              orgId: event.orgId,
              type: event.type,
              recipientCount: dispatchedUserIds.length,
              notificationIds: savedIds,
              userIds: dispatchedUserIds,
            });
          } catch (error) {
            this.logger.error('Failed to process notification message', {
              error: error instanceof Error ? error.message : String(error),
              messageValue: message.value,
            });
          } finally {
            await handler(message);
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
