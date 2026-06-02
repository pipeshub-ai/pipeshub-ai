import { IMessageConsumer, StreamMessage } from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject } from 'inversify';
import mongoose from 'mongoose';
import { Notifications } from '../schema/notification.schema';
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

  async subscribe(
    topics: string[],
    fromBeginning = false,
  ): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.subscribe(topics, fromBeginning);
    }
  }

  async consume<INotification>(
    handler: (message: StreamMessage<INotification>) => Promise<void>,
  ): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.consume(async (message: StreamMessage<INotification>) => {
        const event = toBrokerMessage(message.value);
        if (!event) {
          this.logger.warn('Notification event skipped: invalid orgId or type', {
            value: message.value,
          });
          await handler(message);
          return;
        }

        const orgOid = new mongoose.Types.ObjectId(String(event.orgId));
        let recipientUserIds = await resolveNotificationRecipientUserIds(
          orgOid,
          event.recipientUserIds,
          event.recipientRoles,
        );

        if (recipientUserIds.length === 0) {
          this.logger.warn('Notification event skipped: no recipients', {
            orgId: event.orgId,
            type: event.type,
          });
          await handler(message);
          return;
        }

        const savedIds: string[] = [];
        const dispatchedUserIds: string[] = [];

        for (const userOid of recipientUserIds) {
          const doc = buildNotificationDocForUser(event, userOid);
          const saved = await Notifications.create(doc);
          const userId = String(saved.assignedTo);
          const payload =
            typeof (saved as { toObject?: () => object }).toObject === 'function'
              ? (saved as { toObject: () => object }).toObject()
              : saved;
          this.notificationService.sendToUser(userId, 'newNotification', payload);
          savedIds.push(String(saved._id));
          dispatchedUserIds.push(userId);
        }

        this.logger.info('Notification saved and dispatched', {
          orgId: event.orgId,
          type: event.type,
          recipientCount: dispatchedUserIds.length,
          notificationIds: savedIds,
          userIds: dispatchedUserIds,
        });
        await handler(message);
      });
    } else {
      this.logger.error('Cannot consume notifications: MessageConsumer is not connected');
      throw new Error('MessageConsumer is not connected');
    }
  }
}
