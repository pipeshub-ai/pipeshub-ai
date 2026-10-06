import { ClientSession } from 'mongoose';
import { OutboxEvent } from '../../../../../libs/services/outbox/outbox.schema';
import { NotificationBrokerMessage } from '../../../../notification/utils/notification-payload.resolver';

const NOTIFICATION_TOPIC = 'notification';

export interface INotificationOutboxWriter {
  /** Throws on failure; with a session the rows commit or roll back with the caller's transaction. */
  write(
    messages: readonly NotificationBrokerMessage[],
    sessionId: string,
    opts?: { session?: ClientSession },
  ): Promise<void>;
}

/**
 * Rows of one chat share an ordering key, so the dispatcher delivers a chat's notifications in the
 * order they were written. The value is the JSON string the notification consumer parses.
 */
export class NotificationOutboxWriter implements INotificationOutboxWriter {
  async write(
    messages: readonly NotificationBrokerMessage[],
    sessionId: string,
    opts: { session?: ClientSession } = {},
  ): Promise<void> {
    if (messages.length === 0) {
      return;
    }
    await OutboxEvent.create(
      messages.map((message) => ({
        topic: NOTIFICATION_TOPIC,
        key: message.type,
        orderingKey: `chat:${sessionId}`,
        value: JSON.stringify(message),
        status: 'pending' as const,
        attempts: 0,
        nextAttemptAt: new Date(),
      })),
      opts.session ? { session: opts.session, ordered: true } : {},
    );
  }
}
