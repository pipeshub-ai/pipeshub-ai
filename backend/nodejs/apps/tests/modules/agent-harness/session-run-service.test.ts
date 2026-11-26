import { expect } from 'chai';
import sinon from 'sinon';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';

const scope = {
  orgId: 'org-1',
  actorId: 'user-1',
  sessionId: 'session-1',
  agentKey: 'research',
};

const run = {
  runId: 'run-1',
  orgId: 'org-1',
  sessionId: 'session-1',
  actorId: 'user-1',
  userMessageKey: 'message-1',
  idempotencyKey: 'submit-1',
  payloadHash: 'sha256:one',
  profile: 'dispatched' as const,
  status: 'paused' as const,
  outcome: null,
  revision: 4,
  fence: 1,
  attemptId: null,
  workerId: null,
  leaseExpiresAt: null,
  absoluteDeadlineAt: 10_000,
  checkpoint: null,
  createdAt: 1_000,
  updatedAt: 2_000,
};

/** The session as a participant may reach it: null for a stranger, a legacy or an unpinned session. */
const sessions = (access: unknown = { audience: 'owner' }) => ({
  findStreamAccess: sinon.stub().resolves(access),
});

describe('SessionRunService', () => {
  it('authorizes the complete tenant, owner, session and agent scope before returning a run', async () => {
    const repository = sessions();
    const executions = { get: sinon.stub().resolves(run) };
    const service = new SessionRunService(
      executions as never,
      repository as never,
    );

    expect(await service.get(scope, 'run-1')).to.equal(run);
    expect(repository.findStreamAccess.calledOnceWithExactly(scope)).to.equal(
      true,
    );
    expect(executions.get.calledOnceWithExactly('org-1', 'run-1')).to.equal(
      true,
    );
  });

  it('does not reveal runs from another session or actor', async () => {
    const executions = {
      get: sinon.stub().resolves({ ...run, actorId: 'user-2' }),
    };
    const service = new SessionRunService(
      executions as never,
      sessions() as never,
    );

    try {
      await service.get(scope, 'run-1');
      expect.fail('Expected not found');
    } catch (error) {
      expect(error).to.be.instanceOf(HarnessNotFoundError);
    }
  });

  it('fails closed for an unpinned or legacy session before reading run state', async () => {
    const executions = { get: sinon.stub() };
    const service = new SessionRunService(
      executions as never,
      sessions(null) as never,
    );

    try {
      await service.get(scope, 'run-1');
      expect.fail('Expected not found');
    } catch (error) {
      expect(error).to.be.instanceOf(HarnessNotFoundError);
    }
    expect(executions.get.called).to.equal(false);
  });

  it('uses a deterministic opaque command identity for idempotent resume', async () => {
    const control = sinon.stub().resolves({ commandId: 'receipt', run });
    const executions = { get: sinon.stub().resolves(run), control };
    const service = new SessionRunService(
      executions as never,
      sessions() as never,
    );
    const command = {
      ...scope,
      runId: 'run-1',
      expectedRevision: 4,
      idempotencyKey: 'resume-click-1',
      action: 'resume' as const,
    };

    await service.control(command);
    await service.control(command);

    const first = control.firstCall.args[0];
    const second = control.secondCall.args[0];
    expect(first).to.deep.equal(second);
    expect(first).to.include({
      orgId: 'org-1',
      runId: 'run-1',
      expectedRevision: 4,
      action: 'resume',
    });
    expect(first.commandId).to.match(/^user-control:[a-f0-9]{64}:commit$/);
    expect(first.dispatchCommandId).to.match(
      /^user-control:[a-f0-9]{64}:dispatch$/,
    );
  });

  it('AP-3: a collaborator controls their own run, never someone else\'s', async () => {
    const bob = { ...scope, actorId: 'user-2' };
    const bobsRun = { ...run, actorId: 'user-2', sessionRole: 'collaborator' };
    const control = sinon.stub().resolves({ commandId: 'receipt', run: bobsRun });
    const service = new SessionRunService(
      { get: sinon.stub().resolves(bobsRun), control } as never,
      sessions({ audience: 'shared' }) as never,
    );
    await service.control({
      ...bob,
      runId: 'run-1',
      expectedRevision: 4,
      idempotencyKey: 'cancel-1',
      action: 'cancel',
    });
    expect(control.calledOnce).to.equal(true);
    expect(control.firstCall.args[0].by).to.equal('p_user-2');

    const alice = new SessionRunService(
      { get: sinon.stub().resolves(bobsRun), control: sinon.stub() } as never,
      sessions({ audience: 'owner' }) as never,
    );
    try {
      await alice.get(scope, 'run-1');
      expect.fail('Expected not found');
    } catch (error) {
      expect(error).to.be.instanceOf(HarnessNotFoundError);
    }
  });

  it('AU-8: a run\'s actor watches it as owner; every other participant as shared', async () => {
    const v2 = { ...run, harnessMajor: 2 as const, actorId: 'user-2' };
    const service = (audience: string) =>
      new SessionRunService(
        { get: sinon.stub().resolves(v2) } as never,
        sessions({ audience, title: 'Plan' }) as never,
      );
    const bob = await service('shared').streamAccess(
      { ...scope, actorId: 'user-2' },
      'run-1',
    );
    expect(bob.access).to.deep.equal({ audience: 'owner', title: 'Plan' });
    const alice = await service('owner').streamAccess(scope, 'run-1');
    expect(alice.access).to.deep.equal({ audience: 'shared', title: 'Plan' });
  });

  describe('WP88-FU-4: the stream of the turn just admitted', () => {
    const v2 = { ...run, harnessMajor: 2 as const };

    it('opens a conversation the admission created on what it wrote, reading neither the session nor the run', async () => {
      const get = sinon.stub().rejects(new Error('not read'));
      const repository = sessions();
      const service = new SessionRunService({ get } as never, repository as never);
      const opened = await service.admittedStreamAccess(scope, {
        run: v2 as never,
        created: true,
        createdSession: { title: 'Plan the launch' },
      });
      expect(opened.run).to.equal(v2);
      expect(opened.access).to.deep.equal({ audience: 'owner', title: 'Plan the launch' });
      expect(get.called).to.equal(false);
      expect(repository.findStreamAccess.called).to.equal(false);
    });

    it('reads a follow-up turn, a replay and a run of another session as streamAccess does', async () => {
      for (const admitted of [
        { run: v2, created: true },
        { run: v2, created: false, createdSession: { title: 'T' } },
        { run: { ...v2, sessionId: 'session-2' }, created: true, createdSession: { title: 'T' } },
        { run: { ...v2, actorId: 'user-2' }, created: true, createdSession: { title: 'T' } },
      ]) {
        const get = sinon.stub().resolves(v2);
        const repository = sessions({ audience: 'owner', title: 'Stored' });
        const service = new SessionRunService({ get } as never, repository as never);
        const opened = await service.admittedStreamAccess(scope, admitted as never);
        expect(repository.findStreamAccess.calledOnce).to.equal(true);
        expect(get.calledOnceWith('org-1', 'run-1')).to.equal(true);
        expect(opened.access.title).to.equal('Stored');
      }
    });
  });
});
