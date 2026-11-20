import mongoose, { ClientSession } from 'mongoose';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../../../libs/errors/http.errors';
import { idOf } from '../../../../../libs/repositories/ids';
import { getUserRepository } from '../../../../../libs/repositories/user';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { AppConfig } from '../../../../tokens_manager/config/config';
import type {
  ParticipantsChanged,
  SessionParticipantRecorder,
} from '../../../../agent-harness/ports/session-participants';
import {
  inDurableTransaction,
  type StoreSession,
} from '../../../../../libs/repositories/agent-harness/durable-transaction';
import { mongoSession } from '../../../../../libs/repositories/agent-harness/durable-transaction/mongo';
import { sessionParticipantsOf } from '../../../../../libs/repositories/agent-harness/session-roles/mongo';
import { EXCLUDE_AGENT, ONLY_AGENT } from '../../../constants/constants';
import { ChatSession } from '../../../schema/chat.session.schema';
import {
  IChatSessionDocument,
  IConversation,
} from '../../../types/conversation.interfaces';
import {
  persistableSharedWith,
  sharedWithUserId,
  userSharedWithRows,
} from '../../../utils/utils';
import { notifyChatShared } from '../../../utils/share-notification';
import { AccessLevel } from '../domain/types';
import { ShareChange } from './chat-content-access';
import { ConversationAccessGrant } from '../http/conversation-context';
import { conversationExtensions } from '../conversation-extensions';

/** What the `/share` and `/unshare` responses are built from. */
export interface ConversationShareResult {
  id: unknown;
  isShared: boolean | undefined;
  shareLink?: string;
  sharedWith: unknown;
  /** The level that was stored. */
  appliedAccessLevel: AccessLevel;
}

/**
 * The harness's record of who may read a conversation (SS1), asked per request by the route; undefined while the
 * harness is off. Given one, a share and its record commit in one durable transaction, or neither does.
 */
export type ParticipantRecorder = SessionParticipantRecorder<StoreSession>;

/** The `/share` and `/unshare` routes' write. */
export interface IConversationSharing {
  share(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
    accessLevel?: AccessLevel,
    participants?: ParticipantRecorder,
  ): Promise<ConversationShareResult>;
  unshare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
    participants?: ParticipantRecorder,
  ): Promise<ConversationShareResult>;
}

/** A share write and who may read the conversation before and after it (SS1). */
interface ParticipantChange {
  result: ConversationShareResult;
  updated: IChatSessionDocument;
}

const toResult = (
  doc: IChatSessionDocument,
  appliedAccessLevel: AccessLevel,
): ConversationShareResult => ({
  id: doc._id,
  isShared: doc.isShared,
  shareLink: doc.shareLink,
  sharedWith: doc.sharedWith,
  appliedAccessLevel,
});

/** Plain chats and agent chats share one collection: the kind keeps a share on its own kind. */
const kindScope = (grant: ConversationAccessGrant): Record<string, unknown> =>
  grant.session.agentKey !== undefined
    ? { ...ONLY_AGENT, agentKey: grant.session.agentKey }
    : EXCLUDE_AGENT;

/** The inbound request's id, for the participant record's log line (never journaled). */
const requestIdOf = (identity: CallerIdentity): { requestId?: string } => {
  const id = (identity.requestKey as { context?: { requestId?: unknown } })
    .context?.requestId;
  return typeof id === 'string' ? { requestId: id } : {};
};

/** An ObjectId is hex, so 'ABC' and 'abc' name the same user: compare and de-duplicate on one form. */
const normalizedIds = (userIds: readonly string[]): string[] => [
  ...new Set(userIds.map((id) => id.toLowerCase())),
];

/**
 * Sharing with users: whole-array writes, each recipient stored with the level asked for (`read`
 * unless `write` is asked; an agent chat only `read`). Recipients are given read access to the
 * conversation's attachments and artifacts once the write is committed, and lose it on unshare.
 * People newly given access are notified. With the harness's participant recorder, the write and
 * its record commit together.
 */
