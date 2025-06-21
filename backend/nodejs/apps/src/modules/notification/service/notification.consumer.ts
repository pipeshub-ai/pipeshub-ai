// consume the notification events from the kafka topic
// and save them to the database

import { BaseKafkaConsumerConnection } from '../../../libs/services/kafka.service';
import { KafkaConfig, KafkaMessage } from '../../../libs/types/kafka.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject } from 'inversify';
import { Notifications } from '../schema/notification.schema';

@injectable()
export class NotificationConsumer extends BaseKafkaConsumerConnection {
    constructor(@inject('KafkaConfig') config: KafkaConfig, @inject('Logger') logger: Logger) {
    super(config, logger);
  }

  async start(): Promise<void> {
    if (!this.isConnected()) {
      await super.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.isConnected()) {
      await super.disconnect();
    }
  }

  override async subscribe(
    topics: string[],
    fromBeginning = false,
  ): Promise<void> {
    if (this.isConnected()) {
      await super.subscribe(topics, fromBeginning);
    }
  }

  override async consume<INotification>(
    handler: (message: KafkaMessage<INotification>) => Promise<void>,
  ): Promise<void> {
    if (this.isConnected()) {
      // write the logic to save the notification to the database and pass the handler to the kafka consumer
      await super.consume(async (message: KafkaMessage<INotification>) => {
        await handler(message);
        await Notifications.create(message.value);
        this.logger.info('Notification saved to the database', message.value);
      });
    }
  }
}
