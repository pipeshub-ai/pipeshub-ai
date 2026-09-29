import { inject, injectable } from 'inversify';
import {
  IMessageConsumer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { Logger } from '../../../libs/services/logger.service';
import {
  NotificationProducer,
  EventType as NotificationEventType,
} from '../../notification/service/notification.producer';
import { INotification } from '../../notification/schema/notification.schema';
import { AppConfig } from '../../tokens_manager/config/config';
import { newAccountPasswordLink } from '../../../libs/utils/createJwt';
import { MailBody } from '../middlewares/types';
import {
  MailSenderService,
  SMTP_SEND_DEADLINE_MS,
} from './mail.sender.service';
import {
  MAIL_MESSAGE_BUDGET_MS,
  MailEventPayload,
  MailSendResult,
} from '../types/mail-event.types';

const MAX_ATTEMPTS = 4;
const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;
// Below this an attempt would mostly just hit its deadline and come back indeterminate.
const MIN_ATTEMPT_MS = 15_000;
// Without this, one bad SMTP server during a 1000-address import raises a
// notification per recipient, per admin.
const FAILURE_NOTIFY_WINDOW_MS = 5 * 60_000;

/**
 * Delivers mail jobs off the request path. Retries in-handler rather than by
 * redelivery: the consumer base auto-commits offsets and swallows handler
 * errors, so throwing would drop the job instead of replaying it.
 */
@injectable()
export class MailConsumer {
  private readonly failureNotifyState = new Map<
    string,
    { last: number; suppressed: number }
  >();

  constructor(
    @inject('MessageConsumer') private readonly consumer: IMessageConsumer,
    @inject('Logger') private readonly logger: Logger,
    @inject(MailSenderService) private readonly sender: MailSenderService,
    @inject(NotificationProducer)
    private readonly notificationProducer: NotificationProducer,
    @inject('AppConfigProvider')
    private readonly getAppConfig: () => AppConfig,
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

  async consume<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    if (!this.consumer.isConnected()) {
      this.logger.error('Cannot consume mail events: MessageConsumer is not connected');
      throw new Error('MessageConsumer is not connected');
    }

    await this.consumer.consume(async (message: StreamMessage<T>) => {
      try {
        const payload = message.value as MailEventPayload;
        if (!payload?.mail?.emailTemplateType) {
          this.logger.warn('Mail event skipped: invalid payload', {
            value: message.value,
          });
          return;
        }
        await this.deliver(payload);
      } catch (error) {
        this.logger.error('Failed to process mail event', {
          error: error instanceof Error ? error.message : String(error),
        });
      } finally {
        await handler(message);
      }
    });
  }

  private async deliver(payload: MailEventPayload): Promise<void> {
    const smtpConfig = this.sender.getSmtpConfig();
    if (!smtpConfig) {
      // Not retryable until an admin configures SMTP.
      this.logger.error('Mail event dropped: SMTP configuration not set');
      await this.notifyFailure(payload, 'SMTP configuration not set');
      return;
    }

    const mail = this.withPasswordResetLink(payload);
    const giveUpAt = Date.now() + MAIL_MESSAGE_BUDGET_MS;
    let attempt = 0;
    let lastError = 'unknown error';

    while (attempt < MAX_ATTEMPTS) {
      attempt += 1;
      const result: MailSendResult = await this.sender.send(
        mail,
        smtpConfig,
        Math.min(SMTP_SEND_DEADLINE_MS, giveUpAt - Date.now()),
      );

      if (result.status === 'sent') {
        this.logger.info('Mail sent', {
          emailTemplateType: payload.mail.emailTemplateType,
          attempt,
        });
        return;
      }

      lastError = result.error;

      if (result.status === 'permanent') {
        this.logger.error('Mail permanently failed; not retrying', {
          emailTemplateType: payload.mail.emailTemplateType,
          error: lastError,
          attempt,
        });
        await this.notifyFailure(payload, lastError);
        return;
      }

      // indeterminate: the original send may still complete, so a retry could
      // duplicate it. unavailable: the server is cooling down after repeated
      // connection failures; the retry ladder would only stall the partition.
      if (result.status === 'indeterminate' || result.status === 'unavailable') {
        this.logger.error('Mail not delivered; not retrying', {
          emailTemplateType: payload.mail.emailTemplateType,
          status: result.status,
          error: lastError,
          attempt,
        });
        await this.notifyFailure(payload, lastError);
        return;
      }

      if (attempt < MAX_ATTEMPTS) {
        const delay = Math.min(
          BASE_BACKOFF_MS * 2 ** (attempt - 1),
          MAX_BACKOFF_MS,
        );
        if (giveUpAt - Date.now() - delay < MIN_ATTEMPT_MS) {
          this.logger.warn('Mail retry budget exhausted', {
            emailTemplateType: payload.mail.emailTemplateType,
            attempt,
          });
          break;
        }
        this.logger.warn('Mail send failed; retrying', {
          emailTemplateType: payload.mail.emailTemplateType,
          error: lastError,
          attempt,
          nextRetryInMs: delay,
        });
        await this.sleep(delay);
      }
    }

    this.logger.error('Mail failed after all retries', {
      emailTemplateType: payload.mail.emailTemplateType,
      error: lastError,
      attempts: attempt,
    });
    await this.notifyFailure(payload, lastError);
  }

  private withPasswordResetLink(payload: MailEventPayload): MailBody {
    const target = payload.passwordResetLinkFor;
    if (!target) {
      return payload.mail;
    }
    const config = this.getAppConfig();
    return {
      ...payload.mail,
      templateData: {
        ...payload.mail.templateData,
        link: newAccountPasswordLink(
          config.frontendUrl,
          target.email,
          target.userId,
          target.orgId,
          config.scopedJwtSecret,
        ),
      },
    };
  }

  /** Drops idle orgs so the throttle map cannot grow without bound. */
  private pruneFailureNotifyState(now: number): void {
    for (const [orgId, entry] of this.failureNotifyState) {
      if (now - entry.last > FAILURE_NOTIFY_WINDOW_MS) {
        this.failureNotifyState.delete(orgId);
      }
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /** Needs an orgId: the notification pipeline drops events without one. */
  private async notifyFailure(
    payload: MailEventPayload,
    error: string,
  ): Promise<void> {
    if (!payload.orgId) {
      this.logger.error('Mail failure not notified: no orgId on the event', {
        emailTemplateType: payload.mail.emailTemplateType,
        error,
      });
      return;
    }

    const now = Date.now();
    const state = this.failureNotifyState.get(payload.orgId);
    if (state && now - state.last < FAILURE_NOTIFY_WINDOW_MS) {
      state.suppressed += 1;
      this.logger.warn('Mail failure notification suppressed', {
        orgId: payload.orgId,
        suppressed: state.suppressed,
        error,
      });
      return;
    }
    const suppressed = state?.suppressed ?? 0;
    this.failureNotifyState.set(payload.orgId, { last: now, suppressed: 0 });
    this.pruneFailureNotifyState(now);

    const recipients = payload.mail.sendEmailTo ?? [];
    const alsoFailed =
      suppressed > 0 ? ` (${suppressed} further failure(s) suppressed)` : '';
    try {
      await this.notificationProducer.start();
      await this.notificationProducer.publishEvent({
        eventType: NotificationEventType.NewNotificationEvent,
        timestamp: Date.now(),
        payload: {
          orgId: payload.orgId,
          type: 'mail.deliveryFailed',
          recipientRoles: ['admin'],
          title: 'Email delivery failed',
          message: `Could not deliver "${payload.mail.subject ?? payload.mail.emailTemplateType}" to ${recipients.join(', ') || 'the recipient'}: ${error}${alsoFailed}`,
          severity: 'error',
          status: 'unread',
          payload: {
            emailTemplateType: payload.mail.emailTemplateType,
            recipients,
            error,
            suppressedFailures: suppressed,
          },
        } as unknown as INotification,
      });
    } catch (publishError) {
      this.logger.error('Failed to publish mail failure notification', {
        error:
          publishError instanceof Error
            ? publishError.message
            : String(publishError),
      });
    }
  }
}
