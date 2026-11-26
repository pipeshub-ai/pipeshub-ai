/**
 * CPS NR-1..NR-6 (and FUQUICK-FU-1's FUQ1-1..2): a message that waited behind someone else's run (SS2
 * next_run) is admitted as a fresh run of its sender's would be when it
 * drains, not with the grant frozen when it was queued (R2 F6). Alice owns an
 * agent session shared with Bob; her message waits behind Bob's run; between
 * the queueing and the drain the policy changes. Everything is the in-memory
 * store, the real admission service and the drain as composition wires it.
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { Types } from 'mongoose';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { ActorReadmission } from '../../../src/modules/agent-harness/application/actor-readmission';
import { NextRunService } from '../../../src/modules/agent-harness/application/next-run-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import { ActorAuthority, ActorRefusal } from '../../../src/modules/agent-harness/ports/schedules';
import { agentDefinition } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { readmissionBy } from './support/next-runs';
import { fixedRollout, HARNESS_ENABLED } from './support/rollout';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import { RoutingSubject } from '../../../src/modules/agent-harness/ports/routing-decider';
import { FakeEngineCeilings, FakeModelAccessPolicies } from './support/org-policy-fakes';

const AGENT = 'agent-x';

describe('CPS next-run re-admission: a drained message is admitted as its sender now', () => {
  const org = new Types.ObjectId().toString();
  const [alice, bob] = [0, 1].map(() => new Types.ObjectId().toString()) as [string, string];
  let store: InMemoryExecutionStore;
  let ceilings: FakeEngineCeilings;
  let policies: FakeModelAccessPolicies;
  let definition: AgentRunDefinition;
  let revoked: Set<string>;
  let actorRefusals: Map<string, ActorRefusal>;
  let lines: Array<[string, Record<string, unknown>]>;

  const record = (message: string, meta: Record<string, unknown>) => void lines.push([message, meta]);
  const logger = { debug: () => undefined, info: record, warn: record, error: record };

  const admission = (): RunAdmissionService =>
    new RunAdmissionService({
      store,
      rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      agentAccess: {
        authorizeRun: async (request) =>
          revoked.has(request.actorId) || request.authorization !== `Bearer ${request.actorId}`
            ? null
            : structuredClone(definition),
      },
      roles: store,
      collaboratorTurns: { enabled: async () => true },
      engineProfiles: ceilings,
      modelAccess: testModelAccess({ policies }),
      executors: profileExecutor,
      logger,
    });

  const actors: Pick<ActorAuthority, 'authorize'> = {
    authorize: async (_orgId, actorId) => {
      const refused = actorRefusals.get(actorId);
      return refused !== undefined ? { refused } : { authorization: `Bearer ${actorId}` };
    },
  };

  const drains = () =>
    new NextRunService({
      store,
      readmission: readmissionBy(admission, actors),
      collaboratorTurns: { enabled: async () => true },
      logger,
    });

  const send = async (actorId: string, sessionId?: string) => {
    const outcome = await admission().submit({
      orgId: org,
      actorId,
      agentKey: AGENT,
      authorization: `Bearer ${actorId}`,
      ...(sessionId !== undefined ? { sessionId } : {}),
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable admission');
    return outcome.admission;
  };

  /** Alice's message waiting behind Bob's run in her session, Bob's run then ended. */
  const aliceQueuedBehindBob = async (): Promise<{ sessionId: string; runId: string }> => {
    const first = await send(alice);
    await store.failRun(org, first.run.runId, 'ended', []);
    store.shareSession(org, first.sessionId, bob, 'write');
    const bobs = await send(bob, first.sessionId);
    const waiting = await send(alice, first.sessionId);
    expect(waiting.nextRun?.position).to.equal(1);
    await store.failRun(org, bobs.run.runId, 'ended', []);
    return { sessionId: first.sessionId, runId: waiting.nextRun?.runId as string };
  };

  const refusedWith = (): unknown =>
    lines.find(([message]) => message === 'next_run.refused')?.[1]?.['reason'];

  beforeEach(() => {
    store = new InMemoryExecutionStore();
    ceilings = new FakeEngineCeilings();
    policies = new FakeModelAccessPolicies();
    definition = agentDefinition({ agentKey: AGENT });
    revoked = new Set();
    actorRefusals = new Map();
    lines = [];
  });

  it('NR-1: a sender who lost access to the agent while waiting is refused agent_forbidden', async () => {
    const { sessionId, runId } = await aliceQueuedBehindBob();
    revoked.add(alice);

    expect(await drains().drain(org, sessionId)).to.equal('empty');
    expect(refusedWith()).to.equal('agent_forbidden');
    expect(await store.get(org, runId)).to.equal(null);
  });

  it("NR-2: an engine profile the org's ceiling no longer admits leaves the drained turn without code_task (CTL-6)", async () => {
    const { sessionId, runId } = await aliceQueuedBehindBob();
    definition = agentDefinition({ agentKey: AGENT, engineProfile: { engines: ['opencode'] } });
    await ceilings.write(org, { engines: ['codex'] }, 'p_admin');

    expect(await drains().drain(org, sessionId)).to.equal('admitted');
    expect((await store.get(org, runId))?.requires?.features).to.not.include('code_task_tool');
  });

  it("NR-3: a model access policy is decided when the drained turn hands off, not at the drain (CTL-6)", async () => {
    const { sessionId, runId } = await aliceQueuedBehindBob();
    definition = agentDefinition({ agentKey: AGENT, engineProfile: { engines: ['opencode'] } });
    await policies.write(org, { disabledRules: ['openai-api-key-org'], consumerPlans: false }, 'p_admin');

    expect(await drains().drain(org, sessionId)).to.equal('admitted');
    expect((await store.get(org, runId))?.requires?.features).to.include('code_task_tool');
  });

  it('NR-4: a sender disabled or deleted while waiting is refused with the actor reason', async () => {
    const { sessionId, runId } = await aliceQueuedBehindBob();
    actorRefusals.set(alice, 'actor_disabled');

    expect(await drains().drain(org, sessionId)).to.equal('empty');
    expect(refusedWith()).to.equal('actor_disabled');
    expect(await store.get(org, runId)).to.equal(null);
  });

  it('NR-5: unchanged access admits the run with the grant decided at the drain', async () => {
    const { sessionId, runId } = await aliceQueuedBehindBob();
    definition = agentDefinition({ agentKey: AGENT, definitionVersion: 8, maxChildren: 1 });

    expect(await drains().drain(org, sessionId)).to.equal('admitted');
    const subject = await store.readClaimSubject(org, runId);
    expect(subject?.run.actorId).to.equal(alice);
    expect(subject?.agentDefinition).to.include({ definitionVersion: 8, maxChildren: 1 });
  });

  it('NR-6: without an actor authority nothing can be decided, so nothing is admitted', async () => {
    const { sessionId, runId } = await aliceQueuedBehindBob();
    const unwired = new NextRunService({
      store,
      readmission: new ActorReadmission(null, admission),
      logger,
    });

    expect(await unwired.drain(org, sessionId)).to.equal('empty');
    expect(refusedWith()).to.equal('unavailable');
    expect(await store.get(org, runId)).to.equal(null);
  });
  it('FUQ1-1: a message drained after the run it waited behind ended on a question is held to that run’s gates and cap', async () => {
    const first = await send(alice);
    await store.failRun(org, first.run.runId, 'ended', []);
    store.shareSession(org, first.sessionId, bob, 'write');
    const bobs = await send(bob, first.sessionId);
    const waiting = await send(alice, first.sessionId);
    const runId = waiting.nextRun?.runId as string;
    // Queued while Bob's run was still working: nothing to answer yet.
    expect((await store.nextPending(org, first.sessionId))?.command.answered).to.equal(undefined);
    // Bob's run ends asking (the store side of that read is the contract's WP64-AG-9, on both stores).
    await store.failRun(org, bobs.run.runId, 'ended', []);
    const answeredRun = store.answeredRun.bind(store);
    store.answeredRun = async (orgId, sessionId, named) =>
      named === undefined && sessionId === first.sessionId
        ? { runId: bobs.run.runId, gates: { dryRun: true }, maxCostUsd: 1.25 }
        : answeredRun(orgId, sessionId, named);
    const admitted: Array<Record<string, unknown>> = [];
    const admit = store.admit.bind(store);
    store.admit = async (command, now) => {
      admitted.push(structuredClone(command) as unknown as Record<string, unknown>);
      return admit(command, now);
    };
    const told: string[] = [];
    const drain = new NextRunService({
      store,
      readmission: readmissionBy(admission, actors),
      collaboratorTurns: { enabled: async () => true },
      logger,
      questionAnswered: (_org, asking) => told.push(asking),
    });

    expect(await drain.drain(org, first.sessionId)).to.equal('admitted');
    const drained = await store.get(org, runId);
    expect(drained?.gates).to.deep.equal({ dryRun: true });
    expect(admitted[0]?.['answered']).to.deep.equal({ runId: bobs.run.runId, maxCostUsd: 1.25 });
    expect(told, 'the asker is told from the answer the drain admitted').to.deep.equal([bobs.run.runId]);
  });

  it('FUQ1-2: a drained message is refused, as a fresh answer is, when whether it answers cannot be read', async () => {
    const { sessionId, runId } = await aliceQueuedBehindBob();
    const answeredRun = store.answeredRun.bind(store);
    store.answeredRun = async () => {
      throw new Error('store down');
    };
    try {
      expect(await drains().drain(org, sessionId)).to.equal('empty');
    } finally {
      store.answeredRun = answeredRun;
    }
    expect(refusedWith()).to.equal('unavailable');
    expect(await store.get(org, runId)).to.equal(null);
  });

  describe('ADM-25 (QA8-F2): the drain is routed as a fresh turn of the sender\'s', () => {
    const settings = (families: string[] | null): HarnessRolloutSettings => ({
      infrastructureReady: true,
      killSwitch: false,
      orgEnabled: true,
      modelFamilies: families,
    });

    it('ADM-25: the RoutingDecider is asked once with the queued turn\'s subject', async () => {
      const { sessionId } = await aliceQueuedBehindBob();
      const asked: RoutingSubject[] = [];
      const drain = new NextRunService({
        store,
        readmission: readmissionBy(admission, actors),
        collaboratorTurns: { enabled: async () => true },
        logger,
        routing: {
          decide: async (subject) => {
            asked.push(subject);
            return { routing: { ...HARNESS_ENABLED, harnessMajor: 2 }, scope: {} };
          },
        },
      });

      expect(await drain.drain(org, sessionId)).to.equal('admitted');
      expect(asked).to.have.length(1);
      expect(asked[0]).to.deep.include({ orgId: org, actorId: alice, agentKey: AGENT, sessionId });
      expect(asked[0]?.model).to.deep.equal({ chatMode: 'auto' });
    });

    it('CPD5-10: model families narrowed while it waited: refused model_family_not_allowed, nothing admitted', async () => {
      const { sessionId, runId } = await aliceQueuedBehindBob();
      const drain = new NextRunService({
        store,
        readmission: readmissionBy(admission, actors),
        collaboratorTurns: { enabled: async () => true },
        logger,
        rollout: new HarnessRollout(
          { read: async () => settings(['anthropic']) },
          { resolve: async () => 'openai' },
        ),
      });

      expect(await drain.drain(org, sessionId)).to.equal('empty');
      expect(refusedWith()).to.equal('model_family_not_allowed');
      expect(await store.get(org, runId)).to.equal(null);
    });

    it('CPD5-10: the same families admit it', async () => {
      const { sessionId } = await aliceQueuedBehindBob();
      const drain = new NextRunService({
        store,
        readmission: readmissionBy(admission, actors),
        collaboratorTurns: { enabled: async () => true },
        logger,
        rollout: new HarnessRollout(
          { read: async () => settings(['openai']) },
          { resolve: async () => 'openai' },
        ),
      });

      expect(await drain.drain(org, sessionId)).to.equal('admitted');
    });

    it('ADM-25: an undecidable route is an outage: the message keeps its place', async () => {
      const { sessionId, runId } = await aliceQueuedBehindBob();
      const drain = new NextRunService({
        store,
        readmission: readmissionBy(admission, actors),
        collaboratorTurns: { enabled: async () => true },
        logger,
        routing: { decide: async () => Promise.reject(new Error('settings down')) },
      });

      const error = await drain.drain(org, sessionId).catch((e: unknown) => e);
      expect((error as Error).name).to.equal('HarnessUnavailableError');
      expect(refusedWith()).to.equal(undefined);
      expect(await store.get(org, runId)).to.equal(null);
      expect(await drains().drain(org, sessionId)).to.equal('admitted');
    });
  });
});
