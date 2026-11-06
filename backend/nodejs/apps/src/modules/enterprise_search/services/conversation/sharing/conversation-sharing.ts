import mongoose, { ClientSession } from 'mongoose';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../../../libs/errors/http.errors';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { AppConfig } from '../../../../tokens_manager/config/config';
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
import { getUserRepository } from '../../../../../libs/repositories/user';
import { idOf } from '../../../../../libs/repositories/ids';
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

/** The `/share` and `/unshare` routes' write. */
export interface IConversationSharing {
  share(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
    accessLevel?: AccessLevel,
  ): Promise<ConversationShareResult>;
  unshare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
  ): Promise<ConversationShareResult>;
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

/** An ObjectId is hex, so 'ABC' and 'abc' name the same user: compare and de-duplicate on one form. */
const normalizedIds = (userIds: readonly string[]): string[] => [
  ...new Set(userIds.map((id) => id.toLowerCase())),
];

/**
 * Read-only sharing with users: whole-array writes, every recipient stored as `read`. Recipients
 * are given read access to the conversation's attachments and artifacts once the write is
 * committed, and lose it on unshare. People newly given access are notified.
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
    const validUsers = userIds.map((id) => ({
      userId: new mongoose.Types.ObjectId(id),
      accessLevel: 'read' as const,
    }));
    let newlySharedIds: string[] = [];
    let title: string | undefined;
    const result = await this.inTransaction(async (session) => {
      const conversation = await ChatSession.findOne({
        _id: grant.session._id,
        orgId: identity.orgId,
        isDeleted: false,
        ...kindScope(grant),
      });
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
      title = conversation.title;
      const existingSharedWith = persistableSharedWith(conversation.sharedWith);
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
      return this.write(grant, updateObject, session);
    });
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
  ): Promise<ConversationShareResult> {
    requestedIds.forEach((id) => {
      if (!mongoose.Types.ObjectId.isValid(id)) {
        throw new BadRequestError(`Invalid user ID format: ${id}`);
      }
    });
    const userIds = normalizedIds(requestedIds);
    const result = await this.inTransaction(async (session) => {
      const conversation = await ChatSession.findOne({
        _id: grant.session._id,
        orgId: identity.orgId,
        isDeleted: false,
        ...kindScope(grant),
      });
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
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
      return this.write(grant, updateObject, session);
    });
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
  ): Promise<ConversationShareResult> {
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
    return toResult(updated, 'read');
  }

  /** Read from Mongo, so a share works with a token that carries no user:read scope. */
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
