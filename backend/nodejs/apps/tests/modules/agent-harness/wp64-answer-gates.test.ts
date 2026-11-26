/**
 * WP-64 (G-13 x G-03..G-05) at the routes: the inbox's answer names the run it answers and admission reads that run's
 * gates and budget (the store cases are in `support/execution-store-contract/answers.ts`, on both stores); the run
 * routes return the gates a run is held to, as the published spec says; the worker never sees them on the run, only
 * in its pinned spec.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import yaml from 'js-yaml';
import { join } from 'path';
import sinon from 'sinon';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { RunGates } from '../../../src/modules/agent-harness/domain/run-gates';
import { answerGates } from '../../../src/modules/agent-harness/domain/run-gates';
import { workerRun } from '../../../src/modules/agent-harness/domain/steering';
import { AgentHarnessSessionController } from '../../../src/modules/agent-harness/transport/session-controller';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';
import { policyContext } from '../../../src/modules/agent-harness/application/admission/context';
import { pinSubjectOf, todayPolicy } from '../../../src/modules/agent-harness/application/admission/policy-pinning';

const GATES: RunGates = { dryRun: true, planApproval: 'required' };

const RUN = {
  runId: 'run-asked',
  orgId: 'org-1',
  sessionId: 'ses-1',
  actorId: 'u-1',
  agentKey: 'agent-w',
  userMessageKey: 'm',
  idempotencyKey: 'i',
  payloadHash: 'h',
  harnessMajor: 2,
  profile: 'dispatched',
  status: 'completed',
  outcome: 'needs_input',
  revision: 3,
  fence: 1,
  attemptId: null,
  workerId: null,
  leaseExpiresAt: null,
  absoluteDeadlineAt: 9_000,
  checkpoint: null,
  gates: GATES,
  input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
  createdAt: 1_000,
  updatedAt: 2_000,
} as unknown as HarnessRun;

const request = (body: Record<string, unknown> = {}, params: Record<string, string> = {}) => ({
  user: { orgId: 'org-1', userId: 'u-1' },
  params: { agentKey: 'agent-w', sessionId: 'ses-1', ...params },
  headers: {},
  body,
});

const response = () => {
  const res = { status: sinon.stub(), json: sinon.stub() };
  res.status.returns(res);
  res.json.returns(res);
  return res;
};

describe('WP-64: the answer turn and the run routes', () => {
  it('WP64-RT-1: an inbox answer names the run it answers to admission, which holds the turn to it', async () => {
    const submit = sinon.stub().resolves({ route: 'durable', admission: { created: true, run: RUN } });
    const get = sinon.stub().resolves(RUN);
    const controller = new AgentHarnessSessionController({ get } as never, { submit } as never, 'agent');
    const next = sinon.stub();
    await controller.submitSessionRun(
      request({ query: 'User selections: EU', idempotencyKey: 'k-1', answers: 'run-asked' }) as never,
      response() as never,
      next as never,
    );
    expect(next.called).to.equal(false);
    expect(submit.firstCall.args[0]).to.include({ answers: 'run-asked', sessionId: 'ses-1', orgId: 'org-1' });
    // A message that names no run is still checked against the conversation's last run, by admission.
    const plain = sinon.stub().resolves({ route: 'durable', admission: { created: true, run: RUN } });
    await new AgentHarnessSessionController({ get } as never, { submit: plain } as never, 'agent').submitSessionRun(
      request({ query: 'EU', idempotencyKey: 'k-2' }) as never,
      response() as never,
      sinon.stub() as never,
    );
    expect(plain.firstCall.args[0]).to.not.have.property('answers');
  });

  it('WP64-RT-2: the run routes return the gates a run is held to', async () => {
    const runs = { get: sinon.stub().resolves(RUN), effectivePolicy: sinon.stub().resolves({ pinned: false }) };
    const controller = new AgentHarnessSessionController(runs as never, {} as never, 'agent');
    const res = response();
    await controller.get(request({}, { runId: 'run-asked' }) as never, res as never, sinon.stub() as never);
    expect(res.json.firstCall.args[0].run.gates).to.deep.equal(GATES);
    const { gates: _gates, ...ungated } = RUN;
    _gates;
    const bare = response();
    await new AgentHarnessSessionController(
      { get: sinon.stub().resolves(ungated), effectivePolicy: runs.effectivePolicy } as never,
      {} as never,
      'agent',
    ).get(request({}, { runId: 'run-asked' }) as never, bare as never, sinon.stub() as never);
    expect(bare.json.firstCall.args[0].run).to.not.have.property('gates');
  });

  it('WP64-RT-3: the published run has `gates`, as the run gates a turn names them', () => {
    const spec = yaml.load(
      readFileSync(join(__dirname, '../../../src/modules/api-docs/pipeshub-openapi.yaml'), 'utf8'),
    ) as { components: { schemas: Record<string, { properties: Record<string, Record<string, unknown>> }> } };
    const run = spec.components.schemas['AgentHarnessRun']!;
    expect(run.properties['gates']?.['allOf']).to.deep.equal([{ $ref: '#/components/schemas/TurnRunGates' }]);
  });

  it('WP64-RT-4: the worker reads gates from the pinned spec only; its strict run model never sees them', () => {
    expect(workerRun(RUN)).to.not.have.property('gates');
  });

  it('WP64-RT-5: an answer’s gates only narrow: each gate of the asking run stays, the turn may add its own', () => {
    expect(answerGates(GATES, undefined)).to.deep.equal(GATES);
    expect(answerGates({ dryRun: true }, { planApproval: 'required' })).to.deep.equal(GATES);
    expect(
      answerGates(
        { verify: { checks: [{ kind: 'citations' }], onFail: 'flag' } },
        { verify: { checks: [{ kind: 'citations' }, { kind: 'file', path: 'a.pdf' }], onFail: 'continue' } },
      ),
    ).to.deep.equal({ verify: { checks: [{ kind: 'citations' }, { kind: 'file', path: 'a.pdf' }], onFail: 'flag' } });
    expect(answerGates(undefined, undefined)).to.equal(undefined);
    expect(answerGates(null, { dryRun: true })).to.deep.equal({ dryRun: true });
  });

  it('WP64-RT-10: under the control plane’s pin the answer’s cap narrows the budget as a firing’s does, the lower one held', () => {
    const request = {
      orgId: 'org-1',
      actorId: 'u-1',
      sessionId: 'ses-1',
      idempotencyKey: 'k',
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
    };
    const answering = policyContext(request, {});
    answering.answered = { runId: 'run-asked', maxCostUsd: 1.5 };
    expect(todayPolicy(answering).budgets.maxCostUsd).to.equal(1.5);
    expect(pinSubjectOf(answering, todayPolicy(answering)).trigger).to.deep.include({ budgets: { maxCostUsd: 1.5 } });
    const fired = policyContext({ ...request, trigger: { scheduleId: 's', firingId: 'f', slotKey: 'k', maxCostUsd: 0.5 } }, {});
    fired.answered = { runId: 'run-asked', maxCostUsd: 1.5 };
    expect(pinSubjectOf(fired, todayPolicy(fired)).trigger).to.deep.include({ budgets: { maxCostUsd: 0.5 } });
    expect(pinSubjectOf(policyContext(request, {}), todayPolicy(policyContext(request, {}))).trigger).to.equal(undefined);
  });

  it('WP64-RT-11: a message queued behind another person’s task keeps its gates and its answer’s cap when it drains', async () => {
    const decision = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const store = new InMemoryExecutionStore({ now: () => 1_000 });
    // The run it answers is read again at the drain, by the name it was queued with.
    const asked = sinon.stub(store, 'answeredRun').resolves({ runId: 'run-asked', gates: GATES, maxCostUsd: 1.5 });
    const admissions = new RunAdmissionService({
      store,
      rollout: { decide: async () => decision, decideForOrg: async () => ({ ...decision, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => 1_000 },
      agentAccess: grantingAccess(),
    });
    const queued = {
      orgId: 'org-1',
      actorId: 'u-1',
      agentKey: 'agent-w',
      sessionId: 'ses-1',
      idempotencyKey: 'k',
      absoluteDeadlineAt: 61_000,
      via: 'session',
      input: { query: 'EU', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
      gates: GATES,
      answered: { runId: 'run-asked', maxCostUsd: 1.5 },
    };
    const drained = await admissions.readmit(queued as never, 'Bearer u-1');
    expect(drained.gates).to.deep.equal(GATES);
    expect(drained.answered).to.deep.equal({ runId: 'run-asked', maxCostUsd: 1.5 });
    expect(asked.firstCall.args).to.deep.equal(['org-1', 'ses-1', 'run-asked']);
  });
});
