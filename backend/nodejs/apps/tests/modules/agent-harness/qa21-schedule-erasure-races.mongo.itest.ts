/**
 * QA-21: schedule firings that overlap, and erasure racing an active
 * session, on a real MongoDB replica set (Redis for the live bus when
 * REDIS_IT_URL is set). Forced at the store seam (support/store-race.ts);
 * erasure is judged by the QA-15 census. Run instructions in
 * execution-store.mongo.itest.ts.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
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
import { aSchedule, FakeActors, FakeSetting, TestClock } from './support/schedules';
import { enqueue, world } from './support/session-erasure-world';
import { FolderSandboxBroker } from './support/folder-sandbox-broker';
import { composedWorker, drain, expectErasedByCensus, Rig, seeded } from './support/erasure-census-rig';
import { byParty, codeOf, expectForced, expectValidHistory, ordersOf, statusOf } from './support/race-assertions';
import { Gate, StoreRace } from './support/store-race';
import { MongoScheduleStore } from '../../../src/libs/repositories/agent-harness/schedules/mongo';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import {
  AgentScheduleCounterModel,
  AgentScheduleFiringModel,
  AgentScheduleModel,
} from '../../../src/libs/repositories/agent-harness/schedules/schema/schedules.schema';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { RunAdmissionScheduledRuns } from '../../../src/modules/agent-harness/application/scheduled-run-admission';
import { FIRING_LEASE_MS } from '../../../src/modules/agent-harness/domain/schedules';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { RunAdmissionResult } from '../../../src/modules/agent-harness/domain/admission';

registerHarnessModels([
  AgentScheduleModel,
  AgentScheduleFiringModel,
  AgentScheduleCounterModel,
] as unknown as Array<Model<never>>);

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const LIMITS = { perActor: 50, perOrg: 500 };

describe('QA-21 overlapping schedule firings and erasure races (MongoDB replica set)', function () {
  this.timeout(180_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describe('QA21-14 schedule firings overlapping (P8a ST-9 on the real admission)', () => {
    let h: ContractHarness;
    let backend: ContractBackend;
    let clock: TestClock;

    beforeEach(async () => {
      await resetHarnessMongo();
      const runClock = contractClock();
      backend = mongoBackend({ clock: runClock });
      h = createContractHarness(backend, runClock);
      h.setRollout(V2);
      clock = new TestClock();
    });

    const firer = () =>
      new ScheduleFirer({
        store: new MongoScheduleStore(fixedMongoTime(() => clock.now())),
        admission: new RunAdmissionScheduledRuns({ submit: (request) => h.submit(request) }),
        actors: new FakeActors(),
        setting: new FakeSetting(),
      });
    const dueSchedule = async () => {
      const store = new MongoScheduleStore(fixedMongoTime(() => clock.now()));
      const schedule = await store.create(
        aSchedule({ orgId: h.tenant.orgId, actorId: h.tenant.actorId, agentKey: 'agent-1' }),
        LIMITS,
      );
      clock.value = (schedule.nextFireAt as number) + 30_000;
      return { store, schedule };
    };
    const firingsOf = (store: MongoScheduleStore, scheduleId: string) =>
      store.firings(h.tenant.orgId, scheduleId, 10);

    for (const release of ordersOf('replica-a', 'replica-b')) {
      it(`two replicas tick over one due slot: one firing, one run (release ${String(release)})`, async () => {
        const { store, schedule } = await dueSchedule();
        const [a, b] = [firer(), firer()];
        // Each replica holds after it read the due schedules: the later one decides on a stale list.
        const race = new StoreRace(['replica-a', 'replica-b'], { release, gate: (op) => op.collection === 'agentSchedules' && !op.write });
        const outcomes = await race.run({ 'replica-a': () => a.tick(), 'replica-b': () => b.tick() });
        expectForced(race, ['replica-a', 'replica-b'], outcomes);
        const firings = await firingsOf(store, schedule.id);
        expect(firings.map((firing) => firing.status)).to.deep.equal(['admitted']);
        const runs = await AgentRunModel.find({ orgId: h.tenant.orgId }).lean();
        expect(runs.map((run) => run.runId)).to.deep.equal([firings[0]?.runId]);
        await expectValidHistory(backend, runs[0] as unknown as HarnessRun);
      });
    }

    for (const release of ordersOf('original', 'retake')) {
      it(`a stale retake racing the original firing: one run, the firing settled once with it (release ${String(release)})`, async () => {
        const { store, schedule } = await dueSchedule();
        const [a, b] = [firer(), firer()];
        // Both are held in their admission; the retake starts once the original holds the slot past its lease.
        const gate: Gate = (op) => op.collection === 'agentAdmissionReceipts' && op.inTransaction;
        const race = new StoreRace(['original', 'retake'], { release, gate });
        const outcomes = await race.run({
          original: () => a.tick(),
          retake: async () => {
            await race.reached('original');
            clock.advance(FIRING_LEASE_MS + 1);
            await b.tick();
          },
        });
        expectForced(race, ['original', 'retake'], outcomes);
        const firings = await firingsOf(store, schedule.id);
        expect(firings.map((firing) => [firing.status, firing.attempts])).to.deep.equal([['admitted', 2]]);
        const runs = await AgentRunModel.find({ orgId: h.tenant.orgId }).lean();
        expect(runs.map((run) => run.runId), 'the retake replays the slot’s admission').to.deep.equal([firings[0]?.runId]);
        await expectValidHistory(backend, runs[0] as unknown as HarnessRun);
      });
    }
  });

  describe('QA21-15 erasure racing an active session (IX-ERA-6)', () => {
    let rig: Rig;

    beforeEach(async () => {
      await resetHarnessMongo();
      for (const name of ['conversations', 'agentconversations', 'citations']) {
        await mongoose.connection.collection(name).deleteMany({});
      }
      const broker = new FolderSandboxBroker();
      rig = { w: world(), broker, endpoint: await broker.start() };
    });
    afterEach(async () => {
      await rig.broker.stop();
    });

    for (const release of ordersOf('user', 'delete')) {
      it(`a turn sent while the conversation is deleted: admitted then erased, or refused; nothing remains (release ${String(release)})`, async () => {
        const s = await seeded(rig, false);
        const gate: Gate = (op) => op.inTransaction || (op.collection === 'chatSessions' && op.write);
        const race = new StoreRace(['user', 'delete'], { release, gate });
        const outcomes = byParty(
          await race.run<unknown>({
            user: () =>
              rig.w.harness.admit({
                sessionId: s.sessionId,
                input: { query: s.canary, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
              }),
            delete: () => enqueue(rig.w, s),
          }),
        );
        expectForced(race, ['user', 'delete'], outcomes);
        expect(outcomes['delete']!.ok, codeOf(outcomes['delete']!)).to.equal(true);
        const user = outcomes['user']!;
        if (Array.isArray(release)) expect(user.ok).to.equal(release[0] === 'user');
        const runIds = [...s.runIds];
        if (user.ok) {
          runIds.push((user.value as RunAdmissionResult).run.runId);
        } else {
          expect(statusOf(user), codeOf(user)).to.equal(404);
          expect(await AgentRunModel.countDocuments({ sessionId: s.sessionId })).to.equal(s.runIds.length);
        }
        await expectValidHistory(rig.w.harness.backend, s, runIds.slice(s.runIds.length));
        expect(await drain(rig, composedWorker(rig), s)).to.equal('completed');
        await expectErasedByCensus(rig, { ...s, runIds });
      });
    }

    for (const release of ordersOf('worker', 'erasure')) {
      it(`the worker finishing a run of a deleted conversation as erasure stops it: refused, stopped once, nothing remains (release ${String(release)})`, async () => {
        const s = await seeded(rig, true);
        await enqueue(rig.w, s);
        const live = (await rig.w.harness.service.get(s.orgId, s.liveRunId!))!;
        const worker = composedWorker(rig);
        const race = new StoreRace(['worker', 'erasure'], { release });
        const outcomes = byParty(
          await race.run<unknown>({
            worker: () =>
              rig.w.harness.service.commit({
                commandId: 'complete-live',
                orgId: live.orgId,
                runId: live.runId,
                workerId: live.workerId as string,
                attemptId: live.attemptId as string,
                fence: live.fence,
                events: [],
                legacyEvents: [],
                transition: { status: 'completed', outcome: 'answer' },
                outbox: [],
              }),
            erasure: () => worker.tick(),
          }),
        );
        expectForced(race, ['worker', 'erasure'], outcomes);
        // Its conversation is gone: whether its answer finds no session or erasure stopped the run first, the
        // worker is told the cause (QA21-F4, ERR-UX EU-22).
        const refused = outcomes['worker']!;
        expect(refused.ok).to.equal(false);
        expect([statusOf(refused), codeOf(refused)]).to.deep.equal([410, 'CONVERSATION_DELETED']);
        expect(outcomes['erasure']!.ok).to.equal(true);
        expect((await rig.w.harness.service.get(s.orgId, live.runId))?.status).to.equal('cancelling');
        await expectValidHistory(rig.w.harness.backend, s, [live.runId]);
        expect(await drain(rig, worker, s)).to.equal('completed');
        await expectErasedByCensus(rig, s);
      });
    }
  });
});
