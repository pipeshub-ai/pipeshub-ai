import { expect } from 'chai';
import sinon from 'sinon';
import { AgentHarnessSessionController } from '../../../src/modules/agent-harness/transport/session-controller';
import {
  HarnessConflictError,
  HarnessForbiddenError,
  HarnessUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { KILL_SWITCH } from './support/rollout';
import { UNPINNED_POLICY } from '../../../src/modules/agent-harness/domain/effective-policy';
import {
  ForbiddenError,
  HttpError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../src/libs/errors/http.errors';

const response = () => {
  const res = { status: sinon.stub(), json: sinon.stub() };
  res.status.returns(res);
  return res;
};

describe('AgentHarnessSessionController', () => {
  it('derives all authority from authenticated identity and route scope', async () => {
    const run = {
      runId: 'run-1',
      sessionId: 'session-1',
      profile: 'dispatched',
      status: 'running',
      outcome: null,
      revision: 2,
      absoluteDeadlineAt: 100,
      createdAt: 1,
      updatedAt: 2,
      orgId: 'org-auth',
      actorId: 'user-auth',
      workerId: 'secret-worker',
      checkpoint: { objectId: 'private-object' },
    };
    const runs = {
      get: sinon.stub().resolves(run),
      effectivePolicy: sinon.stub().resolves(UNPINNED_POLICY),
    };
    const controller = new AgentHarnessSessionController(runs as never);
    const res = response();
    const next = sinon.stub();

    await controller.get(
      {
        user: { orgId: 'org-auth', userId: 'user-auth' },
        params: { agentKey: 'agent-1', sessionId: 'session-1', runId: 'run-1' },
        body: { orgId: 'attacker-org' },
      } as never,
      res as never,
      next,
    );

    expect(
      runs.get.calledOnceWithExactly(
        {
          orgId: 'org-auth',
          actorId: 'user-auth',
          agentKey: 'agent-1',
          sessionId: 'session-1',
        },
        'run-1',
      ),
    ).to.equal(true);
    expect(
      res.json.calledWith({
        run: {
          runId: 'run-1',
          sessionId: 'session-1',
          harnessMajor: 1,
          profile: 'dispatched',
          status: 'running',
          outcome: null,
          revision: 2,
          absoluteDeadlineAt: 100,
          createdAt: 1,
          updatedAt: 2,
        },
        effectivePolicy: UNPINNED_POLICY,
      }),
    ).to.equal(true);
    expect(next.called).to.equal(false);
  });

  it('fails closed when authenticated identity is incomplete', async () => {
    const controller = new AgentHarnessSessionController({
      get: sinon.stub(),
    } as never);
    const next = sinon.stub();
    await controller.get(
      { user: {}, params: {} } as never,
      response() as never,
      next,
    );
    expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
  });

  it('returns a sanitized run', async () => {
    const run = {
      runId: 'run-1',
      sessionId: 'session-1',
      profile: 'dispatched',
      status: 'running',
      outcome: null,
      revision: 8,
      absoluteDeadlineAt: 100,
      createdAt: 1,
      updatedAt: 2,
      orgId: 'org-auth',
      actorId: 'user-auth',
      fence: 4,
    };
    const runs = {
      get: sinon.stub().resolves(run),
      effectivePolicy: sinon.stub().resolves(UNPINNED_POLICY),
    };
    const controller = new AgentHarnessSessionController(runs as never);
    const res = response();

    await controller.get(
      {
        user: { orgId: 'org-auth', userId: 'user-auth' },
        params: { agentKey: 'agent-1', sessionId: 'session-1', runId: 'run-1' },
      } as never,
      res as never,
      sinon.stub<unknown[]>(),
    );

    expect(
      res.json.calledWith({
        run: {
          runId: 'run-1',
          sessionId: 'session-1',
          harnessMajor: 1,
          profile: 'dispatched',
          status: 'running',
          outcome: null,
          revision: 8,
          absoluteDeadlineAt: 100,
          createdAt: 1,
          updatedAt: 2,
        },
        effectivePolicy: UNPINNED_POLICY,
      }),
    ).to.equal(true);
  });

  it('maps domain conflicts to stable HTTP conflict codes', async () => {
    const runs = {
      control: sinon
        .stub()
        .rejects(new HarnessConflictError('REVISION_CONFLICT', 'stale')),
    };
    const controller = new AgentHarnessSessionController(runs as never);
    const next = sinon.stub();
    await controller.resume(
      {
        user: { orgId: 'org-1', userId: 'user-1' },
        params: { agentKey: 'agent', sessionId: 'session', runId: 'run' },
        body: { expectedRevision: 3, idempotencyKey: 'resume-1' },
      } as never,
      response() as never,
      next,
    );

    const error = next.firstCall.args[0] as HttpError;
    expect(error.code).to.equal('HTTP_AGENT_HARNESS_REVISION_CONFLICT');
    expect(error.statusCode).to.equal(409);
  });

  it('tells the client to use the legacy path when the rollout declines', async () => {
    const admissions = {
      submit: sinon.stub().resolves({ route: 'legacy', rollout: KILL_SWITCH }),
    };
    const controller = new AgentHarnessSessionController(
      {} as never,
      admissions as never,
    );
    const res = response();
    const next = sinon.stub();

    await controller.createSessionRun(
      {
        user: { orgId: 'org-1', userId: 'user-1' },
        params: { agentKey: 'agent-1' },
        body: {
          idempotencyKey: 'submit-1',
          deadlineMs: 60_000,
          query: 'hello',
          recordIds: [],
          filters: {},
          attachments: [],
          chatMode: 'auto',
        },
      } as never,
      res as never,
      next,
    );

    expect(next.called).to.equal(false);
    expect(res.status.calledWith(200)).to.equal(true);
    expect(res.json.firstCall.args[0]).to.deep.equal({
      admission: null,
      rollout: KILL_SWITCH,
    });
  });

  it('fails closed when rollout availability cannot be verified', async () => {
    const admissions = {
      submit: sinon
        .stub()
        .rejects(new HarnessUnavailableError('could not be verified')),
    };
    const controller = new AgentHarnessSessionController(
      {} as never,
      admissions as never,
    );
    const next = sinon.stub();

    await controller.createSessionRun(
      {
        user: { orgId: 'org-1', userId: 'user-1' },
        params: { agentKey: 'agent-1' },
        body: {
          idempotencyKey: 'submit-1',
          deadlineMs: 60_000,
          query: 'hello',
          recordIds: [],
          filters: {},
          attachments: [],
          chatMode: 'auto',
        },
      } as never,
      response() as never,
      next,
    );

    expect(next.firstCall.args[0]).to.be.instanceOf(ServiceUnavailableError);
  });

  it('admits with server-derived identity while the feature is enabled', async () => {
    const run = {
      runId: 'run-1',
      sessionId: 'session-1',
      profile: 'dispatched',
      status: 'queued',
      outcome: null,
      revision: 0,
      absoluteDeadlineAt: 100,
      createdAt: 1,
      updatedAt: 1,
    };
    const admissions = {
      submit: sinon
        .stub()
        .resolves({ route: 'durable', admission: { run, created: true } }),
    };
    const controller = new AgentHarnessSessionController(
      {} as never,
      admissions as never,
    );
    const res = response();

    await controller.createSessionRun(
      {
        user: { orgId: 'org-auth', userId: 'user-auth' },
        params: { agentKey: 'agent-1' },
        body: {
          idempotencyKey: 'submit-1',
          deadlineMs: 60_000,
          query: 'hello',
          recordIds: [],
          filters: {},
          attachments: [],
          chatMode: 'auto',
        },
      } as never,
      res as never,
      sinon.stub<unknown[]>(),
    );

    expect(admissions.submit.firstCall.args[0]).to.deep.include({
      orgId: 'org-auth',
      actorId: 'user-auth',
      agentKey: 'agent-1',
      idempotencyKey: 'submit-1',
      deadlineMs: 60_000,
    });
    expect(res.status.calledWith(202)).to.equal(true);
    // Pre-pinning runs carry no major; the DTO reports the v1 they ran on.
    expect(res.json.firstCall.args[0].admission.run.harnessMajor).to.equal(1);
  });

  it('admits built-in chat without inventing an agent identity', async () => {
    const run = {
      runId: 'run-chat',
      sessionId: 'session-chat',
      profile: 'dispatched',
      status: 'queued',
      outcome: null,
      revision: 0,
      absoluteDeadlineAt: 100,
      createdAt: 1,
      updatedAt: 1,
    };
    const admissions = {
      submit: sinon
        .stub()
        .resolves({ route: 'durable', admission: { run, created: true } }),
    };
    const controller = new AgentHarnessSessionController(
      {} as never,
      admissions as never,
      'chat',
    );

    await controller.createSessionRun(
      {
        user: { orgId: 'org-auth', userId: 'user-auth' },
        params: {},
        body: {
          idempotencyKey: 'submit-chat',
          deadlineMs: 60_000,
          query: 'hello',
          recordIds: [],
          filters: {},
          attachments: [],
          chatMode: 'auto',
        },
      } as never,
      response() as never,
      sinon.stub<unknown[]>(),
    );

    expect(admissions.submit.firstCall.args[0]).to.deep.include({
      orgId: 'org-auth',
      actorId: 'user-auth',
      idempotencyKey: 'submit-chat',
    });
    expect(admissions.submit.firstCall.args[0]).not.to.have.property('agentKey');
  });

  it('forwards the caller credential and maps a denied agent to 403', async () => {
    const admissions = {
      submit: sinon.stub().rejects(new HarnessForbiddenError()),
    };
    const controller = new AgentHarnessSessionController(
      {} as never,
      admissions as never,
    );
    const next = sinon.stub();
    await controller.createSessionRun(
      {
        user: { orgId: 'org-auth', userId: 'user-auth' },
        headers: { authorization: 'Bearer user-token' },
        params: { agentKey: 'someone-elses-agent' },
        body: {
          idempotencyKey: 'submit-denied',
          deadlineMs: 60_000,
          query: 'hello',
          recordIds: [],
          filters: {},
          attachments: [],
          chatMode: 'auto',
        },
      } as never,
      response() as never,
      next,
    );
    expect(admissions.submit.firstCall.args[0]).to.deep.include({
      agentKey: 'someone-elses-agent',
      authorization: 'Bearer user-token',
    });
    const error = next.firstCall.args[0] as ForbiddenError;
    expect(error).to.be.instanceOf(ForbiddenError);
    expect(error.statusCode).to.equal(403);
  });

  it('SYNCMAIN2: a token limited to some knowledge (main #4451) starts no durable run: 403, nothing submitted', async () => {
    const admissions = { submit: sinon.stub() };
    const controller = new AgentHarnessSessionController(
      {} as never,
      admissions as never,
    );
    const next = sinon.stub();
    await controller.createSessionRun(
      {
        user: {
          orgId: 'org-auth',
          userId: 'user-auth',
          isOAuth: true,
          oauthClientId: 'pat-system:org-auth',
          knowledgeLimit: { apps: [], recordGroups: ['kb-1'], records: [] },
        },
        headers: { authorization: 'Bearer limited-token' },
        params: { agentKey: 'agent-1' },
        body: {
          idempotencyKey: 'submit-limited',
          deadlineMs: 60_000,
          query: 'hello',
          recordIds: [],
          filters: {},
          attachments: [],
          chatMode: 'auto',
        },
      } as never,
      response() as never,
      next,
    );
    expect(admissions.submit.called).to.equal(false);
    const error = next.firstCall.args[0] as ForbiddenError;
    expect(error).to.be.instanceOf(ForbiddenError);
    expect(error.message).to.contain('limited to some of your knowledge');
  });

  it('records how the caller authenticated, from the token only', async () => {
    const run = {
      runId: 'run-1',
      sessionId: 'session-1',
      profile: 'dispatched',
      status: 'queued',
      outcome: null,
      revision: 0,
      absoluteDeadlineAt: 100,
      createdAt: 1,
      updatedAt: 1,
    };
    const vias: unknown[] = [];
    const admissions = {
      submit: sinon.stub().callsFake(async (request) => {
        vias.push(request.via);
        return { route: 'durable', admission: { run, created: true } };
      }),
    };
    const controller = new AgentHarnessSessionController(
      {} as never,
      admissions as never,
    );
    for (const [user, tokenPayload] of [
      [{ orgId: 'o', userId: 'u' }, undefined],
      [{ orgId: 'o', userId: 'u', isOAuth: true, oauthClientId: 'pat-system:o' }, undefined],
      [{ orgId: 'o', userId: 'u', isOAuth: true, oauthClientId: 'client-1' }, undefined],
      // The Slack bot's scoped token, hydrated to a user with an account.
      [{ orgId: 'o', userId: 'u' }, { email: 'a@example.com', scopes: ['conversation:create'] }],
      // ... and to a service account (no PipesHub account).
      [{ orgId: 'o', userId: 'u', isServiceAccount: true }, { email: 'b@example.com' }],
      // An OAuth claim cannot turn a service credential into anything else.
      [{ orgId: 'o', userId: 'u', isOAuth: true, oauthClientId: 'client-1' }, { email: 'c@example.com' }],
    ] as const) {
      await controller.createSessionRun(
        {
          user,
          ...(tokenPayload !== undefined ? { tokenPayload } : {}),
          params: { agentKey: 'agent-1' },
          body: {
            idempotencyKey: 'k',
            deadlineMs: 60_000,
            query: 'hello',
            recordIds: [],
            filters: {},
            attachments: [],
            chatMode: 'auto',
          },
        } as never,
        response() as never,
        sinon.stub<unknown[]>(),
      );
    }
    expect(vias).to.deep.equal(['session', 'pat', 'oauth_app', 'service', 'service', 'service']);
  });
});
