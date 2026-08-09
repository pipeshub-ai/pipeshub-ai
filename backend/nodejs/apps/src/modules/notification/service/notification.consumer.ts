import { IMessageConsumer, StreamMessage } from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject } from 'inversify';
import mongoose from 'mongoose';
import { INotification, Notifications } from '../schema/notification.schema';
import { NotificationService } from './notification.service';
import { resolveNotificationRecipientUserIds } from '../utils/notification-recipient.resolver';
import {
  buildNotificationDocForUser,
  toBrokerMessage,
  NotificationBrokerMessage,
} from '../utils/notification-payload.resolver';

type SavedNotification = mongoose.HydratedDocument<INotification>;

const WORKFLOW_RUN_TYPES = new Set([
  'WORKFLOW_RUN_STARTED',
  'WORKFLOW_RUN_SUCCEEDED',
  'WORKFLOW_RUN_FAILED',
  'WORKFLOW_AWAITING_APPROVAL',
]);

@injectable()
export class NotificationConsumer {
  constructor(
    @inject('MessageConsumer') private readonly consumer: IMessageConsumer,
    @inject('Logger') private readonly logger: Logger,
    @inject(NotificationService)
    private readonly notificationService: NotificationService,
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

  async subscribe(
    topics: string[],
    fromBeginning = false,
  ): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.subscribe(topics, fromBeginning);
    }
  }

  /**
   * Persist notification docs, deduplicating workflow-run events by
   * (type, payload.runId, assignedTo) so Kafka redelivery cannot create
   * duplicate rows. Non-workflow notifications carry no stable natural key
   * and are inserted as before.
   */
  private async persistNotifications(
    event: NotificationBrokerMessage,
    docs: Record<string, unknown>[],
  ): Promise<SavedNotification[]> {
    const runId =
      WORKFLOW_RUN_TYPES.has(event.type) && event.payload
        ? ((event.payload as Record<string, unknown>).runId as string | undefined)
        : undefined;

    if (!runId) {
      return Notifications.create(docs);
    }

    const saved = await Promise.all(
      docs.map((doc) =>
        Notifications.findOneAndUpdate(
          {
            type: event.type,
            assignedTo: doc.assignedTo,
            'payload.runId': runId,
          },
          { $set: doc },
          { upsert: true, new: true, setDefaultsOnInsert: true },
        ).exec(),
      ),
    );
    return saved.filter((doc): doc is SavedNotification => doc !== null);
  }

  /**
   * Push the live `workflowRunUpdate` socket event for a workflow-run
   * notification. No-op for other notification types.
   */
  private emitRunUpdate(event: NotificationBrokerMessage, userId: string): void {
    if (!WORKFLOW_RUN_TYPES.has(event.type) || !event.payload) return;
    const wp = event.payload as Record<string, unknown>;
    const workflowId = wp.workflowId as string | undefined;
    const runId = wp.runId as string | undefined;
    if (!workflowId || !runId) return;

    // wp.status is the lowercase run status ('succeeded', 'failed', etc.)
    // set by the Python notifier. Fall back to a lowercased version of the
    // notification type so the frontend's status comparisons are never
    // broken by an uppercase type string.
    const rawStatus = (wp.status as string | undefined) ?? event.type;
    const status = rawStatus.startsWith('WORKFLOW_')
      ? rawStatus.replace('WORKFLOW_RUN_', '').toLowerCase()
      : rawStatus.toLowerCase();

    this.notificationService.emitWorkflowRunUpdate({
      workflowId,
      runId,
      status,
      redirectLink: wp.redirectLink as string | undefined,
      conversationId: wp.conversationId as string | undefined,
      outputSummary: wp.outputSummary as string | undefined,
      triggerKind: wp.triggerKind as string | undefined,
      workflowName: wp.workflowName as string | undefined,
      userId,
      orgId: event.orgId,
    });
  }

  async consume<INotification>(
    handler: (message: StreamMessage<INotification>) => Promise<void>,
  ): Promise<void> {
    if (this.consumer.isConnected()) {
      await this.consumer.consume(async (message: StreamMessage<INotification>) => {
        try {
          const event = toBrokerMessage(message.value);
          if (!event) {
            this.logger.warn('Notification event skipped: invalid orgId or type', {
              value: message.value,
            });
            return;
          }

          const orgOid = new mongoose.Types.ObjectId(String(event.orgId));
          let recipientUserIds = await resolveNotificationRecipientUserIds(
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

          // A dry run publishes run-lifecycle notifications only so the
          // in-chat dry-run card can resolve; storing them would fill the
          // inbox with results of previews nobody asked to be told about.
          const isDryRun =
            Boolean(event.payload && (event.payload as Record<string, unknown>).isDryRun);

          if (isDryRun) {
            for (const userId of recipientUserIds.map(String)) {
              this.emitRunUpdate(event, userId);
            }
            this.logger.info('Dry-run notification dispatched live only', {
              orgId: event.orgId,
              type: event.type,
              recipientCount: recipientUserIds.length,
            });
            return;
          }

          const docs = recipientUserIds.map((userOid) => buildNotificationDocForUser(event, userOid));
          // Kafka keys only control partitioning, they do not dedupe: a
          // consumer-group rebalance or handler timeout redelivers the same
          // message. Upsert on the run-derived identity so a redelivery
          // refreshes the existing row instead of creating a duplicate.
          const savedDocs = await this.persistNotifications(event, docs);

          const savedIds: string[] = [];
          const dispatchedUserIds: string[] = [];

          for (const saved of savedDocs) {
            const userId = String(saved.assignedTo);
            const payload =
              typeof (saved as { toObject?: () => object }).toObject === 'function'
                ? (saved as { toObject: () => object }).toObject()
                : saved;
            this.notificationService.sendToUser(userId, 'newNotification', payload);
            this.emitRunUpdate(event, userId);

            savedIds.push(String(saved._id));
            dispatchedUserIds.push(userId);
          }

          this.logger.info('Notification saved and dispatched', {
            orgId: event.orgId,
            type: event.type,
            recipientCount: dispatchedUserIds.length,
            notificationIds: savedIds,
            userIds: dispatchedUserIds,
          });
        } catch (error) {
          this.logger.error('Failed to process notification message', {
            error: error instanceof Error ? error.message : String(error),
            messageValue: message.value,
          });
        } finally {
          await handler(message);
        }
      });
    } else {
      this.logger.error('Cannot consume notifications: MessageConsumer is not connected');
      throw new Error('MessageConsumer is not connected');
    }
  }
}
