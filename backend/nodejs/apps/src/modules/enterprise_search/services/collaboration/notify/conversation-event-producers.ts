import { ClientSession } from 'mongoose';
import { Logger } from '../../../../../libs/services/logger.service';
import { INotificationPreferencesRepository } from '../../../../notification/repository/notification-preferences.repository';
import { ConversationAccessGrant } from '../http/conversation-context';
import { eventBaseOf, refOf } from '../mutation/mutation-context';
import { MutationEffects } from '../mutation/mutation-effects';
import { IChatAudienceRepository } from '../persistence/chat-audience.repository';
import { IReadStateRepository } from '../persistence/read-state.repository';
import { EmailTemplateType } from '../../../../mail/middlewares/types';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ScopedSession } from '../../../../authz/ports';
import { MentionRef } from '../mentions/mention.types';
import { ICollaborationNotifier } from './collaboration-notifier';
import { ICollaborationEmailIntents } from './collaboration-email-intents';
import { mentionPrincipals } from './mention-principals';
import { IRecipientResolver } from './recipient-resolver';

/** Someone who read the chat within this window is looking at it, so a bell would only be noise. */
export const ACTIVITY_STALE_MS = 2 * 60 * 1000;

/**
 * Events that do not come from a collaborator change: a finished turn and a deleted conversation.
 * The handlers that own those moments reach it through `conversationEventProducers()`.
 */
export interface IConversationEventProducers {
  /** Fire and forget from the turn's exit path: never throws. */
  turnEnded(args: {
    orgId: string;
    sessionId: string;
    senderUserId: string;
  }): Promise<void>;
  /**
   * Tells the users who authored a message. With a session the event is part of the delete's
   * transaction and a failure aborts it; without one a failure is logged.
   */
  conversationDeleted(
    grant: ConversationAccessGrant,
    session?: ClientSession,
  ): Promise<void>;
  /**
   * A stored `user_query` or `note` mentioned people. Throws on failure; the publish is idempotent per
   * (message, recipient), so a caller that can retry should. Only those two row kinds may call this.
   */
  mentioned(args: {
    session: ScopedSession;
    identity: CallerIdentity;
    actorUserId: string;
    messageId: string;
    mentions: readonly MentionRef[];
  }): Promise<void>;
}

export interface ConversationEventProducersDeps {
  audience: IChatAudienceRepository;
  readState: IReadStateRepository;
  preferences: INotificationPreferencesRepository;
  notifier: ICollaborationNotifier;
  effects: MutationEffects;
  recipients?: IRecipientResolver;
  emailIntents?: ICollaborationEmailIntents;
  logger?: Pick<Logger, 'warn' | 'error'>;
  now?: () => number;
}

const defaultLogger = Logger.getInstance({ service: 'ConversationEvents' });
const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

export class ConversationEventProducers implements IConversationEventProducers {
  private readonly logger: Pick<Logger, 'warn' | 'error'>;
  private readonly now: () => number;

  constructor(private readonly deps: ConversationEventProducersDeps) {
    this.logger = deps.logger ?? defaultLogger;
    this.now = deps.now ?? Date.now;
  }

  async turnEnded(args: {
    orgId: string;
    sessionId: string;
    senderUserId: string;
  }): Promise<void> {
    const { orgId, sessionId, senderUserId } = args;
    try {
      const audience = await this.deps.audience.activityAudience(
        orgId,
        sessionId,
      );
      if (!audience) {
        return;
      }
      const candidates = audience.userIds.filter((id) => id !== senderUserId);
      if (candidates.length === 0) {
        return;
      }
      const [recentlyRead, preferences] = await Promise.all([
        this.deps.readState.readSince(
          sessionId,
          candidates,
          new Date(this.now() - ACTIVITY_STALE_MS),
        ),
        this.deps.preferences.getMany(audience.orgId, candidates),
      ]);
      const recipientUserIds = candidates.filter((id) => {
        const prefs = preferences.get(id);
        return (
          !recentlyRead.has(id) &&
          prefs?.inApp.chatActivity !== false &&
          prefs?.mutedSessions.includes(sessionId) !== true
        );
      });
      if (recipientUserIds.length === 0) {
        return;
      }
      await this.deps.notifier.publish([
        {
          type: 'chat.activity',
          orgId: audience.orgId,
          sessionId,
          ref: audience.ref,
          actorUserId: senderUserId,
          recipientUserIds,
        },
      ]);
    } catch (error) {
      this.logger.warn('Chat activity notification failed', {
        sessionId,
        error: errorMessage(error),
      });
    }
  }

