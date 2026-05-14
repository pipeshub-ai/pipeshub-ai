import { IMessageConsumer, StreamMessage } from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject } from 'inversify';
import { Notifications } from '../schema/notification.schema';
import { NotificationService } from './notification.service';

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
        const saved = await Notifications.create(message.value);
        const userId = String(saved.assignedTo);
        const payload =
          typeof (saved as { toObject?: () => object }).toObject === 'function'
            ? (saved as { toObject: () => object }).toObject()
            : saved;
        this.notificationService.sendToUser(userId, 'newNotification', payload);
        this.logger.info('Notification saved and dispatched', {
          id: String(saved._id),
          userId,
        });
        await handler(message);
      });
    }
  }
}
