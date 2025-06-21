import { KafkaConfig, KafkaMessage } from "../../../libs/types/kafka.types";
import { injectable } from "inversify";
import { BaseKafkaProducerConnection } from "../../../libs/services/kafka.service";
import { Logger } from "../../../libs/services/logger.service";
import { INotification } from "../schema/notification.schema";

export enum EventType {
    NewNotificationEvent = 'newNotification',
  }
  
  export interface Event {
    eventType: EventType;
    timestamp: number;
    payload: INotification;
  }


@injectable()
export class NotificationProducer extends BaseKafkaProducerConnection {
  private readonly topic = 'notification';

  constructor(config: KafkaConfig, logger: Logger) {
    super(config, logger);
  }

  async start(): Promise<void> {
    if (!this.isConnected()) {
      await this.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.isConnected()) {
      await this.disconnect();
    }
  }

  async publishEvent(event: Event): Promise<void> {
    const message: KafkaMessage<INotification> = {
      key: event.payload.id,
      value: event.payload,
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.publish(this.topic, message);
      this.logger.info(
        `Published event: ${event.eventType} to topic ${this.topic}`,
      );
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
  
}