  async mentioned(args: {
    session: ScopedSession;
    identity: CallerIdentity;
    actorUserId: string;
    messageId: string;
    mentions: readonly MentionRef[];
  }): Promise<void> {
    const { session, identity, actorUserId, messageId, mentions } = args;
    const { recipients, emailIntents } = this.deps;
    if (!recipients || mentions.length === 0) {
      return;
    }
    const orgId = session.orgId.toString();
    const principals = await mentionPrincipals({
      mentions,
      session,
      recipients,
      identity,
    });
    if (principals.length === 0) {
      return;
    }
    const direct = principals.flatMap((p) =>
      p.type === 'user' && p.userId !== actorUserId ? [p.userId] : [],
    );
    const intents = await this.mentionEmailIntents(
      emailIntents,
      orgId,
      actorUserId,
      direct,
    );
    await this.deps.notifier.publish(
      [
        {
          type: 'chat.mentioned',
          orgId,
          sessionId: session._id.toString(),
          ref: refOf(session),
          actorUserId,
          messageId,
          principals,
          emailIntents: intents,
        },
      ],
      { identity },
    );
  }

  /** A failed lookup costs the email, never the in-app notification. */
  private async mentionEmailIntents(
    emailIntents: ICollaborationEmailIntents | undefined,
    orgId: string,
    actorUserId: string,
    userIds: readonly string[],
  ): ReturnType<ICollaborationEmailIntents['forDirectUsers']> {
    if (!emailIntents || userIds.length === 0) {
      return new Map();
    }
    try {
      return await emailIntents.forDirectUsers({
        orgId,
        actorUserId,
        template: EmailTemplateType.ChatMentioned,
        recipients: userIds.map((userId) => ({ userId })),
      });
    } catch (error) {
      this.logger.warn('Mention email intents failed; in-app only', {
        error: errorMessage(error),
      });
      return new Map();
    }
  }

  async conversationDeleted(
    grant: ConversationAccessGrant,
    session?: ClientSession,
  ): Promise<void> {
    const base = eventBaseOf(grant);
    try {
      const authors = await this.deps.audience.authors(
        base.orgId,
        base.sessionId,
        session ? { session } : {},
      );
      const recipientUserIds = authors.filter((id) => id !== base.actorUserId);
      if (recipientUserIds.length === 0) {
        return;
      }
      await this.deps.effects.publish(
        [{ type: 'chat.deleted', ...base, recipientUserIds }],
        session,
      );
    } catch (error) {
      if (session) {
        throw error;
      }
      this.logger.error('Chat deletion notification failed', {
        sessionId: base.sessionId,
        error: errorMessage(error),
      });
    }
  }
}

const NO_PRODUCERS: IConversationEventProducers = {
  turnEnded: () => Promise.resolve(),
  conversationDeleted: () => Promise.resolve(),
  mentioned: () => Promise.resolve(),
};

let producers: IConversationEventProducers = NO_PRODUCERS;

/** Set once at boot, like the team-id cache; until then (and in suites that never set it) nothing is emitted. */
export function useConversationEventProducers(
  next: IConversationEventProducers | undefined,
): void {
  producers = next ?? NO_PRODUCERS;
}

export const conversationEventProducers = (): IConversationEventProducers =>
  producers;
