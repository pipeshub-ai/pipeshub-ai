import { inject, injectable } from 'inversify';
import {
  BrokerTopic,
  IMessageProducer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { signEnabled } from '../../../libs/context/envelope';
import { MailEvent, MailEventMessage } from '../types/mail-event.types';

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
   * Rethrows, unlike NotificationProducer: swallowing would drop the email.
   *
   * With `ENVELOPE_SIGN` on, the job goes out as `{ eventId, payload: job }`: only a message with a `payload` object can be
   * signed (under the org bound where it is published), and a consumer that enforces the envelope drops an unsigned one.
   * With signing off the job stays flat, as before. `MailConsumer` reads both shapes.
   */
  async publishEvent(event: MailEvent): Promise<void> {
    const job = event.payload;
    const value: MailEventMessage = signEnabled()
      ? { ...(job.eventId ? { eventId: job.eventId } : {}), payload: job }
      : job;
    const message: StreamMessage<MailEventMessage> = {
      key: job.orgId ?? job.mail.emailTemplateType,
      value,
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    // Another module's stop() may have disconnected the shared producer.
    await this.start();
    await this.producer.publish(this.topic, message);
    this.logger.debug('broker.event.published', {
      event_type: event.eventType,
      topic: this.topic,
    });
  }
}
