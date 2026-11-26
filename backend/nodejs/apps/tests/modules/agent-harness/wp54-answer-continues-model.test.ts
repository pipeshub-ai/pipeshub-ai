/**
 * G-13 (C20.14), WP-54: an answer from the inbox Questions tab continues on the model the asking run used. The
 * answer is the conversation's next message (`POST …/runs`) and names the run it answers (`answers`); the server
 * reads that run, as the person's own run in that conversation, and takes its model, mode, model account and
 * tools where the body names none. Without it the turn ran on the org's default model, which may be a paid one.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { admissionBodySchema } from '../../../src/modules/agent-harness/transport/schemas';
import { AgentHarnessSessionController } from '../../../src/modules/agent-harness/transport/session-controller';

const ASKED = {
  orgId: 'org-1',
  sessionId: 'ses-1',
  runId: 'run-asked',
  actorId: 'u-1',
  agentKey: 'agent-w',
  input: {
    query: 'q',
    recordIds: [],
    filters: {},
    attachments: [],
    tools: ['jira.create_issue'],
    agentCapabilities: { internalSearch: true, webSearch: false },
    model: {
      modelKey: 'scripted-model',
      modelName: 'scripted',
      modelProvider: 'openAICompatible',
      modelFriendlyName: 'Scripted',
      reasoningEffort: 'low',
      chatMode: 'agent',
      accountId: 'cloud/acct-1',
    },
  },
} as unknown as HarnessRun;

function world(found: HarnessRun | Error = ASKED) {
  const get = sinon.stub().callsFake(async () => {
    if (found instanceof Error) throw found;
    return found;
  });
  const submit = sinon.stub().resolves({ route: 'legacy', rollout: {} });
  const controller = new AgentHarnessSessionController({ get } as never, { submit } as never, 'agent');
  const call = async (body: Record<string, unknown>) => {
    const req = {
      user: { orgId: 'org-1', userId: 'u-1' },
      params: { agentKey: 'agent-w', sessionId: 'ses-1' },
      headers: {},
      body,
    };
    const res = { status: () => res, json: () => res };
    const next = sinon.stub();
    await controller.submitSessionRun(req as never, res as never, next);
    return next;
  };
  return { get, submit, call };
}

describe('WP-54 G-13: an inbox answer continues on the asking run’s model', () => {
  it('G-13: the body names the run it answers; the turn runs on that run’s model, mode, account and tools', async () => {
    const w = world();
    const next = await w.call({ query: 'User selections: Q4', idempotencyKey: 'k-1', answers: 'run-asked' });
    expect(next.called).to.equal(false);
    expect(w.get.firstCall.args).to.deep.equal([
      { orgId: 'org-1', actorId: 'u-1', sessionId: 'ses-1', agentKey: 'agent-w' },
      'run-asked',
    ]);
    const input = w.submit.firstCall.args[0].input;
    expect(input.model).to.deep.equal(ASKED.input?.model);
    expect(input.tools).to.deep.equal(['jira.create_issue']);
    expect(input.agentCapabilities).to.deep.equal({ internalSearch: true, webSearch: false });
  });

  it('G-13: a model the body names wins; nothing is read from a run the body does not name', async () => {
    const w = world();
    await w.call({ query: 'a', idempotencyKey: 'k-2', answers: 'run-asked', modelKey: 'other', chatMode: 'quick' });
    expect(w.submit.firstCall.args[0].input.model).to.deep.equal({ modelKey: 'other', chatMode: 'quick' });
    const plain = world();
    await plain.call({ query: 'a', idempotencyKey: 'k-3' });
    expect(plain.get.called).to.equal(false);
    expect(plain.submit.firstCall.args[0].input.model).to.deep.equal({ chatMode: 'auto' });
  });

  it('G-13: a run that is not the person’s own in this conversation is not found, and nothing is admitted', async () => {
    const w = world(new HarnessNotFoundError());
    const next = await w.call({ query: 'a', idempotencyKey: 'k-4', answers: 'run-else' });
    expect(next.firstCall.args[0]).to.be.instanceOf(Error);
    expect(w.submit.called).to.equal(false);
  });

  it('G-13: `answers` is a bounded run id', () => {
    expect(admissionBodySchema.safeParse({ query: 'a', idempotencyKey: 'k', answers: 'run-1' }).success).to.equal(true);
    expect(admissionBodySchema.safeParse({ query: 'a', idempotencyKey: 'k', answers: 'bad id/..' }).success).to.equal(false);
  });
});
