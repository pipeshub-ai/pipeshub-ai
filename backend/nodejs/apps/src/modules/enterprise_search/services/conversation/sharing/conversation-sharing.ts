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
  findOrgUserIds,
  persistableSharedWith,
  sharedWithUserId,
  userSharedWithRows,
} from '../../../utils/utils';
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

/** An ObjectId is hex, so 'ABC…' and 'abc…' are the same user. Compare and de-duplicate on one form. */
const normalizedIds = (userIds: readonly string[]): string[] => [
  ...new Set(userIds.map((id) => String(id).toLowerCase())),
];

const assertObjectIds = (userIds: readonly string[]): void => {
  userIds.forEach((id) => {
    if (!mongoose.Types.ObjectId.isValid(id)) {
      throw new BadRequestError(`Invalid user ID format: ${id}`);
    }
  });
};

/**
 * Read-only sharing with users: whole-array writes, every recipient stored as `read`. Recipients
 * are given read access to the conversation's attachments and artifacts once the write is
 * committed, and lose it on unshare. `kind` is which sessions it may touch: plain chats and agent
 * chats live in one collection, so `/conversations/:id/share` never shares an agent chat and the
 * other way round.
 */
export class ConversationSharing implements IConversationSharing {
  constructor(
    private readonly appConfig: AppConfig,
    private readonly rsAvailable: boolean,
    private readonly kind: 'chat' | 'agent' = 'chat',
  ) {}

  private scopeOf(grant: ConversationAccessGrant): Record<string, unknown> {
    return this.kind === 'agent'
      ? { ...ONLY_AGENT, agentKey: grant.session.agentKey }
      : EXCLUDE_AGENT;
  }

  async share(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    requestedUserIds: readonly string[],
  ): Promise<ConversationShareResult> {
    const userIds = normalizedIds(requestedUserIds);
    if (
      this.kind === 'agent' &&
      userIds.includes(identity.userId.toLowerCase())
    ) {
      throw new BadRequestError('You cannot share a conversation with yourself');
    }
    // Checked before the transaction opens: one query, and no network call while it is held.
    assertObjectIds(userIds);
    const orgUserIds = await findOrgUserIds(identity.orgId, userIds);
    const unknownId = userIds.find((id) => !orgUserIds.has(id));
    if (unknownId) {
      throw new BadRequestError(`User not found: ${unknownId}`);
    }
    const validUsers = userIds.map((id) => ({
      userId: new mongoose.Types.ObjectId(id),
      accessLevel: 'read' as const,
    }));
    const result = await this.inTransaction(async (session) => {
      const conversation = await ChatSession.findOne({
        _id: grant.session._id,
        orgId: identity.orgId,
        isDeleted: false,
        ...this.scopeOf(grant),
      });
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
      const existingSharedWith = persistableSharedWith(conversation.sharedWith);
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
    return result;
  }

  async unshare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    requestedUserIds: readonly string[],
  ): Promise<ConversationShareResult> {
    assertObjectIds(requestedUserIds);
    const userIds = normalizedIds(requestedUserIds);
    const result = await this.inTransaction(async (session) => {
      const conversation = await ChatSession.findOne({
        _id: grant.session._id,
        orgId: identity.orgId,
        isDeleted: false,
        ...this.scopeOf(grant),
      });
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
      const updatedSharedWith = persistableSharedWith(
        conversation.sharedWith,
      ).filter((share) => {
        const id = sharedWithUserId(share);
        return id === undefined || !userIds.includes(id);
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
      { _id: grant.session._id.toString(), ...this.scopeOf(grant) },
      { ...updateObject, ...conversationExtensions().acl.versionBump() },
      { new: true, session, runValidators: true },
    );
    if (!updated) {
      throw new InternalServerError(
        'Failed to update conversation sharing settings',
      );
    }
    return toResult(updated, 'read');
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
