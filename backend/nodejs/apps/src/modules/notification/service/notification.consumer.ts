import { randomUUID } from 'crypto';
import {
  BrokerTopic,
  IMessageConsumer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject, optional } from 'inversify';
import mongoose from 'mongoose';
import { Notifications } from '../schema/notification.schema';
import { forOrg } from '../../../libs/context/orgContext';
import { isRetryableStoreError } from '../../../libs/repositories/errors';
import {
  DuplicateEnvelope,
  withDeliveryRecorded,
} from '../../../libs/context/envelope';
import {
  getNotificationRepository,
  type NewNotification,
  type NotificationRow,
} from '../../../libs/repositories/notification';
import { getOutboxRepository } from '../../../libs/repositories/outbox';
import { isNotYetVisible } from '../../../libs/repositories/notification/dates';
import { ValidationError } from '../../../libs/errors/validation.error';
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
import { messageFacts, notificationEventRefused } from './notification.delivery';
import {
  PlatformAlertHandler,
  isPlatformAudience,
} from './platform-alert.handler';
import {
  buildNotificationDocForUser,
  NotificationBrokerMessage,
  brokerMessageLogMeta,
  toBrokerMessage,
} from '../utils/notification-payload.resolver';

// Defined beside the delivery that counts it; exported here as before.
export { notificationEventRefused } from './notification.delivery';

/** In-process retries stop after this; it outlasts a primary election. */
const RETRY_WINDOW_MS = 30_000;
const FIRST_RETRY_DELAY_MS = 1_000;
/** Times one event may go back on the outbox before it is dropped. */
const MAX_REDELIVERIES = 8;

const sessionIdOf = (
  payload: NotificationRow['payload'],
): string | undefined =>
  typeof payload?.sessionId === 'string' ? payload.sessionId : undefined;

