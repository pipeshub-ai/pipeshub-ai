import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { forOrg } from '../../../libs/context/orgContext';
import { getUserRepository } from '../../../libs/repositories/user';
import { MailProducer } from '../../mail/services/mail.producer';
import { MailEventType } from '../../mail/types/mail-event.types';
import { EmailTemplateType } from '../../mail/middlewares/types';
import { NotificationService } from './notification.service';
import { deliverToOrg } from './notification.delivery';
import type { NotificationBrokerMessage } from '../utils/notification-payload.resolver';
import type { PlatformRecipientOrg } from '../utils/platform-admin-recipients';
import {
  SandboxCapacityAlertPayload,
  buildSandboxCapacityMail,
} from '../utils/sandbox-capacity-mail';

/** `audience` of an event for the installation's admins rather than one org's users. */
export const PLATFORM_ADMINS_AUDIENCE = 'platformAdmins';

/** Container key of the function that picks those admins (`resolvePlatformAdminRecipients` in `src/config.ts`). */
export const PLATFORM_ADMIN_RECIPIENTS = 'PlatformAdminRecipients';

export type PlatformAdminRecipientsResolver = () => Promise<
  PlatformRecipientOrg[]
>;

const PAYLOAD_VERSION = 1;

/** The fields of a platform event that are stored on each notification. */
const STORED_FIELDS = [
  'type',
  'severity',
  'status',
  'originService',
  'title',
  'message',
  'redirectLink',
  'payload',
] as const;

interface PlatformAlertEvent {
  audience: string;
  type: string;
  title: string;
  message: string;
  redirectLink?: string;
  email?: boolean;
  payload: Record<string, unknown> & {
    v: number;
    alertId: string;
    expiresAt?: string;
  };
  [key: string]: unknown;
}

export function isPlatformAudience(raw: unknown): boolean {
  return (
    raw != null &&
    typeof raw === 'object' &&
    (raw as Record<string, unknown>).audience === PLATFORM_ADMINS_AUDIENCE
  );
}

function asPlatformEvent(raw: unknown): PlatformAlertEvent | null {
  if (!isPlatformAudience(raw)) return null;
  const msg = raw as Record<string, unknown>;
  const payload = msg.payload as Record<string, unknown> | undefined;
  if (
    typeof msg.type !== 'string' ||
    !msg.type ||
    typeof msg.title !== 'string' ||
    typeof msg.message !== 'string' ||
    payload == null ||
    typeof payload !== 'object' ||
    payload.v !== PAYLOAD_VERSION ||
    typeof payload.alertId !== 'string' ||
    !payload.alertId
  ) {
    return null;
  }
  return msg as unknown as PlatformAlertEvent;
}

/**
 * Delivers an event about the whole installation, such as a code sandbox load
 * alert: an in-app notification for each recipient `src/config.ts` picks, and,
 * when the event asks for it, one email per recipient org.
 *
 * Dedup is the publisher's job; the same event delivered twice notifies twice.
 */
@injectable()
export class PlatformAlertHandler {
  constructor(
    @inject('Logger') private readonly logger: Logger,
    @inject(NotificationService)
    private readonly notificationService: NotificationService,
    @inject(MailProducer) private readonly mailProducer: MailProducer,
    @inject(PLATFORM_ADMIN_RECIPIENTS)
    private readonly resolveRecipients: PlatformAdminRecipientsResolver,
  ) {}

  /**
   * Store errors from the in-app part are thrown, so the broker can deliver
   * the event again. Mail is queued only after every org's in-app part
   * succeeded, and its errors are logged, never thrown: a redelivery must
   * not send the email twice.
   */
  async handle(raw: unknown): Promise<void> {
    const event = asPlatformEvent(raw);
    if (!event) {
      this.logger.warn('Platform alert skipped: invalid event', { value: raw });
      return;
    }
    const alertId = event.payload.alertId;
    const expiresAt = Date.parse(event.payload.expiresAt ?? '');
    if (Number.isFinite(expiresAt) && Date.now() > expiresAt) {
      this.logger.warn('Platform alert skipped: expired', {
        type: event.type,
        alertId,
        expiresAt: event.payload.expiresAt,
      });
      return;
    }

    const orgs = await this.resolveRecipients();
    if (orgs.length === 0) {
      this.logger.warn('Platform alert not delivered: no recipients', {
        type: event.type,
        alertId,
      });
      return;
    }

    let recipients = 0;
    for (const org of orgs) {
      const delivered = await deliverToOrg(
        this.orgEvent(event, org),
        this.notificationService,
        this.logger,
      );
      recipients += delivered;
      this.logger.debug('Platform alert delivered to org', {
        type: event.type,
        alertId,
        orgId: org.orgId,
        recipients: delivered,
      });
    }

    let emailQueued = 0;
    if (event.email === true) {
      for (const org of orgs) {
        if (await this.queueEmail(event, org)) emailQueued += 1;
      }
    }

    this.logger.info('Platform alert delivered', {
      type: event.type,
      alertId,
      orgs: orgs.length,
      recipients,
      emailQueued,
    });
  }

  private orgEvent(
    event: PlatformAlertEvent,
    org: PlatformRecipientOrg,
  ): NotificationBrokerMessage {
    const stored: Record<string, unknown> = {};
    for (const key of STORED_FIELDS) {
      if (event[key] !== undefined) stored[key] = event[key];
    }
    return {
      ...stored,
      type: event.type,
      orgId: org.orgId,
      recipientUserIds: org.userIds,
      isDeleted: false,
    };
  }

  /** Whether a mail job was queued for *org*. Never throws. */
  private async queueEmail(
    event: PlatformAlertEvent,
    org: PlatformRecipientOrg,
  ): Promise<boolean> {
    try {
      const mail = buildSandboxCapacityMail(
        event,
        event.payload as SandboxCapacityAlertPayload,
      );
      if (!mail) {
        this.logger.warn(
          'Platform alert email skipped: no email for this event',
          {
            type: event.type,
            alertId: event.payload.alertId,
          },
        );
        return false;
      }
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
      if (emails.length === 0) return false;
      await this.mailProducer.publishEvent({
        eventType: MailEventType.SendMailEvent,
        timestamp: Date.now(),
        payload: {
          mail: {
            orgId: org.orgId,
            emailTemplateType: EmailTemplateType.SandboxCapacityAlert,
            sendEmailTo: emails,
            subject: mail.subject,
            templateData: mail.templateData,
          },
          orgId: org.orgId,
          ifSmtpConfigured: true,
        },
      });
      return true;
    } catch (error) {
      this.logger.error('Platform alert email not queued', {
        type: event.type,
        alertId: event.payload.alertId,
        orgId: org.orgId,
        error: error instanceof Error ? error.message : String(error),
      });
      return false;
    }
  }
}
