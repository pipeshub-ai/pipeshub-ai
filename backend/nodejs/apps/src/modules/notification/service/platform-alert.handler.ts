import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { enforcing, forOrg } from '../../../libs/context/orgContext';
import { ctxRequired, signEnabled } from '../../../libs/context/envelope';
import { getUserRepository } from '../../../libs/repositories/user';
import { MailProducer } from '../../mail/services/mail.producer';
import { MailEventType } from '../../mail/types/mail-event.types';
import { EmailTemplateType } from '../../mail/middlewares/types';
import { NotificationService } from './notification.service';
import { deliverToOrg } from './notification.delivery';
import type { NotificationBrokerMessage } from '../utils/notification-payload.resolver';
import type { PlatformRecipientOrg } from '../utils/platform-admin-recipients';
import { buildSandboxCapacityMail } from '../utils/sandbox-capacity-mail';
import {
  SandboxCapacityAlert,
  isExpired,
  parseSandboxCapacityAlert,
  storedAlertFields,
} from '../utils/sandbox-capacity-alert';

/** `audience` of an event for the installation's admins rather than one org's users. */
export const PLATFORM_ADMINS_AUDIENCE = 'platformAdmins';

/** Container key of the function that picks those admins (`resolvePlatformAdminRecipients` in `src/config.ts`). */
export const PLATFORM_ADMIN_RECIPIENTS = 'PlatformAdminRecipients';

export type PlatformAdminRecipientsResolver = () => Promise<
  PlatformRecipientOrg[]
>;

const HOUR_MS = 3600_000;
/** Alerts of one level whose emails are queued per hour, across all events; later ones are in-app only. */
export const MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR = 4;
/** Alerts of one type delivered per hour, across all events; later ones are dropped. */
export const MAX_ALERTS_PER_TYPE_PER_HOUR = 30;
/** Alert ids remembered, so an event the broker delivers again is not delivered twice. */
export const DELIVERED_IDS_KEPT = 1000;
const DELIVERED_IDS_TTL_MS = 24 * HOUR_MS;
/** A skipped-event warning of one kind is logged at most this often (DEBUG in between). */
const SKIP_WARNING_EVERY_MS = 10 * 60_000;

export function isPlatformAudience(raw: unknown): boolean {
  return (
    raw != null &&
    typeof raw === 'object' &&
    (raw as Record<string, unknown>).audience === PLATFORM_ADMINS_AUDIENCE
  );
}

/**
 * Why platform alerts cannot arrive with the current envelope settings, or
 * null. The alerts are about the whole installation and have no org to sign
 * with, so a consumer that requires a signed context rejects every one.
 */
export function platformAlertsBlockedByEnvelope(): string | null {
  if (ctxRequired()) return 'ENVELOPE_CTX_REQUIRED is on';
  let enforce: boolean;
  try {
    enforce = enforcing();
  } catch {
    // An invalid mode fails the app's startup elsewhere; it is not enforce.
    enforce = false;
  }
  if (signEnabled() && enforce) {
    return 'ENVELOPE_SIGN is on with ORG_CONTEXT_MODE=enforce';
  }
  return null;
}

/** Logs one ERROR at startup when the envelope settings drop every platform alert. */
export function logIfPlatformAlertsBlocked(logger: Logger): void {
  const why = platformAlertsBlockedByEnvelope();
  if (!why) return;
  logger.error(
    'Code sandbox load alerts will not be delivered: they have no org to sign, and ' +
      `unsigned notification messages are rejected (${why})`,
    { reason: why },
  );
}

/** At most *max* events per key in any hour. */
class HourlyLimit {
  private readonly seen = new Map<string, number[]>();

  constructor(private readonly max: number) {}

  allow(key: string, now: number): boolean {
    const recent = (this.seen.get(key) ?? []).filter((t) => now - t < HOUR_MS);
    const ok = recent.length < this.max;
    if (ok) recent.push(now);
    this.seen.set(key, recent);
    return ok;
  }
}

/**
 * Delivers a code sandbox load alert to the installation's admins: an in-app
 * notification for each recipient `src/config.ts` picks, and an email to each
 * of them unless the event says `email: false`.
 *
 * Only the sandbox capacity types are accepted, and only their enums, safe
 * identifiers and bounded numbers are read (`sandbox-capacity-alert.ts`); the
 * text and the link are built here. An alert id already delivered is skipped,
 * and deliveries and emails are capped per hour, so a flood of events cannot
 * fill the inbox or the mail queue.
 */
@injectable()
export class PlatformAlertHandler {
  private readonly delivered = new Map<string, number>();
  private readonly deliveries = new HourlyLimit(MAX_ALERTS_PER_TYPE_PER_HOUR);
  private readonly emails = new HourlyLimit(
    MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR,
  );
  private readonly warnedAt = new Map<string, number>();

  constructor(
    @inject('Logger') private readonly logger: Logger,
    @inject(NotificationService)
    private readonly notificationService: NotificationService,
    @inject(MailProducer) private readonly mailProducer: MailProducer,
    @inject(PLATFORM_ADMIN_RECIPIENTS)
    private readonly resolveRecipients: PlatformAdminRecipientsResolver,
  ) {}

  /** WARN the first time per *key* in each period, DEBUG otherwise. */
  private warnLimited(
    key: string,
    message: string,
    meta: Record<string, unknown>,
  ): void {
    const now = Date.now();
    const last = this.warnedAt.get(key);
    if (last !== undefined && now - last < SKIP_WARNING_EVERY_MS) {
      this.logger.debug(message, meta);
      return;
    }
    this.warnedAt.set(key, now);
    this.logger.warn(message, meta);
  }

