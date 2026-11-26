import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { HarnessConversationRetention } from '../../../../src/libs/repositories/agent-harness/conversation-retention/mongo';

const ORG = '507f1f77bcf86cd799439011';
const SESSION = '507f1f77bcf86cd799439012';

const stubFind = (rows: unknown[]) => {
  const chain = {
    sort: sinon.stub(),
    limit: sinon.stub(),
    read: sinon.stub(),
    lean: sinon.stub().resolves(rows),
  };
  chain.sort.returns(chain);
  chain.limit.returns(chain);
  chain.read.returns(chain);
  const find = sinon.stub(ChatSession, 'find').returns(chain as never);
  return { find, chain };
};

describe('HarnessConversationRetention', () => {
  afterEach(() => sinon.restore());

  it("lists the org's live sessions idle since the cutoff, oldest first, bounded", async () => {
    const { find, chain } = stubFind([{ _id: new Types.ObjectId(SESSION) }]);
    expect(
      await new HarnessConversationRetention().idleSince(ORG, 5_000, 25),
    ).to.deep.equal([SESSION]);
    const [filter, projection] = find.firstCall.args as unknown as [
      Record<string, unknown>,
      unknown,
    ];
    expect(String(filter.orgId)).to.equal(ORG);
    expect(filter).to.deep.include({
      isDeleted: false,
      lastActivityAt: { $lt: 5_000 },
    });
    expect(projection).to.deep.equal({ _id: 1 });
    expect(chain.sort.firstCall.args[0]).to.deep.equal({ lastActivityAt: 1 });
    expect(chain.limit.firstCall.args[0]).to.equal(25);
    expect(chain.read.firstCall.args[0]).to.equal('primary');
  });

  it('lists nothing for a malformed org id', async () => {
    const find = sinon.stub(ChatSession, 'find');
    expect(
      await new HarnessConversationRetention().idleSince('nope', 1, 1),
    ).to.deep.equal([]);
    expect(find.called).to.equal(false);
  });

  it('expires only a still-idle live session of the org, in the transaction', async () => {
    const update = sinon
      .stub(ChatSession, 'updateOne')
      .resolves({ modifiedCount: 1 } as never);
    const tx = { id: 'tx' };
    expect(
      await new HarnessConversationRetention().expire(ORG, SESSION, 9, tx as never),
    ).to.equal(true);
    const [filter, mutation, options] = update.firstCall.args as unknown as [
      Record<string, unknown>,
      unknown,
      unknown,
    ];
    expect(String(filter._id)).to.equal(SESSION);
    expect(String(filter.orgId)).to.equal(ORG);
    expect(filter).to.deep.include({
      isDeleted: false,
      lastActivityAt: { $lt: 9 },
    });
    expect(mutation).to.have.nested.property('$set.isDeleted', true);
    expect((mutation as { $set: { erasureRequestedAt: unknown } }).$set.erasureRequestedAt).to.be.a('number');
    expect(options).to.deep.equal({ session: tx });

    update.resolves({ modifiedCount: 0 } as never);
    expect(
      await new HarnessConversationRetention().expire(ORG, SESSION, 9, tx as never),
    ).to.equal(false);
  });

  it('BF-2: lists only deletions that asked for erasure, on the partial index, after the cursor, bounded', async () => {
    const { find, chain } = stubFind([
      { _id: new Types.ObjectId(SESSION), orgId: new Types.ObjectId(ORG), deletedBy: new Types.ObjectId(ORG), erasureRequestedAt: 7 },
      { _id: new Types.ObjectId(ORG), orgId: new Types.ObjectId(ORG), erasureRequestedAt: 8 },
    ]);
    expect(
      await new HarnessConversationRetention().awaitingErasure({ requestedAt: 5, sessionId: SESSION }, 10),
    ).to.deep.equal([
      { orgId: ORG, sessionId: SESSION, requestedAt: 7, by: `p_${ORG}` },
      { orgId: ORG, sessionId: ORG, requestedAt: 8, by: null },
    ]);
    const [filter] = find.firstCall.args as unknown as [Record<string, any>];
    expect(filter.isDeleted).to.equal(true);
    expect(filter.erasureRequestedAt).to.deep.equal({ $exists: true });
    expect(filter.$or[0]).to.deep.equal({ erasureRequestedAt: { $gt: 5 } });
    expect(String(filter.$or[1]._id.$gt)).to.equal(SESSION);
    expect(chain.sort.firstCall.args[0]).to.deep.equal({ erasureRequestedAt: 1, _id: 1 });
    expect(chain.limit.firstCall.args[0]).to.equal(10);
  });

  it('BF-1: never lists a deletion from before erasure existed (no marker) and starts at the oldest without a cursor', async () => {
    const { find } = stubFind([]);
    await new HarnessConversationRetention().awaitingErasure(null, 10);
    expect((find.firstCall.args as unknown[])[0]).to.deep.equal({
      erasureRequestedAt: { $exists: true },
      isDeleted: true,
    });
  });
});
