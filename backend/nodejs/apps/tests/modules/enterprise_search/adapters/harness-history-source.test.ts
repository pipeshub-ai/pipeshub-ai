import { expect } from 'chai';
import sinon from 'sinon';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { HarnessHistorySource } from '../../../../src/libs/repositories/agent-harness/history-source/mongo';

const REF = {
  orgId: '507f1f77bcf86cd799439011',
  sessionId: '507f1f77bcf86cd799439012',
};

const stubLedgerRow = (row: unknown) => {
  const session = sinon
    .stub()
    .returns({ lean: sinon.stub().resolves(row) });
  const select = sinon.stub().returns({ session });
  const findOne = sinon.stub(ChatSession, 'findOne').returns({ select } as never);
  return { findOne, select, session };
};

const stubMessages = (messages: unknown[]) => {
  const chain = {
    sort: sinon.stub(),
    select: sinon.stub(),
    session: sinon.stub(),
    lean: sinon.stub(),
  };
  chain.sort.returns(chain);
  chain.select.returns(chain);
  chain.session.returns(chain);
  chain.lean.returns({ exec: sinon.stub().resolves(messages) });
  sinon.stub(ChatSessionMessage, 'find').returns(chain as never);
  return chain;
};

describe('HarnessHistorySource', () => {
  afterEach(() => sinon.restore());

  it('reads messages as the history a model is sent, with the stored ledger stripped to its fields', async () => {
    const tx = { id: 'tx' };
    const row = stubLedgerRow({
      nextSeq: 2,
      harnessLedger: { seq: 2, digest: 'sha256:x', diverged: false, _id: 'junk' },
    });
    const messages = stubMessages([
      { seq: 1, messageType: 'user_query', content: 'hi', createdAt: new Date(0) },
      { seq: 2, messageType: 'error', content: 'boom' },
    ]);

    const history = await new HarnessHistorySource().read(REF as never, tx as never);

    // #4492: the ledger row is read in the caller's org only.
    expect(row.findOne.firstCall.args[0]).to.deep.equal({ _id: REF.sessionId, orgId: REF.orgId });
    expect(row.select.firstCall.args[0]).to.equal('+nextSeq +harnessLedger');
    expect(row.session.firstCall.args[0]).to.equal(tx);
    expect(messages.session.firstCall.args[0]).to.equal(tx);
    expect(messages.select.firstCall.args[0]).to.equal('+label');
    expect(history.lastSeq).to.equal(2);
    expect(history.ledger).to.deep.equal({ seq: 2, digest: 'sha256:x' });
    expect(history.messages).to.have.length(2);
    expect(history.messages[0]).to.deep.include({ seq: 1 });
    expect(history.messages[0]!.turns).to.deep.equal([
      { content: 'hi', role: 'user_query' },
    ]);
    // An error message is never sent to the model, and nothing reached it.
    expect(history.messages[1]).to.deep.equal({ seq: 2, turns: [], provenance: null });
    // UX2: a user's turn is its own words.
    expect(history.messages[0]!.provenance).to.deep.equal({ complete: true, sources: [] });
  });

  it('keeps the diverged mark and reads an unledgered session as such', async () => {
    stubLedgerRow({ harnessLedger: { seq: 3, digest: 'd', diverged: true } });
    stubMessages([]);
    const diverged = await new HarnessHistorySource().read(REF as never, {} as never);
    expect(diverged.ledger).to.deep.equal({ seq: 3, digest: 'd', diverged: true });
    expect(diverged.lastSeq).to.equal(0);

    sinon.restore();
    stubLedgerRow(null);
    stubMessages([]);
    const none = await new HarnessHistorySource().read(REF as never, {} as never);
    expect(none).to.deep.equal({ messages: [], lastSeq: 0, ledger: null });
  });

  it('PERF-E3: with the counter and ledger its transaction holds, reads the messages only', async () => {
    const findOne = sinon.stub(ChatSession, 'findOne');
    stubMessages([{ seq: 1, messageType: 'user_query', content: 'hi' }]);
    const accounted = { lastSeq: 1, ledger: { seq: 1, digest: 'd1' } };
    const history = await new HarnessHistorySource().read(REF as never, {} as never, accounted);
    expect(findOne.called).to.equal(false);
    expect(history).to.deep.include(accounted);
    expect(history.messages).to.have.length(1);
  });
});
