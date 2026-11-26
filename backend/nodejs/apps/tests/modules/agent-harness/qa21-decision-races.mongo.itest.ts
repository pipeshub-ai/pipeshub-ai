/**
 * QA-21: races at the decision points of a running run, on a real MongoDB
 * replica set (and Redis for the wakes). Each race is forced at the store
 * seam (support/store-race.ts): every racer has read its transaction's
 * snapshot before any of them writes, then they go at once or in a scripted
 * order, so the later one decides on a state the earlier commit made stale.
 * For each: exactly one outcome wins, the loser gets a clear answer, no
 * effect is duplicated, and the session's journal is a valid history
 * (support/journal-history.ts). Run instructions in execution-store.mongo.itest.ts.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import {
  ContractBackend,
  ContractHarness,
  contractClock,
  createContractHarness,
} from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';
import { byParty, codeOf, expectForced, expectValidHistory as expectValid, ordersOf, statusOf, winners } from './support/race-assertions';
import { StoreRace } from './support/store-race';
import { ChannelRecorder, RedisItRig, recordChannel, redisItRig } from './support/redis-it';
import { InteractionService } from '../../../src/modules/agent-harness/application/interaction-service';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { RedisRunWakeup, wakeChannel } from '../../../src/modules/agent-harness/adapters/redis/redis-run-wakeup';
import {
  CommitBatch,
  HarnessRun,
  InteractionDecision,
  RunTransition,
} from '../../../src/modules/agent-harness/domain/execution';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { HarnessChatSessionRepository } from '../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import { UserGroups } from '../../../src/modules/user_management/schema/userGroup.schema';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { PolicyLayerModel } from '../../../src/libs/repositories/control-plane/policy/schema/policy.schema';

registerHarnessModels([Users, UserGroups, PolicyLayerModel] as unknown as Array<Model<never>>);

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const AGENT_RUN = { agentKey: 'agent-1', authorization: 'Bearer user' };

describe('QA-21 races at the decision points of a run (MongoDB replica set, Redis)', function () {
  this.timeout(120_000);
  let h: ContractHarness;
  let backend: ContractBackend;
  let redis: RedisItRig;
  const recorders: ChannelRecorder[] = [];

  before(connectHarnessMongo);
  before(async () => {
    redis = redisItRig('qa21');
  });
  after(async () => {
    await redis.dispose();
    await disconnectHarnessMongo();
  });
  beforeEach(async () => {
    await resetHarnessMongo();
    const clock = contractClock();
    backend = mongoBackend({ clock });
    h = createContractHarness(backend, clock);
    h.setRollout(V2);
  });
  afterEach(() => {
    for (const recorder of recorders.splice(0)) recorder.close();
  });

  const stream = (run: Pick<HarnessRun, 'orgId' | 'sessionId'>) =>
    backend.journal.readStream(run.orgId, run.sessionId, 0, 1_000);
  const types = async (run: Pick<HarnessRun, 'orgId' | 'sessionId'>) =>
    (await stream(run)).map((event) => event.type);
  const count = async (run: Pick<HarnessRun, 'orgId' | 'sessionId'>, type: string) =>
    (await types(run)).filter((each) => each === type).length;
  const expectValidHistory = (run: Pick<HarnessRun, 'orgId' | 'sessionId'>) => expectValid(backend, run);
  const wakesOf = async (runId: string): Promise<ChannelRecorder> => {
    const recorder = await recordChannel(redis.provider, wakeChannel(runId, redis.provider.keyNamespace));
    recorders.push(recorder);
    return recorder;
  };
  const current = async (run: Pick<HarnessRun, 'orgId' | 'runId'>) =>
    (await h.service.get(run.orgId, run.runId)) as HarnessRun;

  const claimAs = (run: Pick<HarnessRun, 'orgId' | 'runId'>, worker: string) =>
    h.service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: worker,
      attemptId: `attempt-${worker}`,
      leaseDurationMs: 5_000,
    });
  const commit = (
    run: HarnessRun,
    worker: string,
    commandId: string,
    events: CommitBatch['events'],
    transition?: RunTransition,
  ) =>
    h.service.commit({
      commandId,
      orgId: run.orgId,
      runId: run.runId,
      workerId: worker,
      attemptId: `attempt-${worker}`,
      fence: run.fence,
      events,
      legacyEvents: [],
      ...(transition !== undefined ? { transition } : {}),
      outbox: [],
    });
  const requested = (interactionId: string) =>
    workerEvent('interaction.requested', {
      interactionId,
      kind: 'approval',
      bindingDigest: `sha256:${'1'.repeat(64)}`,
    });
  const explained = (interactionId: string) =>
    workerEvent(
      'interaction.explained',
      {
        interactionId,
        rule: 'flow_external_write',
        toolId: 'slack@default/send_message',
        sensitivity: 'private',
        sources: [{ title: 'Q3 plan', recordId: 'rec-1' }],
        moreSources: 0,
        destination: { label: '#sales', key: `sha256:${'2'.repeat(64)}` },
        conversationGrant: true,
      },
      { critical: false },
    );
  const claimed = async () => {
    const admitted = await h.admit(AGENT_RUN);
    return (await claimAs(admitted.run, 'worker-a')).run;
  };
  /** A claimed v2 run holding `ix` for approval; suspended for it when `suspend`. */
  const holding = async (ix = 'ix-a', suspend = false, offerGrant = false) => {
    const run = await claimed();
    await commit(
      run,
      'worker-a',
      'worker-a:ask:1',
      [...(offerGrant ? [explained(ix)] : []), requested(ix)],
      suspend
        ? { status: 'waiting_approval', detail: { kind: 'suspend', reason: 'approval', interactionId: ix } }
        : undefined,
    );
    return run;
  };
  const decide = (
    run: HarnessRun,
    commandId: string,
    decision: InteractionDecision = 'approved',
    interactionId = 'ix-a',
  ) =>
    new InteractionService(h.service, new RedisRunWakeup(redis.provider)).resolve({
      orgId: run.orgId,
      runId: run.runId,
      interactionId,
      decision,
      channel: 'web',
      by: `p_${commandId}`,
      commandId,
    });
  /** The user's Stop, as the session route sends it: no revision, re-read if a commit moved the run. */
  const stop = (run: HarnessRun, key = 'stop-1') => {
    h.service.wakeOnCancel(new RedisRunWakeup(redis.provider));
    return new SessionRunService(h.service, new HarnessChatSessionRepository()).control({
      orgId: run.orgId,
      actorId: run.actorId,
      sessionId: run.sessionId,
      agentKey: AGENT_RUN.agentKey,
      runId: run.runId,
      idempotencyKey: key,
      action: 'cancel',
    });
  };
  const complete = (run: HarnessRun, commandId = 'worker-a:complete') =>
    commit(run, 'worker-a', commandId, [workerEvent('message.completed', { role: 'assistant', textRef: inlineContentRef(Buffer.from('done')), isFinal: true })], {
      status: 'completed',
      outcome: 'answer',
    });

  describe('QA21-5 two people answering one approval (APR-13, APR-G5)', () => {
    for (const release of ordersOf('alice', 'bob')) {
      it(`one decision wins, the other is told it is no longer open, one wake (release ${String(release)})`, async () => {
        const run = await holding();
        const wakes = await wakesOf(run.runId);
        const race = new StoreRace(['alice', 'bob'], { release });
        const outcomes = await race.run({
          alice: () => decide(run, 'alice', 'approved'),
          bob: () => decide(run, 'bob', 'denied'),
        });
        expectForced(race, ['alice', 'bob'], outcomes);
        const won = winners(outcomes);
        expect(won, outcomes.map(codeOf).join()).to.have.length(1);
        if (Array.isArray(release)) expect(won[0]?.party).to.equal(release[0]);
        const lost = outcomes.find((outcome) => !outcome.ok)!;
        expect(codeOf(lost)).to.equal('INTERACTION_NOT_OPEN');
        expect(statusOf(lost)).to.equal(409);
        const resolved = (await stream(run)).filter((event) => event.type === 'interaction.resolved');
        expect(resolved).to.have.length(1);
        expect(resolved[0]?.data?.['by']).to.equal(`p_${won[0]!.party}`);
        expect(await wakes.drain()).to.have.length(1);
        await expectValidHistory(run);
      });
    }

    it('50 responses at once to a suspended run: one decision, 49 answered 409, one resume dispatched', async () => {
      const run = await holding('ix-a', true);
      const names = Array.from({ length: 50 }, (_, i) => `p${String(i)}`);
      const race = new StoreRace(names);
      const outcomes = await race.run(
        Object.fromEntries(
          names.map((name, i) => [name, () => decide(run, name, i % 2 === 0 ? 'approved' : 'denied')]),
        ),
      );
      expectForced(race, names, outcomes);
      expect(winners(outcomes), outcomes.map(codeOf).join()).to.have.length(1);
      const losers = outcomes.filter((outcome) => !outcome.ok);
      expect(new Set(losers.map(codeOf))).to.deep.equal(new Set(['INTERACTION_NOT_OPEN']));
      expect(new Set(losers.map(statusOf))).to.deep.equal(new Set([409]));
      expect(await count(run, 'interaction.resolved')).to.equal(1);
      expect(await count(run, 'run.resumed')).to.equal(1);
      const resumes = (await backend.dispatches(run.orgId, run.runId)).filter((d) => d.kind === 'run_resume');
      expect(resumes).to.have.length(1);
      await expectValidHistory(run);
    });
  });

  describe('QA21-3 Stop racing the run completing', () => {
    for (const release of ordersOf('user', 'worker')) {
      it(`one ending: completed, or stopped and then cancelled (release ${String(release)})`, async () => {
        const run = await claimed();
        const wakes = await wakesOf(run.runId);
        const race = new StoreRace(['user', 'worker'], { release });
        const outcomes = byParty(
          await race.run<unknown>({ user: () => stop(run), worker: () => complete(run) }),
        );
        expectForced(race, ['user', 'worker'], outcomes);
        const user = outcomes['user']!;
        const worker = outcomes['worker']!;
        expect([user.ok, worker.ok].filter(Boolean), `${codeOf(user)} / ${codeOf(worker)}`).to.have.length(1);
        if (worker.ok) {
          expect(codeOf(user)).to.equal('INVALID_TRANSITION');
          expect(statusOf(user)).to.equal(409);
          expect((await current(run)).status).to.equal('completed');
          expect(await wakes.drain(), 'nobody to wake once it ended').to.deep.equal([]);
        } else {
          expect(codeOf(worker)).to.equal('INVALID_TRANSITION');
          expect((await current(run)).status).to.equal('cancelling');
          expect(await wakes.drain(), 'the worker is woken once').to.have.length(1);
          await commit(run, 'worker-a', 'worker-a:cancelled', [], { status: 'cancelled' });
        }
        if (Array.isArray(release)) {
          expect(release[0] === 'worker' ? worker.ok : user.ok, 'the first released wins').to.equal(true);
        }
        const events = await types(run);
        expect(events.filter((type) => type === 'run.completed' || type === 'run.cancelled')).to.have.length(1);
        expect(await backend.transcripts!.written(run), 'one reply in the conversation').to.have.length(1);
        await expectValidHistory(run);
      });
    }
  });

  describe('QA21-6 an approval answered while the run is being stopped', () => {
    for (const release of ordersOf('approver', 'user')) {
      it(`a running run: the decision lands before the Stop, or is refused RUN_CANCELLING (release ${String(release)})`, async () => {
        const run = await holding();
        const race = new StoreRace(['approver', 'user'], { release });
        const outcomes = byParty(
          await race.run<unknown>({ approver: () => decide(run, 'approver'), user: () => stop(run) }),
        );
        expectForced(race, ['approver', 'user'], outcomes);
        const approver = outcomes['approver']!;
        expect(outcomes['user']!.ok, codeOf(outcomes['user']!)).to.equal(true);
        expect((await current(run)).status).to.equal('cancelling');
        if (approver.ok) {
          const tail = (await types(run)).slice(-2);
          expect(tail).to.deep.equal(['interaction.resolved', 'run.cancel_requested']);
        } else {
          expect(codeOf(approver)).to.equal('RUN_CANCELLING');
          expect(statusOf(approver)).to.equal(409);
          expect(await count(run, 'interaction.resolved')).to.equal(0);
        }
        if (Array.isArray(release)) expect(approver.ok).to.equal(release[0] === 'approver');
        await expectValidHistory(run);
      });

      it(`a suspended run: resumed by the decision then cancelled, or cancelled and the decision refused (release ${String(release)})`, async () => {
        const run = await holding('ix-a', true);
        const race = new StoreRace(['approver', 'user'], { release });
        const outcomes = byParty(
          await race.run<unknown>({ approver: () => decide(run, 'approver'), user: () => stop(run) }),
        );
        expectForced(race, ['approver', 'user'], outcomes);
        const approver = outcomes['approver']!;
        expect(outcomes['user']!.ok, codeOf(outcomes['user']!)).to.equal(true);
        expect((await current(run)).status).to.equal('cancelled');
        const resumes = (await backend.dispatches(run.orgId, run.runId)).filter((d) => d.kind === 'run_resume');
        if (approver.ok) {
          expect((await types(run)).slice(-4)).to.deep.equal([
            'interaction.resolved',
            'run.resumed',
            'run.cancel_requested',
            'run.cancelled',
          ]);
          expect(resumes).to.have.length(1);
        } else {
          expect(codeOf(approver)).to.equal('INTERACTION_NOT_OPEN');
          expect(await count(run, 'interaction.resolved')).to.equal(0);
          expect(resumes, 'a cancelled run is never resumed').to.deep.equal([]);
        }
        if (Array.isArray(release)) expect(approver.ok).to.equal(release[0] === 'approver');
        await expectValidHistory(run);
      });
    }
  });

  describe('QA21-7 workers claiming one run', () => {
    for (const release of ordersOf('w1', 'w2')) {
      it(`one claim, one fence, one pinned spec (release ${String(release)})`, async () => {
        const { run } = await h.admit(AGENT_RUN);
        const race = new StoreRace(['w1', 'w2'], { release });
        const outcomes = await race.run({ w1: () => claimAs(run, 'w1'), w2: () => claimAs(run, 'w2') });
        expectForced(race, ['w1', 'w2'], outcomes);
        const won = winners(outcomes);
        expect(won, outcomes.map(codeOf).join()).to.have.length(1);
        if (Array.isArray(release)) expect(won[0]?.party).to.equal(release[0]);
        const lost = outcomes.find((outcome) => !outcome.ok)!;
        expect(codeOf(lost)).to.equal('RUN_NOT_CLAIMABLE');
        expect(statusOf(lost)).to.equal(409);
        expect((await current(run)).workerId).to.equal(won[0]?.party);
        expect(await count(run, 'run.claimed')).to.equal(1);
        expect(await count(run, 'run.spec_pinned')).to.equal(1);
        await expectValidHistory(run);
      });
    }

    it('50 workers at once: one holds the run, the rest are refused, one claim journaled', async () => {
      const { run } = await h.admit(AGENT_RUN);
      const names = Array.from({ length: 50 }, (_, i) => `w${String(i)}`);
      const race = new StoreRace(names);
      const outcomes = await race.run(Object.fromEntries(names.map((name) => [name, () => claimAs(run, name)])));
      expectForced(race, names, outcomes);
      const won = winners(outcomes);
      expect(won, outcomes.map(codeOf).join()).to.have.length(1);
      expect(new Set(outcomes.filter((o) => !o.ok).map(codeOf))).to.deep.equal(new Set(['RUN_NOT_CLAIMABLE']));
      expect((await current(run)).workerId).to.equal(won[0]?.party);
      expect(await count(run, 'run.claimed')).to.equal(1);
      await expectValidHistory(run);
    });
  });
});
