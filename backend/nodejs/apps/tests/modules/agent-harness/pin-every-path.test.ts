/**
 * PIN-25 (CPD-5, ADM-25): every admission path reaches the one pinning step:
 * a web turn, a regenerate, Slack and MCP turns, a schedule's firing and a
 * drained next run, each with its surface. The real admission service and
 * in-memory store; a spy pinner in `shadow`.
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { NextRunService } from '../../../src/modules/agent-harness/application/next-run-service';
import { RunAdmissionService, SubmitDurableRun } from '../../../src/modules/agent-harness/application/run-admission-service';
import { RunAdmissionScheduledRuns } from '../../../src/modules/agent-harness/application/scheduled-run-admission';
import { profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import type { PinSubject } from '../../../src/modules/control-plane';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { pinnerOf } from './support/a-pin';
import { readmissionBy } from './support/next-runs';
import { HARNESS_ENABLED } from './support/rollout';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const ORG = 'org-paths';
const INPUT = { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } };

describe('PIN-25: every admission path reaches the pinning step', () => {
  let store: InMemoryExecutionStore;
  let pinned: PinSubject[];
  let step: PolicyPinning;
  let admission: RunAdmissionService;

  const send = (actorId: string, request: Partial<SubmitDurableRun> = {}) =>
    admission.submit({
      orgId: ORG,
      actorId,
      agentKey: 'a7',
      authorization: `Bearer ${actorId}`,
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: INPUT,
      ...request,
    });

  beforeEach(() => {
    store = new InMemoryExecutionStore();
    pinned = [];
    step = new PolicyPinning({
      mode: 'shadow',
      pinner: pinnerOf(async (subject) => (pinned.push(subject), Promise.reject(new Error('spy')))),
      metrics: { mismatch: () => undefined },
      logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
    });
    admission = new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
      roles: store,
      collaboratorTurns: { enabled: async () => true },
      executors: profileExecutor,
      policySteps: [step],
    });
  });

  const surfaces = async (): Promise<string[]> => {
    await step.idle();
    return pinned.map((subject) => subject.surface);
  };

  it('web, Slack and MCP turns pin once each, with their surface', async () => {
    await send('u1');
    await send('u1', { surface: 'slack', via: 'oauth_app' });
    await send('u1', { surface: 'mcp' });
    expect(await surfaces()).to.deep.equal(['web', 'slack', 'mcp']);
    expect(pinned.map((subject) => [subject.orgId, subject.actorId, subject.agentKey])).to.deep.equal(
      Array(3).fill([ORG, 'u1', 'a7']),
    );
  });

  it('a regenerate pins before the store answers', async () => {
    const first = await send('u1');
    if (first.route !== 'durable') return expect.fail('durable');
    await send('u1', { sessionId: first.admission.sessionId, regenerate: { messageId: 'm1' } }).catch(() => undefined);
    expect(await surfaces()).to.deep.equal(['web', 'web']);
  });

  it("a schedule's firing pins as the schedule", async () => {
    await new RunAdmissionScheduledRuns(admission).admit({
      orgId: ORG,
      actorId: 'u1',
      agentKey: 'a7',
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: INPUT,
      authorization: 'Bearer u1',
      trigger: { scheduleId: 's1', firingId: 'f1', slotKey: 'k1', maxCostUsd: 1 },
    });
    expect(await surfaces()).to.deep.equal(['schedule']);
    expect(pinned[0]?.trigger).to.deep.include({ budgets: { maxCostUsd: 1 } });
  });

  it('a drained next run pins as next_run, as its sender', async () => {
    const first = await send('alice');
    if (first.route !== 'durable') return expect.fail('durable');
    await store.failRun(ORG, first.admission.run.runId, 'ended', []);
    store.shareSession(ORG, first.admission.sessionId, 'bob', 'write');
    const bobs = await send('bob', { sessionId: first.admission.sessionId });
    if (bobs.route !== 'durable') return expect.fail('durable');
    await send('alice', { sessionId: first.admission.sessionId });
    await store.failRun(ORG, bobs.admission.run.runId, 'ended', []);
    pinned = [];
    const drains = new NextRunService({
      store,
      readmission: readmissionBy(() => admission),
      collaboratorTurns: { enabled: async () => true },
    });
    expect(await drains.drain(ORG, first.admission.sessionId)).to.equal('admitted');
    expect(await surfaces()).to.deep.equal(['next_run']);
    expect(pinned[0]?.actorId).to.equal('alice');
  });
});
