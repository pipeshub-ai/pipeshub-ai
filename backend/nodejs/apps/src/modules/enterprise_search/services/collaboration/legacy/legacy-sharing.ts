import mongoose, { ClientSession } from 'mongoose';
import { IAMServiceCommand } from '../../../../../libs/commands/iam/iam.service.command';
import { HttpMethod } from '../../../../../libs/enums/http-methods.enum';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../../../libs/errors/http.errors';
import { Logger } from '../../../../../libs/services/logger.service';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ACL_VERSION_INC } from '../../../../authz/cache/acl-version';
import { EXCLUDE_AGENT } from '../../../constants/constants';
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
import { AccessLevel } from '../domain/types';
import { ConversationAccessGrant } from '../http/conversation-context';

const logger = Logger.getInstance({ service: 'LegacySharing' });

/** What the legacy `/share` and `/unshare` responses are built from. */
export interface LegacyShareResult {
  id: unknown;
  isShared: boolean | undefined;
  shareLink?: string;
  sharedWith: unknown;
  /** The level that was stored; `read` while collaborative chats are off. */
  appliedAccessLevel: AccessLevel;
}

export interface ILegacySharing {
  share(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
  ): Promise<LegacyShareResult>;
  unshare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
  ): Promise<LegacyShareResult>;
}

const toResult = (
  doc: IChatSessionDocument,
  appliedAccessLevel: AccessLevel,
): LegacyShareResult => ({
  id: doc._id,
  isShared: doc.isShared,
  shareLink: doc.shareLink,
  sharedWith: doc.sharedWith,
  appliedAccessLevel,
});

/** The PH-01 behaviour, unchanged: whole-array writes, one IAM lookup per user, every recipient stored as `read`. */
export class LegacySharing implements ILegacySharing {
  constructor(
    private readonly iamBackend: string,
    private readonly rsAvailable: boolean,
  ) {}

  async share(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
  ): Promise<LegacyShareResult> {
    return this.inTransaction(async (session) => {
      const conversation = await ChatSession.findOne({
        _id: grant.session._id,
        orgId: identity.orgId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
      });
      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }
      const validUsers = await Promise.all(
        userIds.map(async (id) => {
          await this.assertUserExists(id, identity);
          return {
            userId: new mongoose.Types.ObjectId(id),
            accessLevel: 'read' as const,
          };
        }),
      );
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
  }

  async unshare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
  ): Promise<LegacyShareResult> {
    userIds.forEach((id) => {
      if (!mongoose.Types.ObjectId.isValid(id)) {
        throw new BadRequestError(`Invalid user ID format: ${id}`);
      }
    });
    return this.inTransaction(async (session) => {
      const conversation = await ChatSession.findOne({
        _id: grant.session._id,
        orgId: identity.orgId,
        isDeleted: false,
        ...EXCLUDE_AGENT,
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
  }

  private async write(
    grant: ConversationAccessGrant,
    updateObject: Partial<IConversation>,
    session: ClientSession | undefined,
  ): Promise<LegacyShareResult> {
    const updated = await ChatSession.findOneAndUpdate(
      { _id: grant.session._id.toString(), ...EXCLUDE_AGENT },
      { ...updateObject, ...ACL_VERSION_INC },
      { new: true, session, runValidators: true },
    );
    if (!updated) {
      throw new InternalServerError(
        'Failed to update conversation sharing settings',
      );
    }
    return toResult(updated, 'read');
  }

  private async assertUserExists(
    id: string,
    identity: CallerIdentity,
  ): Promise<void> {
    if (!mongoose.Types.ObjectId.isValid(id)) {
      throw new BadRequestError(`Invalid user ID format: ${id}`);
    }
    try {
      const userResponse = await new IAMServiceCommand({
        uri: `${this.iamBackend}/api/v1/users/${encodeURIComponent(id)}`,
        method: HttpMethod.GET,
        headers: identity.authHeaders as Record<string, string>,
      }).execute();
      if (userResponse.statusCode !== 200) {
        throw new BadRequestError(`User not found: ${id}`);
      }
    } catch {
      logger.debug(`User does not exist: ${id}`);
      throw new BadRequestError(`User not found: ${id}`);
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
