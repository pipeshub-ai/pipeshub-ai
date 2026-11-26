import { expect } from 'chai';
import { UNADVERTISED_WORKER } from '../../../src/modules/agent-harness/domain/worker-compat';
import sinon from 'sinon';
import type { NextFunction } from 'express';
import { AgentHarnessInternalController } from '../../../src/modules/agent-harness/transport/internal-controller';
import { advertisedNodeCapabilities } from '../../../src/modules/agent-harness/domain/node-capabilities';
import {
  HarnessConflictError,
  HarnessUnauthorizedError,
  JournalPayloadMissingError,
  JournalRejectedError,
} from '../../../src/modules/agent-harness/domain/errors';
import {
  HttpError,
  UnauthorizedError,
} from '../../../src/libs/errors/http.errors';

const response = () => {
  const res = {
    status: sinon.stub(),
    json: sinon.stub(),
  };
  res.status.returns(res);
  return res;
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
  status: 'running' as const,
  outcome: null,
  revision: 1,
  fence: 1,
  attemptId: 'attempt-server',
  workerId: 'worker-1',
  leaseExpiresAt: 2000,
  absoluteDeadlineAt: 10000,
  checkpoint: null,
  createdAt: 1000,
  updatedAt: 1000,
};

const attemptToken = { token: 'attempt-token', expiresAt: 99_000 };

const authorityStub = () => ({
  issueFor: sinon.stub().resolves(attemptToken),
  authorizeAttempt: sinon.stub().resolves(),
});

const attempt = (attemptId = 'attempt-server', fence = 1) => ({
  orgId: 'org-1',
  runId: 'run-1',
  attemptId,
  fence,
  workerId: 'worker-1',
});

/** A middleware's `next`, stubbed: `NextFunction`'s overloads leave sinon inferring only the last one. */
const next = (): NextFunction => sinon.stub<[unknown?], undefined>();

