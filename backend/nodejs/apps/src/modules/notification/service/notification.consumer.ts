import {
  IMessageConsumer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import { injectable, inject, optional } from 'inversify';
import { isRetryableStoreError } from '../../../libs/repositories/errors';
import { DuplicateEnvelope } from '../../../libs/context/envelope';
import { NotificationService } from './notification.service';
import { notificationOf, toBrokerMessage } from '../utils/notification-payload.resolver';
import { notificationEventRefused, notificationLegacySigned } from './notification.delivery';
import { enforcing } from '../../../libs/context/orgContext';
import { ValidationError } from '../../../libs/errors/validation.error';
import { deliverToOrg, messageFacts } from './notification.delivery';
import {
  PlatformAlertHandler,
  isPlatformAudience,
} from './platform-alert.handler';

// Defined beside the delivery that counts it; exported here as before.
export { notificationEventRefused } from './notification.delivery';

@injectable()
export class NotificationConsumer {
  constructor(
    @inject('MessageConsumer') private readonly consumer: IMessageConsumer,
    @inject('Logger') private readonly logger: Logger,
    @inject(NotificationService)
    private readonly notificationService: NotificationService,
    @inject(PlatformAlertHandler)
    @optional()
    private readonly platformAlertHandler?: PlatformAlertHandler,
  ) {}

  /** An event for the installation's admins rather than one org's users. */
  private async handlePlatformEvent(value: unknown): Promise<void> {
    if (!this.platformAlertHandler) {
      this.logger.warn('Platform alert skipped: no handler is configured');
      return;
    }
    await this.platformAlertHandler.handle(value);
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
            const { value, signedLegacy, signedBody } = notificationOf(message.value);
            // A signed notification is never routed by its unsigned top level (an added `audience`).
            if (!signedBody && isPlatformAudience(message.value)) {
              await this.handlePlatformEvent(message.value);
              return;
            }
            // Signed, but in the older shape: its top-level fields (text, recipients) are not covered by the signature.
            // Counted; refused in enforce mode, read in shadow mode (its org is held to the signed one at delivery).
            if (signedLegacy) {
              notificationLegacySigned.inc();
              if (enforcing()) {
                notificationEventRefused.inc({ reason: 'legacy_signed' });
                this.logger.warn('Notification event refused: signed in the older shape', messageFacts(message.value));
                return;
              }
            }
            const event = toBrokerMessage(value);
            if (!event) {
              this.logger.warn(
                'Notification event skipped: invalid orgId or type',
                messageFacts(message.value),
              );
              return;
            }

            await deliverToOrg(event, this.notificationService, this.logger);
          } catch (error) {
            // A store that is frozen or away is not a bad message: let the broker deliver it again.
            if (isRetryableStoreError(error)) {
              redeliver = true;
              throw error;
            }
            // Another delivery of the event stored it first: the consumer acknowledges this one.
            if (error instanceof DuplicateEnvelope) throw error;
            // A refused event is the producer's fault (WARNING); anything else is ours.
            const level = error instanceof ValidationError ? 'warn' : 'error';
            this.logger[level]('Failed to process notification message', {
              ...messageFacts(message.value),
              error,
            });
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
}
