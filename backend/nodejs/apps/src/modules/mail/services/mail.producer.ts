import { inject, injectable } from 'inversify';
import {
  BrokerTopic,
  IMessageProducer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { MailEvent, MailEventPayload } from '../types/mail-event.types';

/** Publishes mail jobs so delivery happens off the request path. */
@injectable()
export class MailProducer {
  private readonly topic = BrokerTopic.MAIL_EVENTS;

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

  /** Rethrows, unlike NotificationProducer: swallowing would drop the email. */
  async publishEvent(event: MailEvent): Promise<void> {
    const message: StreamMessage<MailEventPayload> = {
      key: event.payload.orgId ?? event.payload.mail.emailTemplateType,
      value: event.payload,
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    // Another module's stop() may have disconnected the shared producer.
    await this.start();
    await this.producer.publish(this.topic, message);
    this.logger.info(
      `Published event: ${event.eventType} to topic ${this.topic}`,
    );
  }
}
