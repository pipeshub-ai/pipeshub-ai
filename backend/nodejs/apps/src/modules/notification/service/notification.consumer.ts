import { randomUUID } from 'crypto';
import {
  BrokerTopic,
  IMessageConsumer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { OutboxEvent } from '../../../libs/services/outbox/outbox.schema';
import { injectable, inject } from 'inversify';
import mongoose from 'mongoose';
import { INotification, Notifications } from '../schema/notification.schema';
import { NotificationService } from './notification.service';
import {
  INotificationEmailDispatcher,
  NOTIFICATION_EMAIL_DISPATCHER,
} from './notification-email.port';
import {
  DeliveryPlan,
  INotificationDeliveryPolicy,
  NOTIFICATION_DELIVERY_POLICY,
  RetryableDeliveryError,
  deliverToRecipients,
} from './notification-delivery.port';
import { resolveNotificationRecipientUserIds } from '../utils/notification-recipient.resolver';
import {
  buildNotificationDocForUser,
  NotificationBrokerMessage,
  brokerMessageLogMeta,
  toBrokerMessage,
} from '../utils/notification-payload.resolver';

const DUPLICATE_KEY_CODE = 11000;

/** In-process retries stop after this; it outlasts a primary election. */
const RETRY_WINDOW_MS = 30_000;
const FIRST_RETRY_DELAY_MS = 1_000;
/** Times one event may go back on the outbox before it is dropped. */
const MAX_REDELIVERIES = 8;

interface InsertManyFailure {
  code?: number;
  writeErrors?: { code?: number; err?: { code?: number } }[];
  insertedDocs?: INotification[];
}

// Mongoose copies `insertMany` write errors as plain objects, losing the
// driver's `code` getter, so the code is only on the nested `err`.
const isDuplicateKey = (error: unknown): boolean => {
  if (typeof error !== 'object' || error === null) return false;
  const e = error as { code?: unknown; err?: { code?: unknown } };
  return (e.code ?? e.err?.code) === DUPLICATE_KEY_CODE;
};

const sessionIdOf = (payload: INotification['payload']): string | undefined =>
  typeof payload?.sessionId === 'string' ? payload.sessionId : undefined;

const toPlainObject = (doc: INotification): object =>
  typeof doc.toObject === 'function' ? doc.toObject<object>() : doc;

/** The stored row plus the policy's push-only fields. */
const pushed = async (
  saved: INotification,
  plan: DeliveryPlan,
): Promise<object> => ({
  ...toPlainObject(saved),
  ...(await plan.pushExtras?.(saved)),
});

/** A database or policy failure that may pass; any other error would recur. */
const isTransient = (error: unknown): boolean =>
  error instanceof RetryableDeliveryError ||
  error instanceof mongoose.mongo.MongoError ||
  // An operation buffered while the connection is down times out as a plain MongooseError.
  (error instanceof mongoose.Error && error.name === 'MongooseError');

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

@injectable()
export class NotificationConsumer {
  constructor(
    @inject('MessageConsumer') private readonly consumer: IMessageConsumer,
    @inject('Logger') private readonly logger: Logger,
    @inject(NotificationService)
    private readonly notificationService: NotificationService,
    @inject(NOTIFICATION_EMAIL_DISPATCHER)
    private readonly emailDispatcher: INotificationEmailDispatcher,
    @inject(NOTIFICATION_DELIVERY_POLICY)
    private readonly deliveryPolicy: INotificationDeliveryPolicy = deliverToRecipients,
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

  async subscribe(topics: string[], fromBeginning = false): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.subscribe(topics, fromBeginning);
    }
  }

  async consume<INotification>(
    handler: (message: StreamMessage<INotification>) => Promise<void>,
  ): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.consume(
        async (message: StreamMessage<INotification>) => {
          try {
            const event = toBrokerMessage(message.value);
            if (!event) {
              this.logger.warn(
                'Notification event skipped: invalid orgId or type',
                brokerMessageLogMeta(message.value),
              );
              return;
            }
            await this.deliverWithRetry(event, message.key);
          } finally {
            await handler(message);
          }
        },
      );
    } else {
      this.logger.error(
        'Cannot consume notifications: MessageConsumer is not connected',
      );
      throw new Error('MessageConsumer is not connected');
    }
  }

  /**
   * A transient failure is retried here for about 30 s, then the event goes
   * back on the outbox; any other failure is logged and the event dropped.
   * Only a failed re-queue throws, so the broker keeps the message.
   */
  private async deliverWithRetry(
    event: NotificationBrokerMessage,
    key: string | undefined,
  ): Promise<void> {
    const deadline = Date.now() + RETRY_WINDOW_MS;
    for (let delayMs = FIRST_RETRY_DELAY_MS; ; delayMs *= 2) {
      try {
        await this.deliver(event);
        return;
      } catch (error) {
        const waitMs = Math.min(delayMs, deadline - Date.now());
        if (isTransient(error) && waitMs > 0) {
          await sleep(waitMs);
          continue;
        }
        const meta = {
          error: error instanceof Error ? error.message : String(error),
          ...brokerMessageLogMeta(event),
        };
        if (isTransient(error)) {
          await this.requeue(event, key, meta);
        } else {
          this.logger.error('Failed to process notification message', meta);
        }
        return;
      }
    }
  }

  /**
   * Writes the event back to the outbox under a key of its own, so it holds
   * up nothing else.
   */
  private async requeue(
    event: NotificationBrokerMessage,
    key: string | undefined,
    meta: Record<string, unknown>,
  ): Promise<void> {
    const redelivery = (event.redelivery ?? 0) + 1;
    if (redelivery > MAX_REDELIVERIES) {
      this.logger.error('Notification dropped after retries', {
        ...meta,
        redelivery: event.redelivery ?? 0,
      });
      return;
    }
    await OutboxEvent.create({
      topic: BrokerTopic.NOTIFICATION,
      key: key !== undefined && key !== '' ? key : event.type,
      orderingKey: `redeliver:${randomUUID()}`,
      value: JSON.stringify({ ...event, redelivery }),
      nextAttemptAt: new Date(Date.now() + 1000 * 2 ** redelivery),
    });
    this.logger.warn('Notification re-queued after a transient failure', {
      ...meta,
      redelivery,
    });
  }

  private async deliver(event: NotificationBrokerMessage): Promise<void> {
    const orgOid = new mongoose.Types.ObjectId(event.orgId);
    const recipientUserIds = await resolveNotificationRecipientUserIds(
      orgOid,
      event.recipientUserIds,
      event.recipientRoles,
    );
    const plan = await this.deliveryPolicy.plan(event, recipientUserIds);

    if (plan.userIds.length === 0) {
      this.logger.warn('Notification event skipped: no recipients', {
        orgId: event.orgId,
        type: event.type,
      });
      return;
    }

    const docs = plan.userIds.map((userOid) => {
      const doc = buildNotificationDocForUser(event, userOid);
      if (plan.readFor?.has(String(userOid))) {
        doc.status = 'read';
      }
      return doc;
    });
    const created = await this.insertNew(docs);

    const dispatchedUserIds: string[] = [];
    for (const saved of created) {
      const userId = String(saved.assignedTo);
      this.notificationService.sendToUser(
        userId,
        'newNotification',
        await pushed(saved, plan),
      );
      dispatchedUserIds.push(userId);
    }
    const { emailIntent } = event;
    if (emailIntent) {
      await Promise.all(
        created.map((saved) =>
          this.emailDispatcher.dispatch({
            orgId: saved.orgId,
            assignedTo: saved.assignedTo,
            dedupeKey: saved.dedupeKey,
            redirectLink: saved.redirectLink,
            emailIntent,
            notification: {
              id: String(saved._id),
              type: saved.type,
              sessionId: sessionIdOf(saved.payload),
            },
          }),
        ),
      );
    }

    this.logger.debug('Notification saved and dispatched', {
      orgId: event.orgId,
      type: event.type,
      recipientCount: dispatchedUserIds.length,
      notificationIds: created.map((saved) => String(saved._id)),
      userIds: dispatchedUserIds,
    });
  }

  /** Returns the docs that were newly inserted; a duplicate-key doc was delivered before and is dropped. */
  private async insertNew(
    allDocs: Record<string, unknown>[],
  ): Promise<INotification[]> {
    const docs = this.withoutInvalid(allDocs);
    if (docs.length === 0) {
      return [];
    }
    try {
      return await Notifications.insertMany(docs, { ordered: false });
    } catch (error) {
      const failure = error as InsertManyFailure;
      // Mongoose attaches `insertedDocs` to every failure; with no per-document
      // errors (an empty list after an election) the whole write failed and
      // `insertedDocs` lists docs that were never stored, so it is retried.
      const perDocument = failure.writeErrors ?? [];
      if (
        !Array.isArray(failure.insertedDocs) ||
        (perDocument.length === 0 && !isDuplicateKey(failure))
      ) {
        throw error;
      }
      const writeErrors = perDocument.length > 0 ? perDocument : [failure];
      if (!writeErrors.every(isDuplicateKey)) {
        this.logger.error('Some notifications failed to insert', {
          error: error instanceof Error ? error.message : String(error),
        });
      }
      return failure.insertedDocs;
    }
  }

  /** `insertMany` with `ordered: false` skips a schema-invalid doc without a word, so they are found and logged here. */
  private withoutInvalid(
    docs: Record<string, unknown>[],
  ): Record<string, unknown>[] {
    return docs.filter((doc) => {
      const invalid = new Notifications(doc).validateSync();
      if (!invalid) {
        return true;
      }
      this.logger.warn('Notification dropped: failed schema validation', {
        type: doc.type,
        dedupeKey: doc.dedupeKey,
        invalidPaths: Object.keys(invalid.errors),
      });
      return false;
    });
  }
}
