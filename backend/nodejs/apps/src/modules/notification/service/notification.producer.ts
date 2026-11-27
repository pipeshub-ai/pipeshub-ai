import { injectable, inject } from 'inversify';
import { IMessageProducer, StreamMessage } from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { signEnabled } from '../../../libs/context/envelope';

/** What a notification event carries; `id`, when set, is the broker key. */
export interface NotificationEventPayload {
  id?: string;
}

export enum EventType {
    NewNotificationEvent = 'newNotification',
  }
  
  export interface Event {
    eventType: EventType;
    timestamp: number;
    payload: NotificationEventPayload;
  }


/**
 * What goes on the notification topic for *doc*. With `ENVELOPE_SIGN` on: `{ payload: { notification, orgId } }`, since
 * a signature covers only `payload` and `NotificationConsumer` reads the notification from there (ignoring the top
 * level). With signing off: the notification itself, as before. Publish it with the notification's org bound.
 */
export function notificationMessage(doc: object): Record<string, unknown> {
  if (!signEnabled()) return doc as Record<string, unknown>;
  const orgId = (doc as { orgId?: { toString(): string } | null }).orgId;
  return {
    payload: {
      notification: doc,
      orgId: orgId == null ? orgId : orgId.toString(),
    },
  };
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

  /**
   * With `ENVELOPE_SIGN` on, the notification goes out as `{ payload: { notification, orgId } }`: the signature covers
   * only `payload`, so the whole notification has to be inside it (`NotificationConsumer` reads it from there and
   * ignores the top level). With signing off it is sent as before.
   */
  async publishEvent(event: Event): Promise<void> {
    const value = notificationMessage(event.payload);
    const message: StreamMessage<Record<string, unknown>> = {
      key: event.payload.id as string, // the broker key, when the payload carries an id
      value,
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.producer.publish(this.topic, message);
      this.logger.debug('broker.event.published', {
        event_type: event.eventType,
        topic: this.topic,
      });
    } catch (error) {
      this.logger.error('broker.event.publish_failed', {
        event_type: event.eventType,
        topic: this.topic,
        error,
      });
    }
  }
  
}
