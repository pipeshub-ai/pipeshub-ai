import { expect } from 'chai';
import sinon from 'sinon';
import { AgentHarnessSessionController } from '../../../src/modules/agent-harness/transport/session-controller';

/** P2.6c over the web: an approval decision on a run's interaction, as the controller hands it to the service. */
const response = () => {
  const res = { status: sinon.stub(), json: sinon.stub() };
  res.status.returns(res);
  return res;
};

const run = {
  runId: 'run-1',
  sessionId: 'session-1',
  agentKey: 'agent-1',
  harnessMajor: 2,
  profile: 'dispatched',
  status: 'running',
  outcome: null,
  revision: 3,
  absoluteDeadlineAt: 100,
  createdAt: 1,
  updatedAt: 2,
  orgId: 'org-auth',
  actorId: 'user-auth',
  workerId: 'secret-worker',
  checkpoint: { objectId: 'private-object' },
};

const request = (body: unknown) =>
  ({
    user: { orgId: 'org-auth', userId: 'user-auth' },
    params: { agentKey: 'agent-1', sessionId: 'session-1', runId: 'run-1', interactionId: 'ix-1' },
    body,
  }) as never;

describe('AgentHarnessSessionController.decide', () => {
  it('decides as the authenticated user on the route\'s run, never the body\'s, and answers the public run', async () => {
    const interaction = { interactionId: 'ix-1', status: 'resolved', decision: 'approved' };
    const decide = sinon.stub().resolves({ receipt: { commandId: 'c-1', run }, interaction });
    const controller = new AgentHarnessSessionController({} as never, undefined, 'agent', { decide } as never);
    const res = response();
    const next = sinon.stub();
    await controller.decide(request({ decision: 'approved', idempotencyKey: 'k-1' }), res as never, next);
    expect(next.called).to.equal(false);
    expect(decide.firstCall.args[0]).to.deep.equal({
      orgId: 'org-auth',
      actorId: 'user-auth',
      sessionId: 'session-1',
      agentKey: 'agent-1',
      runId: 'run-1',
      interactionId: 'ix-1',
      decision: 'approved',
      idempotencyKey: 'k-1',
      channel: 'web',
    });
    expect(res.status.calledWith(200)).to.equal(true);
    const body = res.json.firstCall.args[0];
    expect(body.interaction).to.equal(interaction);
    expect(body.receipt.commandId).to.equal('c-1');
    expect(body.receipt.run).to.include({ runId: 'run-1', status: 'running', revision: 3 });
    expect(body.receipt.run).to.not.have.any.keys('orgId', 'actorId', 'workerId', 'checkpoint');
  });

  it('a body naming an org or anything but a decision is refused before the service is asked', async () => {
    const decide = sinon.stub();
    const controller = new AgentHarnessSessionController({} as never, undefined, 'agent', { decide } as never);
    const next = sinon.stub();
    await controller.decide(
      request({ decision: 'approved', idempotencyKey: 'k-1', orgId: 'attacker-org' }),
      response() as never,
      next,
    );
    expect(next.calledOnce).to.equal(true);
    expect(decide.called).to.equal(false);
  });

  it('without interaction decisions composed, the decision fails instead of being dropped', async () => {
    const controller = new AgentHarnessSessionController({} as never);
    const next = sinon.stub();
    const res = response();
    await controller.decide(request({ decision: 'denied', idempotencyKey: 'k-2' }), res as never, next);
    expect(next.calledOnce).to.equal(true);
    expect(res.status.called).to.equal(false);
  });
});
