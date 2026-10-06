import { Types } from 'mongoose';
import {
  IAuditWriter,
  MongoAuditWriter,
} from '../../../../../libs/audit/audit.writer';
import { UserNotificationPreferences } from '../../../../notification/schema/user-notification-preferences.schema';
import { statusFor } from '../turn/turn-lifecycle';
import { ChatSession } from '../../../schema/chat.session.schema';
import { ChatSessionReadState } from '../../../schema/chat.session.read-state.schema';

const defaultAuditWriter: IAuditWriter = new MongoAuditWriter();

export interface OffboardResult {
  /** Chats the user was pulled from as a collaborator. */
  removedFrom: number;
  /** Shared chats the user owns; they stay, readable by their collaborators. */
  ownedSharedChats: number;
}

export interface OffboardOptions {
  actorUserId?: string;
  requestId?: string;
}

const withoutId = (field: string, id: Types.ObjectId): object => ({
  $filter: {
    input: { $ifNull: [`$${field}`, []] },
    as: 'entry',
    cond: { $ne: ['$$entry', id] },
  },
});

// One pass bumps `rev` and `aclVersion` once per chat, whichever rows it removed.
const recomputeSharedStage = {
  $set: {
    isShared: { $gt: [{ $size: '$sharedWith' }, 0] },
    rev: { $add: [{ $ifNull: ['$rev', 0] }, 1] },
    aclVersion: { $add: [{ $ifNull: ['$aclVersion', 0] }, 1] },
  },
};

/**
 * Offboarding for the collaboration data in Mongo. Static because the callers (user and team
 * deletion) live in a different container from the collaboration services (73 §3.9). Every
 * method is idempotent: a re-run matches nothing.
 */
export class ChatCollaboratorCleanup {
  static async removeUser(
    orgId: string,
    userId: string,
    opts: OffboardOptions = {},
    auditWriter: IAuditWriter = defaultAuditWriter,
  ): Promise<OffboardResult> {
    const org = new Types.ObjectId(orgId);
    const uid = new Types.ObjectId(userId);

    const pulled = await ChatSession.updateMany(
      { orgId: org, 'sharedWith.userId': uid },
      [
        {
          $set: {
            sharedWith: {
              $filter: {
                input: { $ifNull: ['$sharedWith', []] },
                as: 'row',
                cond: { $ne: ['$$row.userId', uid] },
              },
            },
            hiddenFor: withoutId('hiddenFor', uid),
            archivedFor: withoutId('archivedFor', uid),
          },
        },
        recomputeSharedStage,
      ],
    );
    // Ids left by a Leave (no access row). Per-user state, so no rev bump (PH-05 5f).
    const leftovers = await ChatSession.updateMany(
      {
        orgId: org,
        $or: [{ hiddenFor: uid }, { archivedFor: uid }],
      },
      { $pull: { hiddenFor: uid, archivedFor: uid } },
    );

    // Not a release: the run's next fenced write fails its lease renewal and is refused
    // (LeaseLostError) instead of landing on a session the user can no longer touch.
    const cleared = await ChatSession.updateMany(
      { orgId: org, 'activeRun.userId': uid },
      {
        $set: { activeRun: null, status: statusFor('stopped') },
        $inc: { rev: 1 },
      },
    );
    const readStates = await ChatSessionReadState.deleteMany({
      orgId: org,
      userId: uid,
    });
    const preferences = await UserNotificationPreferences.deleteOne({
      orgId: org,
      userId: uid,
    });
    const ownedSharedChats = await ChatSession.countDocuments({
      orgId: org,
      userId: uid,
      isShared: true,
      isDeleted: { $ne: true },
    });

    const result: OffboardResult = {
      removedFrom: pulled.modifiedCount,
      ownedSharedChats,
    };
    const changed =
      pulled.modifiedCount +
      leftovers.modifiedCount +
      cleared.modifiedCount +
      readStates.deletedCount +
      preferences.deletedCount;
    if (changed > 0) {
      await ChatCollaboratorCleanup.audit(auditWriter, org, uid, opts, {
        ...result,
        runsCleared: cleared.modifiedCount,
        readStatesDeleted: readStates.deletedCount,
      });
    }
    return result;
  }

  static async removeTeam(
    orgId: string,
    teamId: string,
  ): Promise<{ removedFrom: number }> {
    const pulled = await ChatSession.updateMany(
      { orgId: new Types.ObjectId(orgId), 'sharedWith.teamId': teamId },
      [
        {
          $set: {
            sharedWith: {
              $filter: {
                input: { $ifNull: ['$sharedWith', []] },
                as: 'row',
                cond: { $ne: ['$$row.teamId', teamId] },
              },
            },
          },
        },
        recomputeSharedStage,
      ],
    );
    return { removedFrom: pulled.modifiedCount };
  }

  // Audit is append-only evidence, not part of the cleanup; the writer swallows a failed write.
  private static async audit(
    auditWriter: IAuditWriter,
    orgId: Types.ObjectId,
    userId: Types.ObjectId,
    opts: OffboardOptions,
    after: Record<string, number>,
  ): Promise<void> {
    await auditWriter.record({
      orgId,
      actorUserId:
        opts.actorUserId !== undefined
          ? new Types.ObjectId(opts.actorUserId)
          : userId,
      action: 'chat.offboard',
      targetType: 'user',
      targetId: String(userId),
      after,
      requestId: opts.requestId,
    });
  }
}
