import {
  IMessageConsumer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject } from 'inversify';
import mongoose from 'mongoose';
import { INotification, Notifications } from '../schema/notification.schema';
import { NotificationService } from './notification.service';
import {
  INotificationEmailDispatcher,
  NOTIFICATION_EMAIL_DISPATCHER,
} from './notification-email.dispatcher';
import { resolveNotificationRecipientUserIds } from '../utils/notification-recipient.resolver';
import {
  buildNotificationDocForUser,
  NotificationBrokerMessage,
  brokerMessageLogMeta,
  toBrokerMessage,
} from '../utils/notification-payload.resolver';

const DUPLICATE_KEY_CODE = 11000;

interface InsertManyFailure {
  code?: number;
  writeErrors?: { code?: number }[];
  insertedDocs?: INotification[];
}

interface CoalesceOutcome {
  created: INotification[];
  updated: INotification[];
}

const isDuplicateKey = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === DUPLICATE_KEY_CODE;

const sessionIdOf = (payload: INotification['payload']): string | undefined =>
  typeof payload?.sessionId === 'string' ? payload.sessionId : undefined;

const toPlainObject = (doc: INotification): object =>
  typeof doc.toObject === 'function' ? doc.toObject<object>() : doc;

@injectable()
export class NotificationConsumer {
  constructor(
    @inject('MessageConsumer') private readonly consumer: IMessageConsumer,
    @inject('Logger') private readonly logger: Logger,
    @inject(NotificationService)
    private readonly notificationService: NotificationService,
    @inject(NOTIFICATION_EMAIL_DISPATCHER)
    private readonly emailDispatcher: INotificationEmailDispatcher,
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

            const orgOid = new mongoose.Types.ObjectId(event.orgId);
            const recipientUserIds = await resolveNotificationRecipientUserIds(
              orgOid,
              event.recipientUserIds,
              event.recipientRoles,
            );

            if (recipientUserIds.length === 0) {
              this.logger.warn('Notification event skipped: no recipients', {
                orgId: event.orgId,
                type: event.type,
              });
              return;
            }

            const docs = recipientUserIds.map((userOid) =>
              buildNotificationDocForUser(event, userOid),
            );
            const { created, updated } =
              typeof event.coalesceKey === 'string' && event.coalesceKey !== ''
                ? await this.coalesce(docs, event)
                : { created: await this.insertNew(docs), updated: [] };

            const dispatchedUserIds: string[] = [];
            for (const saved of created) {
              const userId = String(saved.assignedTo);
              this.notificationService.sendToUser(
                userId,
                'newNotification',
                toPlainObject(saved),
              );
              dispatchedUserIds.push(userId);
            }
            for (const saved of updated) {
              this.notificationService.sendToUser(
                String(saved.assignedTo),
                'notificationUpdated',
                toPlainObject(saved),
              );
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
              coalescedCount: updated.length,
              notificationIds: created.map((saved) => String(saved._id)),
              userIds: dispatchedUserIds,
            });
          } catch (error) {
            this.logger.error('Failed to process notification message', {
              error: error instanceof Error ? error.message : String(error),
              ...brokerMessageLogMeta(message.value),
            });
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
      if (!Array.isArray(failure.insertedDocs)) {
        throw error;
      }
      const writeErrors = failure.writeErrors ?? [failure];
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

  private async coalesce(
    docs: Record<string, unknown>[],
    event: NotificationBrokerMessage,
  ): Promise<CoalesceOutcome> {
    const outcome: CoalesceOutcome = { created: [], updated: [] };
    const count = event.payload?.count;
    const increment =
      typeof count === 'number' && Number.isInteger(count) && count > 0
        ? count
        : 1;
    await Promise.all(
      docs.map(async (doc) => {
        try {
          const result = await this.upsertCoalesced(doc, increment);
          if (result) {
            (result.created ? outcome.created : outcome.updated).push(
              result.doc,
            );
          }
        } catch (error) {
          this.logger.error('Failed to coalesce notification', {
            userId: String(doc.assignedTo),
            type: event.type,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }),
    );
    return outcome;
  }

  /** Two concurrent upserts can both miss; the loser hits the unique index and retries as a plain update. */
  private async upsertCoalesced(
    doc: Record<string, unknown>,
    increment: number,
  ): Promise<{ doc: INotification; created: boolean } | null> {
    const { assignedTo, coalesceKey, message, payload, ...onInsert } = doc;
    delete onInsert.status;
    for (const [key, value] of Object.entries(
      (payload ?? {}) as Record<string, unknown>,
    )) {
      if (key !== 'count') {
        onInsert[`payload.${key}`] = value;
      }
    }
    const filter = { assignedTo, coalesceKey, status: 'unread' };
    const update = {
      $inc: { 'payload.count': increment },
      $set: { message, updatedAt: new Date() },
      $setOnInsert: onInsert,
    };
    for (let attempt = 0; ; attempt++) {
      try {
        const result = await Notifications.findOneAndUpdate(filter, update, {
          upsert: true,
          new: true,
          includeResultMetadata: true,
        });
        return result.value
          ? {
              doc: result.value,
              created: result.lastErrorObject?.updatedExisting !== true,
            }
          : null;
      } catch (error) {
        if (attempt > 0 || !isDuplicateKey(error)) {
          throw error;
        }
      }
    }
  }
}
