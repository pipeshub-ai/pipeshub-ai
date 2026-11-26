/**
 * ADR-UI-01 D2 on MongoDB: the seen marks on `chatSessions.harnessSeen`, written through the conversation repository.
 * Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Types } from 'mongoose';
import {
  getConversationRepository,
  setConversationRepository,
} from '../../../src/libs/repositories/conversation';
import { MongoConversationRepository } from '../../../src/libs/repositories/conversation/mongo';
import { conversationSeenMarks } from '../../../src/libs/repositories/agent-harness/conversation-seen';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { describeConversationSeenContract } from './support/conversation-seen-contract';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';

const updatedAt = async (sessionId: string): Promise<number> => {
  const row = (await ChatSession.findById(sessionId, { updatedAt: 1 }).lean()) as { updatedAt?: Date } | null;
  return row?.updatedAt?.getTime() ?? 0;
};

describe('conversation seen marks on MongoDB (ADR-UI-01 D2)', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  before(() => setConversationRepository(new MongoConversationRepository()));
  after(async () => {
    setConversationRepository();
    await disconnectHarnessMongo();
  });

  describeConversationSeenContract('chatSessions.harnessSeen', () => ({
    marks: conversationSeenMarks(),
    conversation: async (orgId, userId) =>
      (
        await getConversationRepository().createSession({
          orgId,
          userId,
          initiator: userId,
          title: 'seen',
          lastActivityAt: Date.now(),
          status: 'Complete',
          sessionType: 'chat',
        })
      )._id,
    updatedAt: (_orgId, sessionId) => updatedAt(sessionId),
  }));

  it('SEEN-M1: a session read never carries anyone\'s marks', async () => {
    const orgId = new Types.ObjectId().toHexString();
    const userId = new Types.ObjectId().toHexString();
    const { _id: id } = await getConversationRepository().createSession({
      orgId,
      userId,
      initiator: userId,
      title: 'seen',
      lastActivityAt: Date.now(),
      status: 'Complete',
      sessionType: 'chat',
    });
    await conversationSeenMarks().mark(orgId, id, userId, 5_000);
    const row = (await ChatSession.findById(id).lean()) as Record<string, unknown> | null;
    expect(row).to.not.equal(null);
    expect(row).to.not.have.property('harnessSeen');
  });
});
