import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  ATTACHMENT_CONTEXT_LIMIT,
  ChatContentLoader,
} from '../../../src/modules/authz/loaders/chat-content.loader';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';

const oid = () => new mongoose.Types.ObjectId();

const findChain = (rows: unknown[]) => {
  const chain = {
    sort: sinon.stub().returnsThis(),
    limit: sinon.stub().returnsThis(),
    select: sinon.stub().returnsThis(),
    lean: sinon.stub().resolves(rows),
  };
  return chain;
};

const findOneChain = (row: unknown) => ({
  select: sinon.stub().returnsThis(),
  lean: sinon.stub().resolves(row),
});

describe('ChatContentLoader', () => {
  afterEach(() => sinon.restore());

  describe('loadAttachmentContext', () => {
    it('queries the org-scoped user turns that attached the record, newest first and capped', async () => {
      const chain = findChain([]);
      const find = sinon.stub(ChatSessionMessage, 'find').returns(chain as never) as sinon.SinonStub<unknown[]>;
      const org = oid().toString();
      await new ChatContentLoader().loadAttachmentContext(org, 'rec-1');
      expect(find.firstCall.args[0]).to.deep.equal({
        orgId: org,
        messageType: 'user_query',
        'attachments.recordId': { $eq: 'rec-1', $type: 'string' },
      });
      expect(chain.sort.firstCall.args[0]).to.deep.equal({ _id: -1 });
      expect(chain.limit.firstCall.args[0]).to.equal(ATTACHMENT_CONTEXT_LIMIT);
      expect(ATTACHMENT_CONTEXT_LIMIT).to.equal(20);
    });

    it('restricts to one chat when conversationId is given, so another chat cannot vouch for the record', async () => {
      const find = sinon.stub(ChatSessionMessage, 'find').returns(findChain([]) as never) as sinon.SinonStub<unknown[]>;
      const org = oid().toString();
      const chat = oid().toString();
      await new ChatContentLoader().loadAttachmentContext(org, 'rec-1', chat);
      expect(find.firstCall.args[0]).to.deep.include({ orgId: org, sessionId: chat });
    });

    it('maps rows to string ids and omits absent legacy fields', async () => {
      const [s1, s2, author] = [oid(), oid(), oid()];
      sinon.stub(ChatSessionMessage, 'find').returns(
        findChain([
          { sessionId: s1, authorUserId: author, filesShared: false },
          { sessionId: s2 },
        ]) as never,
      );
      const rows = await new ChatContentLoader().loadAttachmentContext(oid().toString(), 'r');
      expect(rows).to.deep.equal([
        { sessionId: s1.toString(), authorUserId: author.toString(), filesShared: false },
        { sessionId: s2.toString() },
      ]);
    });

    it('does not query for a malformed org or conversation id', async () => {
      const find = sinon.stub(ChatSessionMessage, 'find');
      const loader = new ChatContentLoader();
      expect(await loader.loadAttachmentContext('bad', 'r')).to.deep.equal([]);
      expect(await loader.loadAttachmentContext(oid().toString(), 'r', 'bad')).to.deep.equal([]);
      expect(find.called).to.equal(false);
    });
  });

  describe('loadArtifactContext', () => {
    it("reads the user turn of that run in that chat, scoped to the org", async () => {
      const author = oid();
      const findOne = sinon
        .stub(ChatSessionMessage, 'findOne')
        .returns(findOneChain({ authorUserId: author, shareToolResults: true }) as never);
      const org = oid().toString();
      const chat = oid().toString();
      const turn = await new ChatContentLoader().loadArtifactContext(org, chat, 'run-1');
      expect(findOne.firstCall.args[0]).to.deep.equal({
        orgId: org,
        sessionId: chat,
        messageType: 'user_query',
        runId: { $eq: 'run-1', $type: 'string' },
      });
      expect(turn).to.deep.equal({ authorUserId: author.toString(), shareToolResults: true });
    });

    it('is null without a runId (a legacy artifact) and never queries', async () => {
      const findOne = sinon.stub(ChatSessionMessage, 'findOne');
      const loader = new ChatContentLoader();
      expect(await loader.loadArtifactContext(oid().toString(), oid().toString(), undefined)).to.equal(null);
      expect(await loader.loadArtifactContext(oid().toString(), oid().toString(), '')).to.equal(null);
      expect(findOne.called).to.equal(false);
    });

    it('is null when no such turn exists or an id is malformed', async () => {
      const findOne = sinon.stub(ChatSessionMessage, 'findOne').returns(findOneChain(null) as never);
      const loader = new ChatContentLoader();
      expect(await loader.loadArtifactContext(oid().toString(), oid().toString(), 'run-1')).to.equal(null);
      findOne.resetHistory();
      expect(await loader.loadArtifactContext('bad', oid().toString(), 'run-1')).to.equal(null);
      expect(findOne.called).to.equal(false);
    });
  });
});