/** The stored row plus the policy's push-only fields. */
const pushed = async (
  saved: NotificationRow,
  plan: DeliveryPlan,
): Promise<object> => ({
  ...saved,
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

/**
 * What a log line says about a message: its facts (`messageFacts`: the type when it is a plain token, and the
 * size, never the value), and the ids that name the event and which of its fields were invalid.
 */
const logFacts = (value: unknown): Record<string, unknown> => {
  const { type: _rawType, ...ids } = brokerMessageLogMeta(value);
  return { ...ids, ...messageFacts(parsedValue(value)) };
};

/** A message value the broker handed over as JSON text, as an object; anything else as it is. */
const parsedValue = (value: unknown): unknown => {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
};

/** Sends nothing: a consumer built without the email port, as tests build it. */
const noEmail: INotificationEmailDispatcher = {
  dispatch: () => Promise.resolve(),
};

/** Tries at taking back rows whose recipient lost access since the plan, before giving up on it. */
const TAKE_BACK_TRIES = 3;
const TAKE_BACK_DELAY_MS = 200;

@injectable()
export class NotificationConsumer {
  constructor(
    @inject('MessageConsumer') private readonly consumer: IMessageConsumer,
    @inject('Logger') private readonly logger: Logger,
    @inject(NotificationService)
    private readonly notificationService: NotificationService,
    // Fourth: the platform alert handler (#5959) is where its callers and tests pass it.
    @inject(PlatformAlertHandler)
    @optional()
    private readonly platformAlertHandler?: PlatformAlertHandler,
    @inject(NOTIFICATION_EMAIL_DISPATCHER)
    private readonly emailDispatcher: INotificationEmailDispatcher = noEmail,
    @inject(NOTIFICATION_DELIVERY_POLICY)
    private readonly deliveryPolicy: INotificationDeliveryPolicy = deliverToRecipients,
  ) {}

  /** An event for the installation's admins rather than one org's users. */
  private async handlePlatformEvent(value: unknown): Promise<void> {
    if (!this.platformAlertHandler) {
      this.logger.warn('Platform alert skipped: no handler is configured');
      return;
    }
    // The catch in `consume` rethrows for the org path; here only a store that is frozen or away is thrown, so the
    // broker delivers the event again, and an event another delivery stored first, which the envelope acknowledges.
    // Any other failure is logged and the event acknowledged.
    try {
      await this.platformAlertHandler.handle(value);
    } catch (error) {
      if (isRetryableStoreError(error) || error instanceof DuplicateEnvelope) throw error;
      // A refused event is the producer's fault (WARNING); anything else is ours.
      const level = error instanceof ValidationError ? 'warn' : 'error';
      this.logger[level]('Failed to process notification message', {
        ...logFacts(value),
        error,
      });
    }
  }

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
          let redeliver = false;
          try {
            if (isPlatformAudience(message.value)) {
              await this.handlePlatformEvent(message.value);
              return;
            }
            const event = toBrokerMessage(message.value);
            if (!event) {
              this.logger.warn(
                'Notification event skipped: invalid orgId or type',
                logFacts(message.value),
              );
              return;
            }
            await this.deliverWithRetry(event, message.key);
          } catch (error) {
            // A store that is frozen or away is not a bad message: the broker delivers it again. Any other error is
            // rethrown too: an event another delivery stored first (DuplicateEnvelope) is acknowledged by the envelope.
            if (isRetryableStoreError(error)) redeliver = true;
            throw error;
          } finally {
            if (!redeliver) await handler(message);
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
   * Only a store that is frozen or away, or a failed re-queue, throws, so the
   * broker keeps the message.
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
        // Waiting here would hold the partition while the store moves; the broker delivers it again instead.
        if (isRetryableStoreError(error)) throw error;
        // Another delivery of the event stored it first: the envelope acknowledges this one.
        if (error instanceof DuplicateEnvelope) throw error;
        const waitMs = Math.min(delayMs, deadline - Date.now());
        if (isTransient(error) && waitMs > 0) {
          await sleep(waitMs);
          continue;
        }
        const meta = { ...logFacts(event), error };
        if (isTransient(error)) {
          await this.requeue(event, key, meta);
        } else {
          // A refused event is the producer's fault (WARNING); anything else is ours.
          const level = error instanceof ValidationError ? 'warn' : 'error';
          this.logger[level]('Failed to process notification message', meta);
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
    await forOrg(event.orgId, 'notification-consumer', () =>
      getOutboxRepository().append({
        topic: BrokerTopic.NOTIFICATION,
        key: key !== undefined && key !== '' ? key : event.type,
        orderingKey: `redeliver:${randomUUID()}`,
        value: JSON.stringify({ ...event, redelivery }),
        status: 'pending',
        attempts: 0,
        nextAttemptAt: new Date(Date.now() + 1000 * 2 ** redelivery),
      }),
    );
    this.logger.warn('Notification re-queued after a transient failure', {
      ...meta,
      redelivery,
    });
  }

  private deliver(event: NotificationBrokerMessage): Promise<void> {
    // The org comes from the event, bound before any repository is touched.
    return forOrg(event.orgId, 'notification-consumer', () =>
      this.deliverForOrg(event),
    );
  }

  private async deliverForOrg(event: NotificationBrokerMessage): Promise<void> {
    const recipientUserIds = await resolveNotificationRecipientUserIds(
      event.orgId,
      event.recipientUserIds,
      event.recipientRoles,
    );
    // A supplied id names one stored row; copied to several recipients it would reach only the first. Refuse the whole event.
    if (event._id != null && recipientUserIds.length > 1) {
      notificationEventRefused.inc({
        reason: 'id_with_many_recipients',
      });
      throw new ValidationError('Notification event refused', [
        {
          field: '_id',
          code: 'id_with_many_recipients',
          message: `an event that carries _id must have exactly one recipient, got ${recipientUserIds.length}`,
        },
      ]);
    }
    const plan = await this.deliveryPolicy.plan(
      event,
      recipientUserIds.map((id) => new mongoose.Types.ObjectId(id)),
    );

    if (plan.userIds.length === 0) {
      this.logger.warn('Notification event skipped: no recipients', {
        orgId: event.orgId,
        type: event.type,
      });
      return;
    }

    const docs = plan.userIds.map((userOid) => {
      const doc = buildNotificationDocForUser(event, String(userOid));
      if (plan.readFor?.has(String(userOid))) {
        doc.status = 'read';
      }
      return doc;
    });
    // On PostgreSQL the rows and the inbox record for this org commit together, so a redelivery cannot store them twice.
    const created = await this.stillWanted(
      event.orgId,
      await withDeliveryRecorded(() => this.insertNew(docs), event.orgId),
      plan,
    );
    // stored, but a scheduled one is not pushed now and not queued; it is read from the list at its time
    const due = created.filter((saved) => !isNotYetVisible(saved.visibleAt));

    const dispatchedUserIds: string[] = [];
    for (const saved of due) {
      const userId = saved.assignedTo;
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
        due.map((saved) =>
          this.emailDispatcher.dispatch({
            orgId: saved.orgId,
            assignedTo: saved.assignedTo,
            dedupeKey: saved.dedupeKey,
            redirectLink: saved.redirectLink,
            emailIntent,
            notification: {
              id: saved._id,
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
      notificationIds: created.map((saved) => saved._id),
      userIds: dispatchedUserIds,
    });
  }

  /**
   * The stored rows whose recipient may still have them (`plan.stillFor`); the others are deleted before anything
   * is pushed. When the check fails every row of this delivery is deleted, so a retry can store them again.
   */
  private async stillWanted(
    orgId: string,
    created: NotificationRow[],
    plan: DeliveryPlan,
  ): Promise<NotificationRow[]> {
    if (!plan.stillFor || created.length === 0) {
      return created;
    }
    let still: ReadonlySet<string>;
    try {
      still = await plan.stillFor(
        created.map((saved) => new mongoose.Types.ObjectId(saved.assignedTo)),
      );
    } catch (error) {
      await this.takeBack(orgId, created);
      throw error;
    }
    const gone = created.filter((saved) => !still.has(saved.assignedTo));
    if (gone.length > 0) {
      await this.takeBack(orgId, gone);
      this.logger.debug(
        'Notification taken back: access changed while it was stored',
        {
          type: gone[0]?.type,
          notificationIds: gone.map((saved) => saved._id),
        },
      );
    }
    return created.filter((saved) => still.has(saved.assignedTo));
  }

  /** Deletes rows nobody has been told of yet; a few tries, since a row left behind points at what they lost. */
  private async takeBack(
    orgId: string,
    rows: readonly NotificationRow[],
  ): Promise<void> {
    const ids = rows.map((saved) => saved._id);
    for (let attempt = 1; ; attempt += 1) {
      try {
        await getNotificationRepository().deleteByIds(orgId, ids);
        return;
      } catch (error) {
        if (attempt >= TAKE_BACK_TRIES) {
          this.logger.error(
            'Could not take back notifications nobody may see',
            {
              notificationIds: ids,
              error: error instanceof Error ? error.message : String(error),
            },
          );
          return;
        }
        await sleep(TAKE_BACK_DELAY_MS * attempt);
      }
    }
  }

  /** Returns the rows that were newly stored; a row whose `dedupeKey` its recipient has was delivered before and is dropped. */
  private async insertNew(
    allDocs: NewNotification[],
  ): Promise<NotificationRow[]> {
    const docs = this.withoutInvalid(allDocs);
    if (docs.length === 0) {
      return [];
    }
    const { inserted, refused } =
      await getNotificationRepository().insertNew(docs);
    if (refused.length > 0) {
      this.logger.error('Some notifications failed to insert', {
        errors: refused,
      });
    }
    return inserted;
  }

  /** A store would skip a schema-invalid doc without a word, or refuse the whole batch for it, so they are found and logged here. */
  private withoutInvalid(docs: NewNotification[]): NewNotification[] {
    return docs.filter((doc) => {
      // eslint-disable-next-line @typescript-eslint/no-deprecated -- a synchronous schema check, without middleware
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