  private alreadyDelivered(alertId: string, now: number): boolean {
    const at = this.delivered.get(alertId);
    return at !== undefined && now - at < DELIVERED_IDS_TTL_MS;
  }

  private remember(alertId: string, now: number): void {
    this.delivered.delete(alertId);
    this.delivered.set(alertId, now);
    while (this.delivered.size > DELIVERED_IDS_KEPT) {
      const oldest = this.delivered.keys().next().value as string;
      this.delivered.delete(oldest);
    }
  }

  /**
   * Store errors from the in-app part are thrown, so the broker can deliver
   * the event again. Mail is queued only after every org's in-app part
   * succeeded, and its errors are logged, never thrown: a redelivery must
   * not send the email twice.
   */
  async handle(raw: unknown): Promise<void> {
    const now = Date.now();
    const parsed = parseSandboxCapacityAlert(raw, now);
    if (!parsed.ok) {
      this.warnLimited(
        `invalid:${parsed.reason}`,
        'Platform alert skipped: invalid event',
        { reason: parsed.reason, type: parsed.type },
      );
      return;
    }
    const alert = parsed.alert;
    const { type, alertId } = alert;
    if (isExpired(alert, now)) {
      this.warnLimited(`expired:${type}`, 'Platform alert skipped: expired', {
        type,
        alertId,
        expiresAt: new Date(alert.expiresAtMs).toISOString(),
      });
      return;
    }
    if (this.alreadyDelivered(alertId, now)) {
      this.logger.debug('Platform alert skipped: already delivered', {
        type,
        alertId,
      });
      return;
    }
    if (!this.deliveries.allow(type, now)) {
      this.warnLimited(
        `rate:${type}`,
        'Platform alert skipped: too many this hour',
        { type, alertId, maxPerHour: MAX_ALERTS_PER_TYPE_PER_HOUR },
      );
      return;
    }

    const orgs = await this.resolveRecipients();
    if (orgs.length === 0) {
      this.logger.warn('Platform alert not delivered: no recipients', {
        type,
        alertId,
      });
      return;
    }

    let recipients = 0;
    for (const org of orgs) {
      const delivered = await deliverToOrg(
        this.orgEvent(alert, org),
        this.notificationService,
        this.logger,
      );
      recipients += delivered;
      this.logger.debug('Platform alert delivered to org', {
        type,
        alertId,
        orgId: org.orgId,
        recipients: delivered,
      });
    }
    this.remember(alertId, now);

    let emailQueued = 0;
    if (alert.email) {
      if (this.emails.allow(alert.kind, now)) {
        for (const org of orgs) {
          emailQueued += await this.queueEmail(alert, org);
        }
      } else {
        this.warnLimited(
          `email-rate:${alert.kind}`,
          'Platform alert email skipped: too many this hour',
          { type, alertId, maxPerHour: MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR },
        );
      }
    }

    this.logger.info('Platform alert delivered', {
      type,
      alertId,
      orgs: orgs.length,
      recipients,
      emailQueued,
    });
  }

  private orgEvent(
    alert: SandboxCapacityAlert,
    org: PlatformRecipientOrg,
  ): NotificationBrokerMessage {
    return {
      ...storedAlertFields(alert),
      orgId: org.orgId,
      recipientUserIds: org.userIds,
      isDeleted: false,
    };
  }

  /**
   * Queues one mail job per admin of *org*, so no admin sees the others'
   * addresses and a long list never meets a server's recipient limit.
   * Returns how many were queued. Never throws.
   */
  private async queueEmail(
    alert: SandboxCapacityAlert,
    org: PlatformRecipientOrg,
  ): Promise<number> {
    const { type, alertId } = alert;
    const logFailure = (error: unknown): void => {
      this.logger.error('Platform alert email not queued', {
        type,
        alertId,
        orgId: org.orgId,
        error: error instanceof Error ? error.message : String(error),
      });
    };
    try {
      const mail = buildSandboxCapacityMail(alert);
      if (!mail) return 0;
      const emails = await forOrg(
        org.orgId,
        'platform-alert-mail',
        async () => {
          const users = getUserRepository();
          const [live, serviceIds] = await Promise.all([
            users.listLiveByIds(org.orgId, org.userIds),
            users.serviceAccountIdsAmong(org.orgId, org.userIds),
          ]);
          const service = new Set(serviceIds.map(String));
          return live
            .filter((user) => !service.has(user._id))
            .map((user) => user.email)
            .filter(
              (email): email is string =>
                typeof email === 'string' && email.trim() !== '',
            );
        },
      );
      let queued = 0;
      for (const email of new Set(emails)) {
        try {
          await this.mailProducer.publishEvent({
            eventType: MailEventType.SendMailEvent,
            timestamp: Date.now(),
            payload: {
              mail: {
                orgId: org.orgId,
                emailTemplateType: EmailTemplateType.SandboxCapacityAlert,
                sendEmailTo: [email],
                subject: mail.subject,
                templateData: mail.templateData,
              },
              orgId: org.orgId,
              ifSmtpConfigured: true,
            },
          });
          queued += 1;
        } catch (error) {
          logFailure(error);
        }
      }
      return queued;
    } catch (error) {
      logFailure(error);
      return 0;
    }
  }
}
