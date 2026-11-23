import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { enforcing, forOrg } from '../../../libs/context/orgContext';
import {
  DuplicateEnvelope,
  ctxRequired,
  signEnabled,
} from '../../../libs/context/envelope';
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
/**
 * Alert emails of one level queued per hour for one budget scope (see
 * `budgetScope`); later alerts are in-app only.
 */
export const MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR = 4;
/** Alert emails of one level queued per hour across all scopes. */
export const MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR_ALL_SCOPES = 12;
/** Alerts of one type delivered per hour for one budget scope; later ones are dropped. */
export const MAX_ALERTS_PER_TYPE_PER_HOUR = 30;
/** Alerts of one type delivered per hour across all scopes. */
export const MAX_ALERTS_PER_TYPE_PER_HOUR_ALL_SCOPES = 120;
/** Alert ids remembered, so an event the broker delivers again is not delivered twice. */
export const DELIVERED_IDS_KEPT = 1000;
const DELIVERED_IDS_TTL_MS = 24 * HOUR_MS;
/** A skipped-event warning of one kind is logged at most this often (DEBUG in between). */
const SKIP_WARNING_EVERY_MS = 10 * 60_000;

/** The alert id the AI service's publisher writes: a lowercase random UUID. */
const PUBLISHER_ALERT_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
/** Scope of events whose id is not in the publisher's format. Not a valid host. */
export const OTHER_IDS_SCOPE = '~other-ids';
/** Scope of publisher-format events without a host. Not a valid host. */
const NO_HOST_SCOPE = '~no-host';

/**
 * The rate budget an alert draws from: its host when the id has the
 * publisher's format, else one shared scope for every other id. Events are
 * not signed, so this cannot stop a sender that copies the host and the id
 * format; it only keeps malformed or other-host floods off a host's budget.
 */
export function budgetScope(alert: SandboxCapacityAlert): string {
  if (!PUBLISHER_ALERT_ID_RE.test(alert.alertId)) return OTHER_IDS_SCOPE;
  return alert.host ?? NO_HOST_SCOPE;
}

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

/** A token taken from an `HourlyBudget`, to give back if the delivery fails. */
interface BudgetToken {
  group: string;
  scope: string;
  at: number;
}

/**
 * At most *perScope* tokens per (group, scope) and *perGroup* per group in
 * any hour. A token is given back when what it paid for did not happen.
 */
class HourlyBudget {
  private readonly used = new Map<string, number[]>();

  constructor(
    private readonly perScope: number,
    private readonly perGroup: number,
  ) {}

  private recent(key: string, now: number): number[] {
    const times = (this.used.get(key) ?? []).filter((t) => now - t < HOUR_MS);
    if (times.length === 0) this.used.delete(key);
    else this.used.set(key, times);
    return times;
  }

  private static keys(group: string, scope: string): [string, string] {
    // A group is a fixed type or level name, so `|` cannot appear in it.
    return [`${group}|${scope}`, `${group}|`];
  }

  /** A token, or null (nothing taken) when the scope or the group is used up. */
  take(group: string, scope: string, now: number): BudgetToken | null {
    const [scopeKey, groupKey] = HourlyBudget.keys(group, scope);
    const inScope = this.recent(scopeKey, now);
    const inGroup = this.recent(groupKey, now);
    if (inScope.length >= this.perScope || inGroup.length >= this.perGroup) {
      return null;
    }
    this.used.set(scopeKey, [...inScope, now]);
    this.used.set(groupKey, [...inGroup, now]);
    return { group, scope, at: now };
  }

  refund(token: BudgetToken): void {
    for (const key of HourlyBudget.keys(token.group, token.scope)) {
      const times = this.used.get(key);
      if (!times) continue;
      const i = times.lastIndexOf(token.at);
      if (i >= 0) times.splice(i, 1);
      if (times.length === 0) this.used.delete(key);
    }
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
 *
 * Only a valid, unexpired, new alert takes a token, and a token is given back
 * when nothing was stored (a store error, no recipients) or no email was
 * queued, so a failure or a redelivery does not use up the hour. Budgets are
 * kept per type (deliveries) and per level (emails), each split by
 * `budgetScope`. The caps live in this process's memory.
 */
@injectable()
export class PlatformAlertHandler {
  private readonly delivered = new Map<string, number>();
  private readonly deliveries = new HourlyBudget(
    MAX_ALERTS_PER_TYPE_PER_HOUR,
    MAX_ALERTS_PER_TYPE_PER_HOUR_ALL_SCOPES,
  );
  private readonly emails = new HourlyBudget(
    MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR,
    MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR_ALL_SCOPES,
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
    const scope = budgetScope(alert);
    const token = this.deliveries.take(type, scope, now);
    if (!token) {
      this.warnLimited(
        `rate:${type}:${scope}`,
        'Platform alert skipped: too many this hour',
        { type, alertId, scope, maxPerHour: MAX_ALERTS_PER_TYPE_PER_HOUR },
      );
      return;
    }

    let orgs: PlatformRecipientOrg[];
    let recipients = 0;
    let storedBefore = 0;
    try {
      orgs = await this.resolveRecipients();
      for (const org of orgs) {
        let delivered: number;
        try {
          delivered = await deliverToOrg(
            this.orgEvent(alert, org),
            this.notificationService,
            this.logger,
          );
        } catch (error) {
          // An earlier delivery of this event stored it for this org (the durable inbox): skip the org, carry on with the rest.
          if (!(error instanceof DuplicateEnvelope)) throw error;
          storedBefore += 1;
          continue;
        }
        recipients += delivered;
        this.logger.debug('Platform alert delivered to org', {
          type,
          alertId,
          orgId: org.orgId,
          recipients: delivered,
        });
      }
    } catch (error) {
      this.deliveries.refund(token);
      throw error;
    }
    if (recipients === 0 && storedBefore > 0) {
      // A redelivery of an alert every org already has: the delivery that stored the last org owned the email, so none is queued twice.
      this.deliveries.refund(token);
      this.remember(alertId, now);
      this.logger.debug('Platform alert skipped: already delivered', {
        type,
        alertId,
        orgs: storedBefore,
      });
      return;
    }
    if (recipients === 0) {
      this.deliveries.refund(token);
      this.logger.warn('Platform alert not delivered: no recipients', {
        type,
        alertId,
        orgs: orgs.length,
      });
      return;
    }
    this.remember(alertId, now);

    let emailQueued = 0;
    if (alert.email) {
      const emailToken = this.emails.take(alert.kind, scope, now);
      if (emailToken) {
        for (const org of orgs) {
          emailQueued += await this.queueEmail(alert, org);
        }
        if (emailQueued === 0) this.emails.refund(emailToken);
      } else {
        this.warnLimited(
          `email-rate:${alert.kind}:${scope}`,
          'Platform alert email skipped: too many this hour',
          {
            type,
            alertId,
            scope,
            maxPerHour: MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR,
          },
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
