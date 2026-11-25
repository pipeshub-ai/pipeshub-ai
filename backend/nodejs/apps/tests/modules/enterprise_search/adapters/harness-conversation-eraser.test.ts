import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema';
import { Conversation } from '../../../../src/modules/enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../../src/modules/enterprise_search/schema/agent.conversation.schema';
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema';
import { HarnessConversationEraser } from '../../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { FakeChatAttachments } from '../../agent-harness/support/chat-attachments';

const ORG = '507f1f77bcf86cd799439011';
const SESSION = '507f1f77bcf86cd799439012';
const MAJORITY = { writeConcern: { w: 'majority' } };

const citationId = (n: number) =>
  new Types.ObjectId(n.toString(16).padStart(24, '0'));

describe('HarnessConversationEraser', () => {
  afterEach(() => sinon.restore());

  const stubWorld = (messages: unknown[]) => {
    const read = sinon.stub().returns({ lean: sinon.stub().resolves(messages) });
    const find = sinon.stub(ChatSessionMessage, 'find').returns({ read } as never);
    const citations = sinon
      .stub(Citation, 'deleteMany')
      .callsFake(((filter: { _id: { $in: unknown[] } }) =>
        Promise.resolve({ deletedCount: filter._id.$in.length })) as never);
    const removeMessages = sinon
      .stub(ChatSessionMessage, 'deleteMany')
      .resolves({ deletedCount: messages.length } as never);
    const removeSession = sinon
      .stub(ChatSession, 'deleteOne')
      .resolves({ deletedCount: 1 } as never);
    const legacy = sinon.stub(Conversation, 'deleteOne').resolves({} as never);
    const agentLegacy = sinon
      .stub(AgentConversation, 'deleteOne')
      .resolves({} as never);
    const notices = sinon.stub(Notifications, 'deleteMany').resolves({ deletedCount: 1 } as never);
    sinon.stub(ChatSession, 'findOne').returns({
      read: sinon.stub().returns({ lean: sinon.stub().resolves({ userId: new Types.ObjectId() }) }),
    } as never);
    return { find, read, citations, removeMessages, removeSession, legacy, agentLegacy, notices };
  };

  it('erases nothing for malformed ids', async () => {
    const find = sinon.stub(ChatSessionMessage, 'find');
    const eraser = new HarnessConversationEraser(new FakeChatAttachments());
    const nothing = { citations: 0, messages: 0, sessions: 0, attachments: 0, attachmentBlobs: 0 };
    expect(await eraser.erase(ORG, 'bad')).to.deep.equal(nothing);
    expect(await eraser.erase('bad', SESSION)).to.deep.equal(nothing);
    expect(find.called).to.equal(false);
  });

  it("deletes the tenant's cited citations once each, then messages, the session and both legacy copies", async () => {
    const shared = citationId(1);
    const world = stubWorld([
      { citations: [{ citationId: shared }, { citationId: citationId(2) }] },
      { citations: [{ citationId: shared }, {}] },
      {},
    ]);

    const counts = await new HarnessConversationEraser(new FakeChatAttachments()).erase(ORG, SESSION);

    expect(counts).to.deep.equal({ citations: 2, messages: 3, sessions: 1, attachments: 0, attachmentBlobs: 0 });
    expect(world.read.firstCall.args).to.deep.equal(['primary']);
    const [messageFilter] = world.find.firstCall.args as unknown as [
      Record<string, Types.ObjectId>,
    ];
    expect(String(messageFilter.sessionId)).to.equal(SESSION);
    expect(String(messageFilter.orgId)).to.equal(ORG);

    const [citationFilter, citationOptions] = world.citations.firstCall
      .args as unknown as [
      { _id: { $in: Types.ObjectId[] }; 'metadata.orgId': string },
      unknown,
    ];
    expect(citationFilter['metadata.orgId']).to.equal(ORG);
    expect(citationFilter._id.$in.map(String)).to.deep.equal([
      String(shared),
      String(citationId(2)),
    ]);
    expect(citationOptions).to.deep.equal(MAJORITY);

    expect(world.citations.calledBefore(world.removeMessages)).to.equal(true);
    expect(world.removeMessages.calledBefore(world.removeSession)).to.equal(true);
    for (const stub of [world.removeSession, world.legacy, world.agentLegacy]) {
      const [filter, options] = stub.firstCall.args as unknown as [
        Record<string, Types.ObjectId>,
        unknown,
      ];
      expect(String(filter._id)).to.equal(SESSION);
      expect(String(filter.orgId)).to.equal(ORG);
      expect(options).to.deep.equal(MAJORITY);
    }
    // CLEANUP-3: a scheduled run's inbox notice links to the conversation by id.
    const [noticeFilter, noticeOptions] = world.notices.firstCall.args as unknown as [
      { orgId: Types.ObjectId; 'payload.conversationId': string },
      unknown,
    ];
    expect(String(noticeFilter.orgId)).to.equal(ORG);
    expect(noticeFilter['payload.conversationId']).to.equal(SESSION);
    expect(noticeOptions).to.deep.equal(MAJORITY);
  });

  it('deletes citations in batches of a thousand', async () => {
    const many = Array.from({ length: 2_001 }, (_, i) => ({
      citationId: citationId(i + 1),
    }));
    const world = stubWorld([{ citations: many }]);

    const counts = await new HarnessConversationEraser(new FakeChatAttachments()).erase(ORG, SESSION);

    expect(world.citations.callCount).to.equal(3);
    const sizes = world.citations
      .getCalls()
      .map((call) => (call.args[0] as { _id: { $in: unknown[] } })._id.$in.length);
    expect(sizes).to.deep.equal([1_000, 1_000, 1]);
    expect(counts.citations).to.equal(2_001);
  });

  it('issues no citation delete when the messages cite nothing', async () => {
    const world = stubWorld([{ citations: [] }]);
    const counts = await new HarnessConversationEraser(new FakeChatAttachments()).erase(ORG, SESSION);
    expect(world.citations.called).to.equal(false);
    expect(counts.citations).to.equal(0);
  });
});
