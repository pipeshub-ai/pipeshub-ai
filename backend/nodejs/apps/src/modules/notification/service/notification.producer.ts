import { injectable, inject } from 'inversify';
import { IMessageProducer, StreamMessage } from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { INotification } from '../schema/notification.schema';

export enum EventType {
    NewNotificationEvent = 'newNotification',
  }
  
  export interface Event {
    eventType: EventType;
    timestamp: number;
    payload: INotification;
  }


@injectable()
export class NotificationProducer {
  private readonly topic = 'notification';

  constructor(
    @inject('MessageProducer') private readonly producer: IMessageProducer,
    @inject('Logger') private readonly logger: Logger,
  ) {}

  async start(): Promise<void> {
    if (!this.producer.isConnected()) {
      await this.producer.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.producer.isConnected()) {
      await this.producer.disconnect();
    }
  }

  isConnected(): boolean {
    return this.producer.isConnected();
  }

  async publishEvent(event: Event): Promise<void> {
    const message: StreamMessage<INotification> = {
      key: event.payload.id,
      value: event.payload,
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.producer.publish(this.topic, message);
      this.logger.info(
        `Published event: ${event.eventType} to topic ${this.topic}`,
      );
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
  
}
