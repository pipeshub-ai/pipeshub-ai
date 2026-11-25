import { expect } from 'chai';
import sinon from 'sinon';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { HarnessChatSessionRepository } from '../../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';

describe('HarnessChatSessionRepository', () => {
  afterEach(() => sinon.restore());

  it('loads a pinned execution binding with the complete authority predicate', async () => {
    const scope = {
      orgId: '507f1f77bcf86cd799439011',
      actorId: '507f191e810c19729de860ea',
      sessionId: '507f1f77bcf86cd799439012',
      agentKey: 'research-agent',
    };
    const lean = sinon.stub().resolves({ harnessVersion: 'durable-v1' });
    const select = sinon.stub().returns({ lean });
    const find = sinon
      .stub(ChatSession, 'findOne')
      .returns({ select } as never);

    expect(
      await new HarnessChatSessionRepository().findExecutionBinding(scope),
    ).to.deep.equal({ ...scope, harnessVersion: 'durable-v1' });
    expect(
      find.calledOnceWithExactly({
        _id: scope.sessionId,
        orgId: scope.orgId,
        userId: scope.actorId,
        agentKey: scope.agentKey,
        sessionType: 'agent',
        isDeleted: false,
      }),
    ).to.equal(true);
    expect(select.calledWith('+sessionType +harnessVersion')).to.equal(true);
  });

  it('binds and pins the version with tenant, owner, type, agent and deletion checks in one predicate', async () => {
    const lean = sinon.stub().resolves({
      _id: 'ignored',
      userId: '507f191e810c19729de860ea',
      isShared: true,
      sharedWith: [{ userId: 'u2', accessLevel: 'read' }],
      nextSeq: 4,
      harnessLedger: { seq: 4, digest: 'd4', _id: 'junk' },
    });
    const select = sinon.stub().returns({ lean });
    const update = sinon
      .stub(ChatSession, 'findOneAndUpdate')
      .returns({ select } as never);
    const scope = {
      orgId: '507f1f77bcf86cd799439011',
      actorId: '507f191e810c19729de860ea',
      sessionId: '507f1f77bcf86cd799439012',
      agentKey: 'research-agent',
    };

    const tx = { id: 'tx' };
    expect(
      await new HarnessChatSessionRepository().bindDurableSession(
        scope,
        1_000,
        tx as never,
      ),
    ).to.deep.equal({
      ownerId: scope.actorId,
      shared: true,
      // PERF-E3: what the turn's write needs, so it does not read the session again.
      accounted: { lastSeq: 4, ledger: { seq: 4, digest: 'd4' } },
    });

    const [filter, mutation, options] = update.firstCall.args;
    expect(filter).to.include({
      _id: scope.sessionId,
      orgId: scope.orgId,
      userId: scope.actorId,
      agentKey: scope.agentKey,
      sessionType: 'agent',
      isDeleted: false,
    });
    expect(filter!.$or).to.deep.equal([
      { harnessVersion: { $exists: false } },
      { harnessVersion: 'durable-v1' },
    ]);
    expect(mutation).to.deep.equal({
      $set: {
        harnessVersion: 'durable-v1',
        status: 'Inprogress',
        lastActivityAt: 1_000,
      },
    });
    expect(options).to.deep.equal({ returnDocument: 'after', session: tx });
    expect(
      select.calledWith('+sessionType +harnessVersion +nextSeq +harnessLedger userId isShared sharedWith'),
    ).to.equal(true);
  });

  it('CA-5: binds a collaborator only through a write share of a shared session, in the transaction', async () => {
    const lean = sinon.stub().resolves({
      userId: 'owner-1',
      isShared: true,
      sharedWith: [{ userId: '507f191e810c19729de860eb', accessLevel: 'write' }],
    });
    const update = sinon
      .stub(ChatSession, 'findOneAndUpdate')
      .returns({ select: sinon.stub().returns({ lean }) } as never);
    const scope = {
      orgId: '507f1f77bcf86cd799439011',
      actorId: '507f191e810c19729de860eb',
      sessionId: '507f1f77bcf86cd799439012',
    };
    const tx = { id: 'tx' };
    expect(
      await new HarnessChatSessionRepository().bindDurableSession(
        scope,
        1_000,
        tx as never,
        'collaborator',
      ),
    ).to.deep.equal({ ownerId: 'owner-1', shared: true, accounted: { lastSeq: 0, ledger: null } });
    const [filter, , options] = update.firstCall.args;
    expect(filter).to.not.have.property('userId');
    expect(filter).to.include({ isShared: true, isDeleted: false });
    expect(filter!.sharedWith).to.deep.equal({
      $elemMatch: { userId: scope.actorId, accessLevel: 'write' },
    });
    expect(options).to.deep.equal({ returnDocument: 'after', session: tx });
  });

  it('fails closed before querying for malformed authority identifiers', async () => {
    const update = sinon.stub(ChatSession, 'findOneAndUpdate');
    const result = await new HarnessChatSessionRepository().bindDurableSession(
      {
        orgId: 'org-from-request-body',
        actorId: 'user-from-request-body',
        sessionId: 'invalid',
        agentKey: 'agent',
      },
      1_000,
      {} as never,
    );
    expect(result).to.equal(null);
    expect(update.called).to.equal(false);
  });

  it('binds built-in chat sessions by owner and chat type', async () => {
    const scope = {
      orgId: '507f1f77bcf86cd799439011',
      actorId: '507f191e810c19729de860ea',
      sessionId: '507f1f77bcf86cd799439012',
    };
    const lean = sinon.stub().resolves({ harnessVersion: 'durable-v1' });
    const select = sinon.stub().returns({ lean });
    const find = sinon
      .stub(ChatSession, 'findOne')
      .returns({ select } as never);

    expect(
      await new HarnessChatSessionRepository().findExecutionBinding(scope),
    ).to.deep.equal({ ...scope, harnessVersion: 'durable-v1' });
    expect(find.firstCall.args[0]).to.deep.equal({
      _id: scope.sessionId,
      orgId: scope.orgId,
      userId: scope.actorId,
      sessionType: 'chat',
      isDeleted: false,
    });
  });

  it('does not distinguish unauthorized, deleted, and version-conflicting sessions', async () => {
    const lean = sinon.stub().resolves(null);
    sinon.stub(ChatSession, 'findOneAndUpdate').returns({
      select: sinon.stub().returns({ lean }),
    } as never);
    const result = await new HarnessChatSessionRepository().bindDurableSession(
      {
        orgId: '507f1f77bcf86cd799439011',
        actorId: '507f191e810c19729de860ea',
        sessionId: '507f1f77bcf86cd799439012',
        agentKey: 'agent',
      },
      1_000,
      {} as never,
    );
    expect(result).to.equal(null);
  });

  it('creates an agent session pinned to durable-v1 inside the caller transaction', async () => {
    const create = sinon.stub(ChatSession, 'create').resolves([] as never);
    const tx = { id: 'tx' };
    await new HarnessChatSessionRepository().createDurableSession(
      {
        orgId: '507f1f77bcf86cd799439011',
        actorId: '507f191e810c19729de860ea',
        sessionId: '507f1f77bcf86cd799439012',
        agentKey: 'research-agent',
        title: 'hello',
        model: { chatMode: 'auto' },
        now: 5,
      },
      tx as never,
    );
    const [documents, options] = create.firstCall.args as unknown as [
      Array<Record<string, unknown>>,
      Record<string, unknown>,
    ];
    expect(documents[0]).to.include({
      _id: '507f1f77bcf86cd799439012',
      userId: '507f191e810c19729de860ea',
      sessionType: 'agent',
      agentKey: 'research-agent',
      conversationSource: 'agent_chat',
      harnessVersion: 'durable-v1',
      status: 'Inprogress',
    });
    expect(options).to.deep.equal({ session: tx, ordered: true });
  });

  describe('findStreamAccess', () => {
    const scope = {
      orgId: '507f1f77bcf86cd799439011',
      actorId: '507f191e810c19729de860ea',
      sessionId: '507f1f77bcf86cd799439012',
    };
    const stubFind = (row: unknown) => {
      const lean = sinon.stub().resolves(row);
      const select = sinon.stub().returns({ lean });
      return sinon.stub(ChatSession, 'findOne').returns({ select } as never);
    };

    it('admits the owner or a user the session is shared with, in the org, durable sessions only', async () => {
      const find = stubFind({ userId: scope.actorId, title: 'Plan' });
      expect(
        await new HarnessChatSessionRepository().findStreamAccess(scope),
      ).to.deep.equal({ audience: 'owner', title: 'Plan' });
      expect(find.firstCall.args[0]).to.deep.equal({
        _id: scope.sessionId,
        orgId: scope.orgId,
        sessionType: 'chat',
        isDeleted: false,
        harnessVersion: 'durable-v1',
        $or: [
          { userId: scope.actorId },
          { isShared: true, 'sharedWith.userId': scope.actorId },
        ],
      });
    });

    it('marks a non-owner reader as shared and scopes agent sessions to the agent', async () => {
      const find = stubFind({ userId: '507f191e810c19729de860ff' });
      expect(
        await new HarnessChatSessionRepository().findStreamAccess({
          ...scope,
          agentKey: 'research-agent',
        }),
      ).to.deep.equal({ audience: 'shared' });
      expect(find.firstCall.args[0]).to.include({
        agentKey: 'research-agent',
        sessionType: 'agent',
      });
    });

    it('returns null for an unknown session or malformed ids', async () => {
      const find = stubFind(null);
      const repository = new HarnessChatSessionRepository();
      expect(await repository.findStreamAccess(scope)).to.equal(null);
      expect(
        await repository.findStreamAccess({ ...scope, actorId: 'nope' }),
      ).to.equal(null);
      expect(find.calledOnce).to.equal(true);
    });
  });
});