describe('AgentHarnessInternalController', () => {
  it('claims for the named org and worker, creates the attempt ID and returns an attempt token', async () => {
    const claimed = { run, journal: { lastSeq: { 'session-1': 3 } } };
    const executions = { claim: sinon.stub().resolves(claimed), treeSkippedConnects: sinon.stub().resolves([]) };
    const authority = authorityStub();
    const controller = new AgentHarnessInternalController(
      executions as never,
      authority as never,
      () => 'attempt-server',
    );
    const req = {
      params: { runId: 'run-1' },
      body: { leaseDurationMs: 5000, orgId: 'org-1', workerId: 'worker-1' },
    };
    const res = response();
    const next = sinon.stub();

    await controller.claim(req as never, res as never, next);

    expect(
      executions.claim.calledOnceWithExactly({
        orgId: 'org-1',
        runId: 'run-1',
        workerId: 'worker-1',
        attemptId: 'attempt-server',
        leaseDurationMs: 5000,
        // No advertisement: a worker older than the rule (RU-2), filtered from every v2 run. No region either: it
        // is taken to be in this Node's region (OWD-12), which also holds the runs from before regions.
        worker: {
          ...UNADVERTISED_WORKER,
          placement: { region: 'default', servesUnregioned: true },
        },
      }),
    ).to.equal(true);
    expect(authority.issueFor.calledOnceWithExactly(run, 5000)).to.equal(true);
    expect(res.status.calledWith(200)).to.equal(true);
    expect(res.json.calledWith({ ...claimed, attemptToken, nodeCapabilities: advertisedNodeCapabilities(), priorityClass: 'interactive', skippedConnects: [], treeDepth: 0 })).to.equal(true);
    expect(next.called).to.equal(false);
  });

  it('T2: a claimed run reaches the worker without Node\'s bookkeeping (steering, claimedFrom: its run model is strict)', async () => {
    const steered = { ...run, inputSeq: 2, pendingInputs: [2], claimedFrom: 'expired_lease' };
    const executions = {
      claim: sinon.stub().resolves({ run: steered, journal: { lastSeq: { 'session-1': 3 } } }),
      treeSkippedConnects: sinon.stub().resolves([]),
    };
    const controller = new AgentHarnessInternalController(
      executions as never,
      authorityStub() as never,
      () => 'attempt-server',
    );
    const res = response();
    await controller.claim(
      {
        params: { runId: 'run-1' },
        body: { leaseDurationMs: 5000, orgId: 'org-1', workerId: 'worker-1' },
      } as never,
      res as never,
      next(),
    );
    const body = res.json.firstCall.args[0] as { run: Record<string, unknown> };
    expect(body.run).to.deep.equal(run);
  });

  it('derives commit authority from the verified token', async () => {
    const receipt = {
      commandId: 'commit-1',
      run,
      firstEventSeq: 1,
      lastEventSeq: 1,
    };
    const executions = {
      commit: sinon.stub().resolves(receipt),
      receiptEvents: sinon.stub().resolves([]),
      priorCommit: sinon.stub().resolves(null),
    };
    const authority = authorityStub();
    const controller = new AgentHarnessInternalController(
      executions as never,
      authority as never,
    );
    const body = {
      commandId: 'commit-1',
      attemptId: 'attempt-server',
      fence: 1,
      expectedRevision: 1,
      status: 'completed',
      outcome: 'answer',
      events: [{ type: 'run_completed', data: {} }],
      outbox: [],
      orgId: 'attacker-org',
      workerId: 'attacker-worker',
      runId: 'attacker-run',
    };
    const req = {
      params: { runId: 'run-1' },
      body,
      harnessAttempt: attempt(),
    };
    const res = response();

    await controller.commit(req as never, res as never, next());

    const command = executions.commit.firstCall.args[0];
    expect(command).to.include({
      orgId: 'org-1',
      workerId: 'worker-1',
      runId: 'run-1',
    });
    expect(
      res.json.calledWith({ receipt, events: [], replayed: false }),
    ).to.equal(true);
    expect(authority.authorizeAttempt.firstCall.args[0]).to.deep.equal(
      attempt(),
    );
  });

  it('accepts a batched commit and passes its journal events, transition and CAS through', async () => {
    const executions = {
      commit: sinon.stub().resolves({}),
      receiptEvents: sinon.stub().resolves([]),
      priorCommit: sinon.stub().resolves(null),
    };
    const controller = new AgentHarnessInternalController(
      executions as never,
      authorityStub() as never,
    );
    const body = {
      commandId: 'attempt-server:turn:1',
      attemptId: 'attempt-server',
      fence: 1,
      events: [
        {
          type: 'workspace.committed',
          schemaVersion: 1,
          critical: true,
          visibility: 'owner',
          data: { workspaceId: 'ws-1', revision: 2, changed: ['/a'] },
        },
      ],
      transition: { expectedRevision: 1, status: 'running' },
      workspaceHeadCAS: {
        workspaceId: 'ws-1',
        expectedRevision: 1,
        revision: 2,
      },
      orgId: 'attacker-org',
    };
    const next = sinon.stub();
    await controller.commit(
      {
        params: { runId: 'run-1' },
        body,
        harnessAttempt: attempt(),
      } as never,
      response() as never,
      next,
    );
    expect(next.called).to.equal(false);
    const command = executions.commit.firstCall.args[0];
    expect(command).to.deep.include({
      orgId: 'org-1',
      runId: 'run-1',
      workerId: 'worker-1',
      legacyEvents: [],
      outbox: [],
      transition: { expectedRevision: 1, status: 'running' },
      workspaceHeadCAS: {
        workspaceId: 'ws-1',
        expectedRevision: 1,
        revision: 2,
      },
    });
    expect(command.events).to.have.length(1);
  });

  it('maps a rejected journal event to 422 and a missing payload to a terminal 409', async () => {
    const cases: Array<[Error, string, number]> = [
      [
        new JournalRejectedError('WRITER_NOT_ALLOWED', 0, 'nope'),
        'HTTP_AGENT_HARNESS_JOURNAL_WRITER_NOT_ALLOWED',
        422,
      ],
      [
        new JournalPayloadMissingError('ref', 'missing'),
        'HTTP_AGENT_HARNESS_JOURNAL_PAYLOAD_MISSING',
        409,
      ],
    ];
    for (const [failure, code, status] of cases) {
      const controller = new AgentHarnessInternalController(
        { readRunJournal: sinon.stub().rejects(failure) } as never,
        authorityStub() as never,
      );
      const next = sinon.stub();
      await controller.readJournal(
        {
          params: { runId: 'run-1' },
          query: { attemptId: 'a-1', fence: '2', after: '5' },
          harnessAttempt: attempt('a-1', 2),
        } as never,
        response() as never,
        next,
      );
      const error = next.firstCall.args[0] as HttpError;
      expect(error.code).to.equal(code);
      expect(error.statusCode).to.equal(status);
    }
  });

  it('reads the journal only for the token worker and the named attempt', async () => {
    const events = [{ seq: 6 }];
    const executions = { readRunJournal: sinon.stub().resolves(events) };
    const controller = new AgentHarnessInternalController(
      executions as never,
      authorityStub() as never,
    );
    const res = response();
    await controller.readJournal(
      {
        params: { runId: 'run-1' },
        query: { attemptId: 'a-1', fence: '2', after: '5' },
        harnessAttempt: attempt('a-1', 2),
      } as never,
      res as never,
      sinon.stub() as never,
    );
    expect(executions.readRunJournal.firstCall.args[0]).to.deep.equal({
      orgId: 'org-1',
      runId: 'run-1',
      workerId: 'worker-1',
      attemptId: 'a-1',
      fence: 2,
      afterSeq: 5,
      limit: 200,
      scope: 'stream',
    });
    expect(res.json.calledWith({ events })).to.equal(true);
  });

  it('PERFD-13: the run the attempt check read is handed to the commit and to the journal read, so neither reads it again', async () => {
    const executions = {
      commit: sinon.stub().resolves({ commandId: 'commit-1', run }),
      receiptEvents: sinon.stub().resolves([]),
      readRunJournal: sinon.stub().resolves([]),
      priorCommit: sinon.stub().resolves(null),
    };
    const authority = authorityStub();
    const checked = { ...run, revision: 7 };
    authority.authorizeAttempt.resolves(checked);
    const controller = new AgentHarnessInternalController(executions as never, authority as never);

    await controller.commit(
      {
        params: { runId: 'run-1' },
        body: { commandId: 'commit-1', attemptId: 'attempt-server', fence: 1, events: [], outbox: [] },
        harnessAttempt: attempt(),
      } as never,
      response() as never,
      next(),
    );
    await controller.readJournal(
      {
        params: { runId: 'run-1' },
        query: { attemptId: 'attempt-server', fence: '1', after: '0', limit: '10' },
        harnessAttempt: attempt(),
      } as never,
      response() as never,
      next(),
    );

    expect(executions.commit.firstCall.args[1]).to.equal(checked);
    expect(executions.readRunJournal.firstCall.args[1]).to.equal(checked);
  });

  it('WP88-PF05-3: the replay check is sent with the attempt check, not after it, and its read reaches the commit', async () => {
    const order: string[] = [];
    let authorize!: (value: unknown) => void;
    const prior = Promise.resolve(null);
    const executions = {
      commit: sinon.stub().resolves({ commandId: 'commit-1', run }),
      receiptEvents: sinon.stub().resolves([]),
      priorCommit: sinon.stub().callsFake(() => {
        order.push('receipt');
        return prior;
      }),
    };
    const authority = authorityStub();
    authority.authorizeAttempt.callsFake(() => {
      order.push('attempt');
      return new Promise((resolve) => {
        authorize = resolve;
      });
    });
    const controller = new AgentHarnessInternalController(executions as never, authority as never);
    const committing = controller.commit(
      {
        params: { runId: 'run-1' },
        body: { commandId: 'commit-1', attemptId: 'attempt-server', fence: 1, events: [], outbox: [] },
        harnessAttempt: attempt(),
      } as never,
      response() as never,
      next(),
    );
    await Promise.resolve();
    expect(order, 'both reads are out before the attempt check answers').to.have.members(['receipt', 'attempt']);
    expect(executions.priorCommit.firstCall.args).to.deep.equal(['org-1', 'run-1', 'commit-1']);
    authorize({ ...run });
    await committing;
    expect(executions.commit.firstCall.args[2]).to.equal(prior);
  });

  it('WP88-PF05-3: an attempt check that refuses is the answer, whatever the replay check read', async () => {
    const executions = {
      commit: sinon.stub().resolves({}),
      receiptEvents: sinon.stub().resolves([]),
      priorCommit: sinon.stub().rejects(new Error('store down')),
    };
    const authority = authorityStub();
    const refusal = new Error('superseded');
    authority.authorizeAttempt.rejects(refusal);
    const controller = new AgentHarnessInternalController(executions as never, authority as never);
    const nextStub = sinon.stub();
    await controller.commit(
      {
        params: { runId: 'run-1' },
        body: { commandId: 'commit-1', attemptId: 'attempt-server', fence: 1, events: [], outbox: [] },
        harnessAttempt: attempt(),
      } as never,
      response() as never,
      nextStub,
    );
    expect(executions.commit.called).to.equal(false);
    expect(nextStub.calledOnce).to.equal(true);
  });

  it('fails closed when a post-claim route has no verified attempt', async () => {
    const executions = { commit: sinon.stub(), renew: sinon.stub() };
    const controller = new AgentHarnessInternalController(
      executions as never,
      authorityStub() as never,
    );
    const next = sinon.stub();
    await controller.renew(
      {
        params: { runId: 'run-1' },
        body: { attemptId: 'attempt-1', fence: 1, leaseDurationMs: 1000 },
      } as never,
      response() as never,
      next,
    );
    expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
    expect(executions.renew.called).to.equal(false);
  });

  it('maps an authority refusal to 401 before touching the store', async () => {
    const executions = { renew: sinon.stub() };
    const authority = authorityStub();
    authority.authorizeAttempt.rejects(
      new HarnessUnauthorizedError('superseded'),
    );
    const controller = new AgentHarnessInternalController(
      executions as never,
      authority as never,
    );
    const next = sinon.stub();
    await controller.renew(
      {
        params: { runId: 'run-1' },
        body: { attemptId: 'attempt-server', fence: 1, leaseDurationMs: 1000 },
        harnessAttempt: attempt(),
      } as never,
      response() as never,
      next,
    );
    const error = next.firstCall.args[0] as HttpError;
    expect(error.statusCode).to.equal(401);
    expect(error.code).to.equal('HTTP_AGENT_HARNESS_ATTEMPT_SUPERSEDED');
    expect(executions.renew.called).to.equal(false);
  });

  it('maps domain conflicts to a stable transport error code', async () => {
    const executions = {
      renew: sinon
        .stub()
        .rejects(new HarnessConflictError('STALE_ATTEMPT', 'stale')),
    };
    const controller = new AgentHarnessInternalController(
      executions as never,
      authorityStub() as never,
    );
    const next = sinon.stub();
    await controller.renew(
      {
        params: { runId: 'run-1' },
        body: { attemptId: 'attempt-1', fence: 1, leaseDurationMs: 1000 },
        harnessAttempt: attempt(),
      } as never,
      response() as never,
      next,
    );
    const error = next.firstCall.args[0] as HttpError;
    expect(error).to.be.instanceOf(HttpError);
    expect(error.code).to.equal('HTTP_AGENT_HARNESS_STALE_ATTEMPT');
    expect(error.statusCode).to.equal(409);
  });

  it('GAPS-1: renew and release answer the run without its sealed input (the worker never opens it there)', async () => {
    const stored = {
      ...run,
      input: { recordIds: [], model: { chatMode: 'auto' }, querySealed: { v: 1 }, detailsSealed: { v: 1 } },
    };
    const executions = { renew: sinon.stub().resolves(stored), release: sinon.stub().resolves(stored) };
    const controller = new AgentHarnessInternalController(executions as never, authorityStub() as never);
    const renewed = response();
    await controller.renew(
      {
        params: { runId: 'run-1' },
        body: { attemptId: 'attempt-server', fence: 1, leaseDurationMs: 1000 },
        harnessAttempt: attempt(),
      } as never,
      renewed as never,
      sinon.stub(),
    );
    const released = response();
    await controller.release(
      {
        params: { runId: 'run-1' },
        body: { attemptId: 'attempt-server', fence: 1, reason: 'fold_unreadable' },
        harnessAttempt: attempt(),
      } as never,
      released as never,
      sinon.stub(),
    );
    for (const res of [renewed, released]) {
      const body = res.json.firstCall.args[0] as { run: { input: Record<string, unknown> } };
      expect(body.run.input).to.deep.equal({ recordIds: [], model: { chatMode: 'auto' } });
    }
  });
});
