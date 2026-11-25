/**
 * The collaborator-turns backend on MongoDB (SS2): the real admission store,
 * share predicate and conversation, for every contract that needs two users
 * in one conversation on a replica set (SS2, P2.5a, P7.3).
 */
import { Types } from 'mongoose';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { HarnessSessionRoles } from '../../../../src/libs/repositories/agent-harness/session-roles/mongo';
import { MongoRunAdmissionStore } from '../../../../src/libs/repositories/agent-harness/run-admission-store/mongo';
import { StoreClock } from '../../../../src/modules/agent-harness/domain/execution';
import { CollaboratorBackend } from './collaborator-turns-contract';
import { mongoBackend } from './harness-mongo';

/** Plays the share route on the conversation document. */
export const shareConversation = async (
  sessionId: string,
  userId: string,
  accessLevel: 'read' | 'write',
): Promise<void> => {
  await ChatSession.updateOne(
    { _id: sessionId },
    {
      $set: { isShared: true },
      $push: { sharedWith: { userId: new Types.ObjectId(userId), accessLevel } },
    },
  );
};

export const mongoCollaboratorBackend = (
  clock: StoreClock,
): CollaboratorBackend => {
  const base = mongoBackend({ clock });
  return {
    ...base,
    admissionStore: base.admissionStore as MongoRunAdmissionStore,
    roles: new HarnessSessionRoles(),
    share: (_orgId, sessionId, userId, accessLevel) =>
      shareConversation(sessionId, userId, accessLevel),
    unshare: async (_orgId, sessionId, userId) => {
      await ChatSession.updateOne(
        { _id: sessionId },
        { $pull: { sharedWith: { userId: new Types.ObjectId(userId) } } },
      );
    },
    messages: async (sessionId) =>
      (
        await ChatSessionMessage.find({ sessionId }).sort({ seq: 1 }).lean()
      ).map((message) => ({
        messageType: message.messageType,
        ...(message.senderId !== undefined && message.senderId !== null
          ? { senderId: String(message.senderId) }
          : {}),
      })),
  };
};