export class ConversationSharing implements IConversationSharing {
  constructor(
    private readonly appConfig: AppConfig,
    private readonly rsAvailable: boolean,
  ) {}

  async share(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    requestedIds: readonly string[],
    accessLevel?: AccessLevel,
    participants?: ParticipantRecorder,
  ): Promise<ConversationShareResult> {
    const userIds = normalizedIds(requestedIds);
    if (grant.session.agentKey !== undefined) {
      if (accessLevel !== undefined && accessLevel !== 'read') {
        throw new BadRequestError(
          "Agent conversations can only be shared with 'read' access",
        );
      }
      if (userIds.includes(identity.userId.toLowerCase())) {
        throw new BadRequestError(
          'You cannot share a conversation with yourself',
        );
      }
    }
    // Checked before the transaction opens: one query, and no lookup while it is held.
    await this.assertOrgUsers(userIds, identity);
    // A write share is stored as write (OWD-19); the guards decide what each level may do.
    const level: AccessLevel = accessLevel ?? 'read';
    const validUsers = userIds.map((id) => ({
      userId: new mongoose.Types.ObjectId(id),
      accessLevel: level,
    }));
    let newlySharedIds: string[] = [];
    let title: string | undefined;
    const result = await this.withParticipantRecord(
      participants,
      grant,
      identity,
      async (session, before) => {
        const conversation = await ChatSession.findOne({
          _id: grant.session._id,
          orgId: identity.orgId,
          isDeleted: false,
          ...kindScope(grant),
        });
        if (!conversation) {
          throw new NotFoundError('Conversation not found or unauthorized');
        }
        // Read before the merge below, which edits the shares in place.
        before(conversation);
        title = conversation.title;
        const existingSharedWith = persistableSharedWith(
          conversation.sharedWith,
        );
        // Only users who did not already have access are told; re-sharing is not news.
        const alreadyShared = new Set(
          userSharedWithRows(existingSharedWith).map((share) =>
            (sharedWithUserId(share) ?? '').toLowerCase(),
          ),
        );
        newlySharedIds = userIds.filter((id) => !alreadyShared.has(id));
        const existingUserMap = new Map(
          userSharedWithRows(existingSharedWith).map(
            (share) => [sharedWithUserId(share) ?? '', share] as const,
          ),
        );
        const mergedSharedWith = [...existingSharedWith];
        for (const newUser of validUsers) {
          const existingUser = existingUserMap.get(newUser.userId.toString());
          if (existingUser) {
            existingUser.accessLevel = newUser.accessLevel;
          } else {
            mergedSharedWith.push(newUser);
          }
        }
        const updateObject: Partial<IConversation> = {
          isShared: true,
          sharedWith: mergedSharedWith,
        };
        return this.write(grant, updateObject, session, level);
      },
    );
    await conversationExtensions().content.shared(
      this.shareChange(grant, identity, userIds),
    );
    await notifyChatShared({
      orgId: identity.orgId,
      ownerId: identity.userId,
      recipientIds: newlySharedIds,
      conversationId: grant.session._id.toString(),
      chatTitle: title,
      agentKey: grant.session.agentKey,
    });
    return result;
  }

