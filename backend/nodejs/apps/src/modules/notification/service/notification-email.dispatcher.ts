import mongoose from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import { TtlCache } from '../../../libs/utils/ttl-cache';
import { EmailTemplateType } from '../../mail/middlewares/types';
import { MailProducer } from '../../mail/services/mail.producer';
import { MailEventType } from '../../mail/types/mail-event.types';
import { Users } from '../../user_management/schema/users.schema';
import { Notifications } from '../schema/notification.schema';

export type ChatEmailTemplate =
  | EmailTemplateType.ChatShared
  | EmailTemplateType.ChatOwnershipTransferred
  | EmailTemplateType.ChatMentioned;

/**
 * Broker-only hint on a notification event. Carries what the template needs and
 * nothing about the recipient: the address is resolved at dispatch time.
 */
export interface EmailIntent {
  template: ChatEmailTemplate;
  actorName: string;
  orgName: string;
  /** Absent for a mention, which grants nothing. */
  accessLevel?: 'read' | 'write';
}

export interface EmailDispatchInput {
  orgId: string | mongoose.Types.ObjectId;
  assignedTo: string | mongoose.Types.ObjectId;
  dedupeKey?: string;
  /** App-relative path, e.g. `/chat?conversationId=…`. */
  redirectLink?: string;
  emailIntent: EmailIntent;
  /** The inserted notification; with a chat id, at most one email per (recipient, type, chat) a day (F-12). */
  notification?: { id: string; type: string; sessionId?: string };
}

export interface INotificationEmailDispatcher {
  /** Never throws. Call only for a newly inserted notification (E11000 means it was already delivered). */
  dispatch(input: EmailDispatchInput): Promise<void>;
}

export const NOTIFICATION_EMAIL_DISPATCHER = Symbol.for(
  'INotificationEmailDispatcher',
);

export interface IRecipientEmailLookup {
  findEmail(orgId: string, userId: string): Promise<string | null>;
}

export const usersRecipientEmailLookup: IRecipientEmailLookup = {
  async findEmail(orgId, userId) {
    const user = await Users.findOne({
      _id: userId,
      orgId,
      isDeleted: { $ne: true },
      isDisabled: { $ne: true },
      kind: { $ne: 'service' },
    })
      .select('email')
      .lean<{ email?: string }>();
    const email = user?.email;
    return email !== undefined && email !== '' ? email : null;
  },
};

export interface IRecentNotificationLookup {
  /** True when the recipient got another notification of this type for this chat since `since`, archived ones included. */
  hasEarlier(q: {
    orgId: string;
    userId: string;
    type: string;
    sessionId: string;
    notificationId: string;
    since: Date;
  }): Promise<boolean>;
}

export const mongoRecentNotificationLookup: IRecentNotificationLookup = {
  async hasEarlier(q) {
    const hit = await Notifications.exists({
      orgId: q.orgId,
      assignedTo: q.userId,
      type: q.type,
      'payload.sessionId': q.sessionId,
      _id: { $ne: q.notificationId },
      createdAt: { $gte: q.since },
    });
    return hit !== null;
  },
};

export const NOTIFICATION_SETTINGS_PATH = '/workspace/profile';

const SUBJECTS: Record<ChatEmailTemplate, (actorName: string) => string> = {
  [EmailTemplateType.ChatShared]: (actor) =>
    `${actor} shared a conversation with you`,
  [EmailTemplateType.ChatOwnershipTransferred]: (actor) =>
    `${actor} transferred a conversation to you`,
  [EmailTemplateType.ChatMentioned]: (actor) =>
    `${actor} mentioned you in a conversation`,
};

const SENT_KEY_TTL_MS = 24 * 60 * 60 * 1000;
const SENT_KEY_MAX = 10_000;

export class NotificationEmailDispatcher
  implements INotificationEmailDispatcher
{
  private readonly sent = new TtlCache<true>(SENT_KEY_TTL_MS, SENT_KEY_MAX);

  constructor(
    private readonly mailProducer: Pick<MailProducer, 'publishEvent'>,
    private readonly recipients: IRecipientEmailLookup,
    private readonly frontendUrl: string,
    private readonly logger: Logger,
    private readonly recent: IRecentNotificationLookup = mongoRecentNotificationLookup,
  ) {}

  async dispatch(input: EmailDispatchInput): Promise<void> {
    const { dedupeKey, emailIntent } = input;
    const orgId = String(input.orgId);
    const userId = String(input.assignedTo);
    try {
      if (dedupeKey === undefined || dedupeKey === '') {
        this.logger.warn('Chat email skipped: notification has no dedupeKey', {
          orgId,
          userId,
        });
        return;
      }
      const sentKey = `${userId}:${dedupeKey}`;
      if (this.sent.get(sentKey)) {
        return;
      }
      const link = this.openPath(input.redirectLink);
      if (link === null) {
        this.logger.warn(
          'Chat email skipped: notification has no usable link',
          {
            orgId,
            userId,
            dedupeKey,
          },
        );
        return;
      }
      if (await this.emailedToday(orgId, userId, input.notification)) {
        this.logger.info(
          'Chat email skipped: already notified about this chat today',
          {
            orgId,
            userId,
            dedupeKey,
          },
        );
        return;
      }
      const email = await this.recipients.findEmail(orgId, userId);
      if (email === null) {
        this.logger.info(
          'Chat email skipped: recipient not found or has no email',
          {
            orgId,
            userId,
            dedupeKey,
          },
        );
        return;
      }

      // Marked before publishing: a failed publish is not retried (at-most-once).
      this.sent.set(sentKey, true);
      const base = this.frontendUrl.replace(/\/+$/, '');
      await this.mailProducer.publishEvent({
        eventType: MailEventType.SendMailEvent,
        timestamp: Date.now(),
        payload: {
          orgId,
          mail: {
            orgId,
            productName: 'PIP',
            emailTemplateType: emailIntent.template,
            isAutoEmail: false,
            fromEmailDomain: 'noreply@contextualml.com',
            sendEmailTo: [email],
            subject: SUBJECTS[emailIntent.template](emailIntent.actorName),
            templateData: {
              actorName: emailIntent.actorName,
              orgName: emailIntent.orgName,
              ...(emailIntent.accessLevel !== undefined && {
                accessLevel: emailIntent.accessLevel,
              }),
              openUrl: `${base}${link}`,
              settingsUrl: `${base}${NOTIFICATION_SETTINGS_PATH}`,
            },
          },
        },
      });
    } catch (error) {
      this.logger.error('Chat email dispatch failed', {
        orgId,
        userId,
        dedupeKey,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  /** A share/unshare loop must not mail the recipient on every round (F-12); the in-app row still follows each share. */
  private async emailedToday(
    orgId: string,
    userId: string,
    notification: EmailDispatchInput['notification'],
  ): Promise<boolean> {
    if (notification?.sessionId === undefined) {
      return false;
    }
    return this.recent.hasEarlier({
      orgId,
      userId,
      type: notification.type,
      sessionId: notification.sessionId,
      notificationId: notification.id,
      since: new Date(Date.now() - SENT_KEY_TTL_MS),
    });
  }

  private openPath(redirectLink: string | undefined): string | null {
    if (
      redirectLink === undefined ||
      !redirectLink.startsWith('/') ||
      redirectLink.startsWith('//')
    ) {
      return null;
    }
    return redirectLink;
  }
}
