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

  /**
   * Unlike the notification producer this rethrows: the caller reports the
   * failure to the user, and swallowing it would silently drop the email.
   */
  async publishEvent(event: MailEvent): Promise<void> {
    const message: StreamMessage<MailEventPayload> = {
      key: event.payload.orgId ?? event.payload.mail.emailTemplateType,
      value: event.payload,
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    // A shared singleton producer can be disconnected by another module's
    // stop(); start() is idempotent and reconnects so the job is not lost.
    await this.start();
    await this.producer.publish(this.topic, message);
    this.logger.info(
      `Published event: ${event.eventType} to topic ${this.topic}`,
    );
  }
}