  async unshare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    requestedIds: readonly string[],
    participants?: ParticipantRecorder,
  ): Promise<ConversationShareResult> {
    requestedIds.forEach((id) => {
      if (!mongoose.Types.ObjectId.isValid(id)) {
        throw new BadRequestError(`Invalid user ID format: ${id}`);
      }
    });
    const userIds = normalizedIds(requestedIds);
    const result = await this.withParticipantRecord(
      participants,
      grant,
      identity,
      async (session, before) => {
        const conversation = await ChatSession.findOne({
          _id: grant.session._id,
          orgId: identity.orgId,
          isDeleted: false,
          ...kindScope(grant),
        });
        if (!conversation) {
          throw new NotFoundError('Conversation not found or unauthorized');
        }
        before(conversation);
        const updatedSharedWith = persistableSharedWith(
          conversation.sharedWith,
        ).filter((share) => {
          const id = sharedWithUserId(share);
          return id === undefined || !userIds.includes(id.toLowerCase());
        });
        const updateObject: Partial<IConversation> = {
          sharedWith: updatedSharedWith,
        };
        if (updatedSharedWith.length === 0) {
          updateObject.isShared = false;
          updateObject.shareLink = undefined;
        }
        return this.write(grant, updateObject, session, 'read');
      },
    );
    await conversationExtensions().content.unshared(
      this.shareChange(grant, identity, userIds),
    );
    return result;
  }

  private shareChange(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
  ): ShareChange {
    return {
      appConfig: this.appConfig,
      conversationId: grant.session._id.toString(),
      ownerId: identity.userId,
      orgId: identity.orgId,
      userIds,
    };
  }

  private async write(
    grant: ConversationAccessGrant,
    updateObject: Partial<IConversation>,
    session: ClientSession | undefined,
    appliedAccessLevel: AccessLevel,
  ): Promise<ParticipantChange> {
    const updated = await ChatSession.findOneAndUpdate(
      { _id: grant.session._id.toString(), ...kindScope(grant) },
      { ...updateObject, ...conversationExtensions().acl.versionBump() },
      { returnDocument: 'after', session, runValidators: true },
    );
    if (!updated) {
      throw new InternalServerError(
        'Failed to update conversation sharing settings',
      );
    }
    return { result: toResult(updated, appliedAccessLevel), updated };
  }

  /**
   * Runs a share change with the harness's record of who may read the conversation (SS1): the
   * change and its record commit in one durable transaction of the document store, or neither
   * does. Without a recorder (the harness off) the change runs in its own transaction as before.
   * `before` takes the conversation as it was read, ahead of any edit.
   */
  private async withParticipantRecord(
    recorder: ParticipantRecorder | undefined,
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    change: (
      session: ClientSession | undefined,
      before: (conversation: IChatSessionDocument) => void,
    ) => Promise<ParticipantChange>,
  ): Promise<ConversationShareResult> {
    if (recorder === undefined) {
      return (await this.inTransaction((session) => change(session, () => {})))
        .result;
    }
    return inDurableTransaction(async (tx) => {
      let before: ParticipantsChanged['before'] | undefined;
      const { result, updated } = await change(
        mongoSession(tx),
        (conversation) => {
          before = sessionParticipantsOf(conversation);
        },
      );
      await recorder.participantsChanged(
        {
          orgId: identity.orgId,
          sessionId: grant.session._id.toString(),
          actorId: identity.userId,
          before: before ?? sessionParticipantsOf(updated),
          after: sessionParticipantsOf(updated),
          ...requestIdOf(identity),
        },
        tx,
      );
      return result;
    });
  }

  /** Read from the document store, so a share works with a token that carries no user:read scope. */
  private async assertOrgUsers(
    userIds: readonly string[],
    identity: CallerIdentity,
  ): Promise<void> {
    const invalid = userIds.find((id) => !mongoose.Types.ObjectId.isValid(id));
    if (invalid !== undefined) {
      throw new BadRequestError(`Invalid user ID format: ${invalid}`);
    }
    const orgUserIds = new Set(
      (
        await getUserRepository().listLiveByIds(identity.orgId, [...userIds])
      ).map((user) => idOf(user._id)),
    );
    const unknownId = userIds.find((id) => !orgUserIds.has(id));
    if (unknownId !== undefined) {
      throw new BadRequestError(`User not found: ${unknownId}`);
    }
  }

  private async inTransaction<T>(
    work: (session: ClientSession | undefined) => Promise<T>,
  ): Promise<T> {
    if (!this.rsAvailable) {
      return work(undefined);
    }
    const session = await mongoose.startSession();
    try {
      session.startTransaction();
      const result = await work(session);
      await session.commitTransaction();
      return result;
    } catch (error) {
      if (session.inTransaction()) {
        await session.abortTransaction();
      }
      throw error;
    } finally {
      await session.endSession();
    }
  }
}
