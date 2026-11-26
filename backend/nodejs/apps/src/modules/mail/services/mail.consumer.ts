import { inject, injectable } from 'inversify';
import {
  IMessageConsumer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import {
  getCurrentOrgId,
  orgContextStorage,
  makeOrgContext,
  runWithOrgContext,
} from '../../../libs/context/orgContext';
import { Logger } from '../../../libs/services/logger.service';
import {
  NotificationProducer,
  EventType as NotificationEventType,
} from '../../notification/service/notification.producer';
import type { NotificationEventPayload } from '../../notification/service/notification.producer';
import { AppConfig } from '../../tokens_manager/config/config';
import { newAccountPasswordLink } from '../../../libs/utils/createJwt';
import { MailBody, SmtpConfig } from '../middlewares/types';
import {
  MailSenderService,
  SMTP_SEND_DEADLINE_MS,
  SmtpSettingsUnreadableError,
} from './mail.sender.service';
import {
  MAIL_MESSAGE_BUDGET_MS,
  MailEventPayload,
  MailSendResult,
  mailJobOf,
} from '../types/mail-event.types';
import {
  classifyError,
  ErrorClass,
  levelFor,
} from '../../../libs/utils/error-class';

const MAX_ATTEMPTS = 4;
const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;
// Below this an attempt would mostly just hit its deadline and come back indeterminate.
const MIN_ATTEMPT_MS = 15_000;
// Without this, one bad SMTP server during a 1000-address import raises a
// notification per recipient, per admin.
const FAILURE_NOTIFY_WINDOW_MS = 5 * 60_000;
const SUPPRESSED_RECIPIENT_SAMPLE = 10;

/** One mail job's progress, so whichever exit it takes writes its one `mail.send.end` line. */
interface MailJob {
  startedAt: number;
  template?: string;
  attempt: number;
  ended: boolean;
}

interface MailEndFields {
  reason?: string;
  errorClass?: ErrorClass;
  smtpCode?: number;
  /** Only our own exceptions: an SMTP failure's text quotes the recipient. */
  error?: unknown;
  /** A skip the sender asked for: logged at INFO. */
  expected?: boolean;
}

// A template type is a short identifier (`loginWithOTP`); anything else is not printed.
const TEMPLATE_TYPE = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;

function newMailJob(payload: MailEventPayload | undefined): MailJob {
  const template = payload?.mail?.emailTemplateType;
  return {
    startedAt: Date.now(),
    template:
      typeof template === 'string' && TEMPLATE_TYPE.test(template)
        ? template
        : undefined,
    attempt: 0,
    ended: false,
  };
}

interface SuppressedFailures {
  count: number;
  recipients: string[];
  emailTemplateType?: string;
  lastError: string;
  timer: NodeJS.Timeout;
}

/**
 * Delivers mail jobs off the request path. Retries in-handler rather than by
 * redelivery: the consumer base auto-commits offsets and swallows handler
 * errors, so throwing would drop the job instead of replaying it.
 */
/** A job asked for a set-password link it may not have (another org, or other recipients): nothing is sent. */
class PasswordLinkRefused extends Error {
  constructor() {
    super('set-password link refused: the job names another org or other recipients');
  }
}

/** Whether this delivery was opened from a verified signed envelope. */
function signedDelivery(): boolean {
  return orgContextStorage.getStore()?.source === 'envelope';
}

@injectable()
export class MailConsumer {
  // Presence means a notification went out for this org in the current window;
  // the timer closes the window and reports whatever was suppressed meanwhile.
  private readonly failureNotifyState = new Map<string, SuppressedFailures>();

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
    for (const state of this.failureNotifyState.values()) {
      clearTimeout(state.timer);
    }
    this.failureNotifyState.clear();
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
      this.logger.error(
        'Cannot consume mail events: MessageConsumer is not connected',
      );
      throw new Error('MessageConsumer is not connected');
    }

    await this.consumer.consume(async (message: StreamMessage<T>) => {
      // Under a verified signature only the signed job is read (a top-level job beside it is a writer's, not the producer's).
      const payload = mailJobOf(message.value, signedDelivery());
      const job = newMailJob(payload);
      try {
        if (!payload?.mail?.emailTemplateType) {
          // Never the value: it holds the recipients and the template data (a sign-in code, a link).
          this.end(job, 'skipped', { reason: 'invalid_payload' });
          return;
        }
        await this.deliver(payload, job);
      } catch (error) {
        if (!job.ended) {
          this.end(job, 'failure', {
            reason: 'exception',
            errorClass: classifyError(error),
            error,
          });
        } else {
          this.logger.error('Failed to process mail event', { error });
        }
      } finally {
        await handler(message);
      }
    });
  }

  /**
   * Runs the delivery inside the event's org. A signed envelope already bound it; an unsigned job carries only the
   * `orgId` the producer took from the inviting user's verified request, so that is bound here (the audit write needs it).
   */
  private deliver(
    payload: MailEventPayload,
    job: MailJob = newMailJob(payload),
  ): Promise<void> {
    const orgId = payload.orgId;
    if (getCurrentOrgId() !== undefined || orgId === undefined || orgId === '')
      return this.deliverInOrg(payload, job);
    return runWithOrgContext(
      makeOrgContext({
        orgId,
        userId: null,
        kind: 'service',
        source: 'legacy-payload',
      }),
      () => this.deliverInOrg(payload, job),
    );
  }

  /** The job's one line: INFO when sent, else at its class's level (docs/logging.md "Mail delivery (Node)"). */
  private end(
    job: MailJob,
    outcome: 'success' | 'failure' | 'skipped',
    fields: MailEndFields = {},
  ): void {
    job.ended = true;
    const level =
      outcome === 'success' || fields.expected
        ? 'info'
        : fields.errorClass
          ? levelFor(fields.errorClass)
          : 'warn';
    this.logger[level]('mail.send.end', {
      outcome,
      reason: fields.reason,
      error_class: fields.errorClass,
      attempt: job.attempt,
      duration_ms: Date.now() - job.startedAt,
      // Untyped keys print as metadata, so only the ones that have a value.
      ...(job.template !== undefined && { email_template_type: job.template }),
      ...(fields.smtpCode !== undefined && { smtp_code: fields.smtpCode }),
      ...(fields.error !== undefined && { error: fields.error }),
      event_name: 'mail.send.end',
    });
  }

  private endFailure(
    job: MailJob,
    reason: string,
    result: MailSendResult,
  ): void {
    if (result.status === 'sent') return;
    this.end(job, 'failure', {
      reason,
      errorClass: result.errorClass,
      smtpCode: result.smtpCode,
    });
  }

  /** `undefined` when the stored settings could not be read: retried, unlike `null`. */
  private async resolveSmtpConfig(
    orgId: string | undefined,
  ): Promise<SmtpConfig | null | undefined> {
    try {
      return await this.sender.getSmtpConfig(orgId);
    } catch (error) {
      if (error instanceof SmtpSettingsUnreadableError) {
        return undefined;
      }
      throw error;
    }
  }

  private async dropUnconfigured(
    payload: MailEventPayload,
    job: MailJob,
  ): Promise<void> {
    if (payload.ifSmtpConfigured) {
      // Mail only if SMTP is set: skipping it is the plan, not a fault.
      this.end(job, 'skipped', { reason: 'smtp_optional', expected: true });
      return;
    }
    // Not retryable until an admin configures SMTP.
    this.end(job, 'skipped', {
      reason: 'smtp_not_configured',
      errorClass: 'misconfigured',
    });
    await this.notifyFailure(payload, 'SMTP configuration not set');
  }

  private async deliverInOrg(
    payload: MailEventPayload,
    job: MailJob,
  ): Promise<void> {
    let smtpConfig = await this.resolveSmtpConfig(payload.orgId);
    if (smtpConfig === null) {
      await this.dropUnconfigured(payload, job);
      return;
    }

    let mail: MailBody;
    try {
      mail = this.withPasswordResetLink(payload);
    } catch (error) {
      if (error instanceof PasswordLinkRefused) {
        this.end(job, 'failure', { reason: 'link_refused', errorClass: 'forbidden', error });
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      this.end(job, 'failure', {
        reason: 'link_failed',
        errorClass: 'internal',
        error,
      });
      await this.notifyFailure(payload, message);
      return;
    }
    const giveUpAt = Date.now() + MAIL_MESSAGE_BUDGET_MS;
    let last: MailSendResult | undefined;
    let reason = 'attempts_exhausted';

    while (job.attempt < MAX_ATTEMPTS) {
      job.attempt += 1;
      if (smtpConfig === undefined) {
        smtpConfig = await this.resolveSmtpConfig(payload.orgId);
        if (smtpConfig === null) {
          await this.dropUnconfigured(payload, job);
          return;
        }
      }
      const result: MailSendResult =
        smtpConfig === undefined
          ? {
              status: 'transient',
              error: 'Could not read the SMTP settings',
              errorClass: 'unavailable',
            }
          : await this.sender.send(
              mail,
              smtpConfig,
              Math.min(SMTP_SEND_DEADLINE_MS, giveUpAt - Date.now()),
            );

      if (result.status === 'sent') {
        this.end(job, 'success');
        return;
      }
      last = result;

      if (result.status === 'permanent') {
        this.endFailure(job, 'permanent', result);
        await this.notifyFailure(payload, result.error);
        return;
      }

      // indeterminate: the original send may still complete, so a retry could
      // duplicate it. unavailable: the server is cooling down after repeated
      // connection failures; the retry ladder would only stall the partition.
      if (
        result.status === 'indeterminate' ||
        result.status === 'unavailable'
      ) {
        this.endFailure(job, result.status, result);
        await this.notifyFailure(payload, result.error);
        return;
      }

      if (job.attempt < MAX_ATTEMPTS) {
        const delay = Math.min(
          BASE_BACKOFF_MS * 2 ** (job.attempt - 1),
          MAX_BACKOFF_MS,
        );
        if (giveUpAt - Date.now() - delay < MIN_ATTEMPT_MS) {
          reason = 'budget_exhausted';
          break;
        }
        this.logger.debug('mail.send.retry', {
          attempt: job.attempt,
          error_class: result.errorClass,
          ...(result.smtpCode !== undefined && { smtp_code: result.smtpCode }),
          next_retry_ms: delay,
        });
        await this.sleep(delay);
      }
    }

    // The loop ran at least once, and every pass that did not return left a failure in `last`.
    const failure = last as Exclude<MailSendResult, { status: 'sent' }>;
    this.endFailure(job, reason, failure);
    await this.notifyFailure(payload, failure.error);
  }

  private withPasswordResetLink(payload: MailEventPayload): MailBody {
    const target = payload.passwordResetLinkFor;
    if (!target) {
      return payload.mail;
    }
    // A set-password link is a credential: it is minted only for a user of the org this delivery is bound to, and only
    // into a mail addressed to that user alone. A job that asks otherwise was not written by our producer.
    // With no org bound there is nothing to hold the target to, so no link either.
    const bound = getCurrentOrgId();
    const to = payload.mail.sendEmailTo ?? [];
    const onlyTarget =
      to.length === 1 &&
      typeof to[0] === 'string' &&
      to[0].trim().toLowerCase() === String(target.email).trim().toLowerCase();
    // No copy to anyone else: CC, or any other recipient list a mail may carry now or later.
    const otherRecipients = Object.entries(payload.mail).some(
      ([field, value]) =>
        field !== 'sendEmailTo' &&
        /(to|cc|bcc)$/i.test(field) &&
        field !== 'replyTo' &&
        Array.isArray(value) &&
        value.length > 0,
    );
    if (
      bound === undefined ||
      String(target.orgId) !== bound ||
      !onlyTarget ||
      otherRecipients
    ) {
      throw new PasswordLinkRefused();
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

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /** Needs an orgId: the notification pipeline drops events without one. */
  private async notifyFailure(
    payload: MailEventPayload,
    error: string,
  ): Promise<void> {
    if (!payload.orgId) {
      // The job's mail.send.end already reported the failure; a pre-sign-in mail has no org to notify.
      this.logger.debug('Mail failure not notified: no orgId on the event');
      return;
    }

    const recipients = payload.mail.sendEmailTo ?? [];
    const orgId = payload.orgId;
    const open = this.failureNotifyState.get(orgId);
    if (open) {
      open.count += 1;
      open.lastError = error;
      if (open.recipients.length < SUPPRESSED_RECIPIENT_SAMPLE) {
        open.recipients.push(...recipients);
      }
      open.emailTemplateType ??= payload.mail.emailTemplateType;
      this.logger.debug('Mail failure notification suppressed', {
        suppressed: open.count,
      });
      return;
    }

    const timer = setTimeout(
      () => void this.flushSuppressed(orgId),
      FAILURE_NOTIFY_WINDOW_MS,
    );
    timer.unref();
    this.failureNotifyState.set(orgId, {
      count: 0,
      recipients: [],
      lastError: error,
      timer,
    });

    await this.publishFailure(orgId, {
      message: `Could not deliver "${payload.mail.subject ?? payload.mail.emailTemplateType}" to ${recipients.join(', ') || 'the recipient'}: ${error}`,
      emailTemplateType: payload.mail.emailTemplateType,
      recipients,
      error,
      suppressedFailures: 0,
    });
  }

  /** Reports failures the window swallowed, so admins learn the real count. */
  private async flushSuppressed(orgId: string): Promise<void> {
    const state = this.failureNotifyState.get(orgId);
    this.failureNotifyState.delete(orgId);
    if (!state || state.count === 0) return;

    const sample = state.recipients.slice(0, SUPPRESSED_RECIPIENT_SAMPLE);
    const more = state.count > sample.length ? ', ...' : '';
    await this.publishFailure(orgId, {
      message: `${state.count} more email(s) could not be delivered in the last ${FAILURE_NOTIFY_WINDOW_MS / 60_000} minutes (${sample.join(', ')}${more}): ${state.lastError}`,
      emailTemplateType: state.emailTemplateType,
      recipients: sample,
      error: state.lastError,
      suppressedFailures: state.count,
    });
  }

  private async publishFailure(
    orgId: string,
    details: {
      message: string;
      emailTemplateType?: string;
      recipients: string[];
      error: string;
      suppressedFailures: number;
    },
  ): Promise<void> {
    try {
      await this.notificationProducer.start();
      await this.notificationProducer.publishEvent({
        eventType: NotificationEventType.NewNotificationEvent,
        timestamp: Date.now(),
        payload: {
          orgId,
          type: 'mail.deliveryFailed',
          recipientRoles: ['admin'],
          title: 'Email delivery failed',
          message: details.message,
          severity: 'error',
          status: 'unread',
          payload: {
            emailTemplateType: details.emailTemplateType,
            recipients: details.recipients,
            error: details.error,
            suppressedFailures: details.suppressedFailures,
          },
        } as unknown as NotificationEventPayload,
      });
    } catch (publishError) {
      this.logger.error('Failed to publish mail failure notification', {
        error: publishError,
      });
    }
  }
}
