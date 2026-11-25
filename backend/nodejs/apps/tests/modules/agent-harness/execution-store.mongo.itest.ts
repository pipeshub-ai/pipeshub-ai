import type { Tx } from '../../../src/libs/repositories/uow';
/**
 * The ExecutionStore contract and Mongo-only harness behaviour against a real
 * MongoDB replica set (transactions are required). Not part of `npm test`.
 *
 *   docker run -d --name harness-mongo --ulimit nofile=64000:64000 -p 27017:27017 mongo:8 --replSet rs0 --bind_ip_all
 *   docker exec harness-mongo mongosh --quiet --eval \
 *     'rs.initiate({_id:"rs0",members:[{_id:0,host:"localhost:27017"}]})'
 *   MONGO_IT_URI='mongodb://localhost:27017/?replicaSet=rs0&directConnection=true' \
 *     TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config \
 *     --require reflect-metadata --require ts-node/register --exit \
 *     tests/modules/agent-harness/execution-store.mongo.itest.ts
 *
 * Every `*.mongo.itest.ts` here runs the same way (pass the glob quoted).
 * AGENT_HARNESS_TEST_MONGO_URI is accepted as well (the Compose replica-set
 * script sets it).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { MongoDispatchOutbox } from '../../../src/libs/repositories/agent-harness/dispatch-outbox/mongo';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import { fixedMongoTime, mongoServerTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import { DispatchOutbox } from '../../../src/modules/agent-harness/ports/dispatch-outbox';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import { OutboxDispatcher } from '../../../src/libs/services/outbox/outbox.dispatcher';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { HarnessForbiddenError } from '../../../src/modules/agent-harness/domain/errors';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
  describeExecutionStoreContract,
} from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  harnessModels,
  mongoBackend,
  MongoBackendOptions,
  resetHarnessMongo,
} from './support/harness-mongo';
import { fixedRollout, HARNESS_ENABLED } from './support/rollout';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { currentWorker } from './support/worker-capabilities';
import { RUN_SPEC_SCHEMA_VERSION } from '../../../src/modules/agent-harness/domain/run-spec';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { CHILD_RESULT, describeChildRunContract } from './support/child-run-contract';
import { MongoRunAdmissionStore } from '../../../src/libs/repositories/agent-harness/run-admission-store/mongo';
import { MongoChildRunStore } from '../../../src/libs/repositories/agent-harness/child-runs/mongo';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { childRunIdFor } from '../../../src/modules/agent-harness/domain/child-runs';
import { testCipher } from './support/data-protection';
import { MongoServerError } from 'mongodb';
import { probeStore, StoreRace } from './support/store-race';
import { expectForced } from './support/race-assertions';

describe('agent harness on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeExecutionStoreContract('mongo replica set', (clock, options) => {
    const backend = mongoBackend({ clock, ...options });
    // The contract's RunAdmissionStore cases must reach the production class.
    expect(backend.admissionStore).to.be.instanceOf(MongoRunAdmissionStore);
    return backend;
  });

  describeChildRunContract('mongo replica set', (clock) =>
    mongoBackend({ clock }),
  );

  describe('JRN-G8: leases and receipts on the database clock, as production reads it', () => {
    let h: ContractHarness;

    beforeEach(async () => {
      await resetHarnessMongo();
      const clock = contractClock();
      h = createContractHarness(mongoBackend(), clock);
      // Admission stamps its deadline from the service clock; claims and renewals read $$NOW.
      h.setNow(await mongoServerTime.read());
    });

    it('a run document written before harness majors existed reads as major 1', async () => {
      const { run } = await h.admit();
      await AgentRunModel.updateOne({ orgId: run.orgId, runId: run.runId }, { $unset: { harnessMajor: 1 } });
      expect((await h.service.get(run.orgId, run.runId))?.harnessMajor).to.equal(1);
    });

    // Leases on DB time against a skewed Node are J-5 (db-time-leases.mongo.itest.ts); lastSeq is the contract's J-8.
    it('a claim leases no further than the run\'s absolute deadline', async () => {
      const { run } = await h.admit();
      const long = await h.claim(run, 'worker-a', 10 * 365 * 86_400_000);
      expect(long.leaseExpiresAt).to.equal(long.absoluteDeadlineAt);
    });

    it('a cancelling run keeps renewing its lease; a run past its deadline renews nothing', async () => {
      const { run } = await h.admit();
      const claimed = await h.claim(run, 'worker-a', 5_000);
      const cancelling = await h.service.control({
        commandId: 'cancel-1', dispatchCommandId: 'dispatch-cancel-1', orgId: run.orgId, runId: run.runId,
        expectedRevision: claimed.revision, action: 'cancel',
      });
      expect(cancelling.run.status).to.equal('cancelling');
      const renew = {
        orgId: run.orgId, runId: run.runId, workerId: 'worker-a', attemptId: claimed.attemptId as string,
        fence: claimed.fence, leaseDurationMs: 60_000,
      };
      const before = await mongoServerTime.read();
      const renewed = await h.service.renew(renew);
      expect(renewed.status).to.equal('cancelling');
      expect(renewed.leaseExpiresAt).to.be.at.least(Math.min(before + 60_000, renewed.absoluteDeadlineAt as number));

      await AgentRunModel.updateOne(
        { orgId: run.orgId, runId: run.runId },
        { $set: { absoluteDeadlineAt: before - 1 } },
      );
      let refused: unknown;
      try {
        await h.service.renew(renew);
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessConflictError);
    });

    it('of two identical commits racing on one snapshot, the one refused as a duplicate answers the stored receipt', async () => {
      const { run } = await h.admit();
      const claimed = await h.claim(run, 'worker-a', 60_000);
      const command = {
        commandId: 'commit-once', orgId: run.orgId, runId: run.runId, workerId: 'worker-a',
        attemptId: claimed.attemptId as string, fence: claimed.fence, expectedRevision: claimed.revision,
        status: 'completed' as const, outcome: 'answer' as const, outbox: [],
        events: [{ type: 'result', data: { n: 1 } }],
      };
      // Held on a snapshot without the first's receipt, the later transaction's first write may be answered with
      // a duplicate key instead of a write conflict (seen on this replica set when both go together, about one
      // run in three); this makes that answer certain.
      let refused = 0;
      const unprobe = probeStore((op, phase) => {
        if (op.party !== 'second' || !op.inTransaction || !op.write || phase !== 'before' || refused > 0) return;
        refused += 1;
        throw new MongoServerError({ message: 'E11000 duplicate key', code: 11000, codeName: 'DuplicateKey' });
      });
      try {
        const race = new StoreRace(['first', 'second'], { release: ['first', 'second'] });
        const outcomes = await race.run({
          first: () => h.service.commit(command),
          second: () => h.service.commit(command),
        });
        expectForced(race, ['first', 'second'], outcomes);
        const [first, second] = outcomes.map((outcome) => {
          if (!outcome.ok) throw outcome.error;
          return outcome.value;
        });
        expect(refused).to.equal(1);
        expect(second).to.deep.equal({ ...first, replayed: true });
      } finally {
        unprobe();
      }
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('completed');
    });
  });

  describe('CJ-8: a join suspension racing the last child ending never strands the parent', () => {
    beforeEach(resetHarnessMongo);

    it('either the parent is woken or its join is refused JOIN_READY, over many races', async function () {
      this.timeout(120_000);
      for (let round = 0; round < 12; round += 1) {
        const clock = contractClock();
        const backend = mongoBackend({ clock });
        const h = createContractHarness(backend, clock);
        h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
        h.setAgentDefinition((agentKey) =>
          agentDefinition({ agentKey, maxChildren: 1 }),
        );
        const { run } = await h.admit({
          agentKey: 'agent-1',
          authorization: 'Bearer user',
        });
        const parent = await h.claim(run, 'worker-p', 30_000);
        const spawned = await new ChildRunService({
          runs: backend.executionStore,
          children: backend.childRuns!,
          journals: new JournalService(backend.payloads, testCipher()),
        }).spawn(
          {
            orgId: parent.orgId,
            runId: parent.runId,
            workerId: 'worker-p',
            attemptId: parent.attemptId as string,
            fence: parent.fence,
          },
          { invocationId: 't1.0', goal: 'go', grants: {} },
        );
        const child = await h.claim(
          (await h.service.get(parent.orgId, spawned.childRunId))!,
          'worker-c',
          30_000,
        );
        const [join, end] = await Promise.allSettled([
          h.service.commit({
            commandId: `join-${String(round)}`,
            orgId: parent.orgId,
            runId: parent.runId,
            workerId: 'worker-p',
            attemptId: parent.attemptId as string,
            fence: parent.fence,
            events: [],
            legacyEvents: [],
            transition: {
              status: 'paused',
              detail: { kind: 'suspend', reason: 'join' },
            },
            outbox: [],
          }),
          h.service.commit({
            commandId: `end-${String(round)}`,
            orgId: child.orgId,
            runId: child.runId,
            workerId: 'worker-c',
            attemptId: child.attemptId as string,
            fence: child.fence,
            events: [],
            legacyEvents: [],
            transition: {
              status: 'completed',
              outcome: 'answer',
              childResult: CHILD_RESULT,
            },
            outbox: [],
          }),
        ]);
        expect(end.status, String((end as PromiseRejectedResult).reason)).to.equal('fulfilled');
        const after = (await h.service.get(parent.orgId, parent.runId))!;
        expect(after.children?.open).to.equal(0);
        if (join.status === 'fulfilled') {
          // The join landed first: the child's ending woke it.
          expect(after.status, `round ${String(round)}`).to.equal('queued');
        } else {
          expect((join.reason as HarnessConflictError).code).to.equal('JOIN_READY');
          expect(after.status).to.equal('running');
        }
      }
    });
  });

  describe('CA-7: child admission is one transaction', () => {
    beforeEach(resetHarnessMongo);

    it('a failure after the child insert leaves no child, no event, no wake and no counter', async () => {
      const clock = contractClock();
      const backend = mongoBackend({ clock });
      const h = createContractHarness(backend, clock);
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      h.setAgentDefinition((agentKey) =>
        agentDefinition({ agentKey, maxChildren: 2 }),
      );
      const { run } = await h.admit({
        agentKey: 'agent-1',
        authorization: 'Bearer user',
      });
      const parent = await h.claim(run, 'worker-p', 5_000);
      const children = backend.childRuns as MongoChildRunStore;
      const journal = (
        children as unknown as {
          journal: { append: (...args: unknown[]) => Promise<unknown> };
        }
      ).journal;
      const append = sinon.stub(journal, 'append');
      append.onFirstCall().callThrough();
      append.onSecondCall().rejects(new Error('injected after the child insert'));
      const service = new ChildRunService({
        runs: backend.executionStore,
        children,
        journals: new JournalService(backend.payloads, testCipher()),
      });
      try {
        await service.spawn(
          {
            orgId: parent.orgId,
            runId: parent.runId,
            workerId: 'worker-p',
            attemptId: parent.attemptId as string,
            fence: parent.fence,
          },
          { invocationId: 't1.0', goal: 'go', grants: {} },
        );
        expect.fail('the admission should have failed');
      } catch (error) {
        expect((error as Error).message).to.equal(
          'injected after the child insert',
        );
      } finally {
        append.restore();
      }
      const childRunId = childRunIdFor(parent.orgId, parent.runId, 't1.0');
      expect(await AgentRunModel.findOne({ runId: childRunId }).lean()).to.equal(
        null,
      );
      expect(
        await backend.journal.readStream(parent.orgId, childRunId, 0, 10),
      ).to.deep.equal([]);
      expect(
        (
          await backend.journal.readStream(parent.orgId, parent.sessionId, 0, 50)
        ).map((event) => event.type),
      ).to.not.include('child.spawned');
      expect(await backend.dispatches(parent.orgId, childRunId)).to.deep.equal(
        [],
      );
      expect(
        (await AgentRunModel.findOne({ runId: parent.runId }).lean())?.children,
      ).to.equal(undefined);
    });
  });

  const harness = (
    options: Omit<MongoBackendOptions, 'clock'> = {},
  ): ContractHarness => {
    const clock = contractClock();
    return createContractHarness(mongoBackend({ ...options, clock }), clock);
  };

  describe('RU-2g: a Node rolled back never serves a run a newer Node pinned', () => {
    beforeEach(resetHarnessMongo);

    it('neither claim-next nor a hint claims a run pinned with a newer spec schema', async () => {
      const h = harness();
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      const { run } = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      const worker = currentWorker();
      const claimed = await h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'w-1',
        attemptId: 'attempt-1',
        leaseDurationMs: 1_000,
        worker,
      });
      await h.service.release({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'w-1',
        attemptId: 'attempt-1',
        fence: claimed.run.fence,
        reason: 'drain',
      });
      // As the next release's Node would have pinned it.
      await AgentRunModel.updateOne(
        { runId: run.runId },
        { $set: { 'requires.schemaVersion': RUN_SPEC_SCHEMA_VERSION + 1 } },
      );
      const next = await h.service.claimNext({
        role: 'dispatched',
        workerId: 'w-2',
        attemptId: 'attempt-2',
        leaseDurationMs: 1_000,
        worker,
      });
      expect(next).to.equal(null);
      try {
        await h.service.claim({
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'w-2',
          attemptId: 'attempt-3',
          leaseDurationMs: 1_000,
          worker,
        });
        expect.fail('claimed a run this Node cannot read');
      } catch (error) {
        expect((error as HarnessConflictError).code).to.equal('WORKER_INCOMPATIBLE');
      }
      const stored = await AgentRunModel.findOne({ runId: run.runId }).lean();
      expect([stored?.status, stored?.fence]).to.deep.equal(['queued', claimed.run.fence]);
      expect(stored?.waitingForWorker).to.not.equal(null);
    });
  });

  describe('RU-2 (F2): the claim filter matches each run on the spec version it would hand out', () => {
    beforeEach(resetHarnessMongo);

    it('a run pinned at an older spec version goes only to a worker that reads that version', async () => {
      const h = harness();
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      const { run } = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      const older = RUN_SPEC_SCHEMA_VERSION - 1;
      // As the previous release's Node would have pinned it.
      await AgentRunModel.updateOne(
        { runId: run.runId },
        { $set: { 'requires.schemaVersion': older } },
      );
      const claimNext = (workerId: string, supportedSchemaVersions: number[]) =>
        h.service.claimNext({
          role: 'dispatched',
          workerId,
          attemptId: `attempt-${workerId}`,
          leaseDurationMs: 1_000,
          worker: currentWorker({ supportedSchemaVersions }),
        });
      expect(await claimNext('w-current', [RUN_SPEC_SCHEMA_VERSION])).to.equal(null);
      const taken = await claimNext('w-older-reader', [older, RUN_SPEC_SCHEMA_VERSION]);
      expect(taken?.run.runId).to.equal(run.runId);
    });
  });

  describe('shared outbox dispatch', () => {
    beforeEach(resetHarnessMongo);
    afterEach(() => sinon.restore());

    it('admission writes exactly one outbox event, keyed and ordered by run', async () => {
      const h = harness();
      const admitted = await h.admit();
      const rows = await OutboxEvent.find({}).lean();
      expect(rows).to.have.length(1);
      const [row] = rows;
      expect(row?.topic).to.equal('agent-run-events');
      expect(row?.key).to.equal(admitted.run.runId);
      expect(row?.orderingKey).to.equal(admitted.run.runId);
      expect(row?.status).to.equal('pending');
      expect(row?.headers).to.deep.equal({
        eventType: 'agent_run_dispatch_requested',
        schemaVersion: '1',
      });
      const envelope = JSON.parse(row?.value as string) as Record<
        string,
        unknown
      >;
      expect(envelope).to.have.keys(['eventType', 'timestamp', 'payload']);
      expect(envelope.eventType).to.equal('agent_run_dispatch_requested');
      const payload = envelope.payload as Record<string, unknown>;
      expect(payload.commandId).to.match(/^dispatch:[a-f0-9]{64}$/);
      expect(payload).to.deep.equal({
        schemaVersion: 1,
        commandId: payload.commandId,
        runId: admitted.run.runId,
        orgId: admitted.run.orgId,
        kind: 'run_ready',
        createdAt: admitted.run.createdAt,
      });
    });

    it('an aborted admission transaction leaves no outbox event or run', async () => {
      const real = new MongoDispatchOutbox();
      const failing: DispatchOutbox<Tx> = {
        enqueue: async (records, tx) => {
          await real.enqueue(records, tx);
          throw new Error('abort after the outbox write');
        },
      };
      const h = harness({ dispatches: failing });
      try {
        await h.admit();
        expect.fail('admission should have failed');
      } catch (error) {
        expect((error as Error).message).to.equal(
          'abort after the outbox write',
        );
      }
      expect(await OutboxEvent.countDocuments()).to.equal(0);
      expect(await AgentRunModel.countDocuments()).to.equal(0);
      expect(await ChatSession.countDocuments()).to.equal(0);
      expect(await ChatSessionMessage.countDocuments()).to.equal(0);
    });

    it('never dead-letters a failing publish; the run stays claimable after the broker recovers', async () => {
      const h = harness();
      const { run } = await h.admit();
      const logger = {
        info: sinon.stub(),
        debug: sinon.stub(),
        warn: sinon.stub(),
        error: sinon.stub(),
      };
      const producer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        publish: sinon.stub().rejects(new Error('broker down')),
      };
      const dispatcher = new OutboxDispatcher(
        producer as never,
        logger as never,
      );
      for (let attempt = 1; attempt <= 12; attempt += 1) {
        expect(await dispatcher.drain()).to.equal(0);
        const row = await OutboxEvent.findOne({
          orderingKey: run.runId,
        }).lean();
        expect(row?.status).to.equal('pending');
        expect(row?.attempts).to.equal(attempt);
        await OutboxEvent.updateMany(
          {},
          { $set: { nextAttemptAt: new Date(0) } },
        );
      }

      producer.publish.resolves();
      expect(await dispatcher.drain()).to.equal(1);
      const [topic, message] = producer.publish.lastCall.args as [
        string,
        { key: string; value: string },
      ];
      expect(topic).to.equal('agent-run-events');
      expect(message.key).to.equal(run.runId);
      expect(
        (await OutboxEvent.findOne({ orderingKey: run.runId }).lean())?.status,
      ).to.equal('published');

      const claimed = await h.claim(run);
      expect(claimed.status).to.equal('running');
    });

    it('control and worker commits dispatch through the same outbox', async () => {
      const h = harness();
      const { run } = await h.admit();
      const claimed = await h.claim(run);
      await h.service.control({
        commandId: 'cancel-1',
        dispatchCommandId: 'cancel-dispatch-1',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: claimed.revision,
        action: 'cancel',
      });
      expect(
        (await h.backend.dispatches(run.orgId, run.runId)).map(
          (item) => item.kind,
        ),
      ).to.deep.equal(['run_ready', 'run_cancel']);
      expect(
        await OutboxEvent.countDocuments({ orderingKey: run.runId }),
      ).to.equal(2);
    });
  });

  describe('agent authorization at admission', () => {
    beforeEach(resetHarnessMongo);

    it('rejects an agent the actor cannot use before writing anything', async () => {
      const backend = mongoBackend({ clock: contractClock() });
      const service = new RunAdmissionService({
        store: backend.admissionStore,
        rollout: fixedRollout(),
        agentAccess: { authorizeRun: async () => null },
      });
      try {
        await service.submit({
          orgId: new Types.ObjectId().toString(),
          actorId: new Types.ObjectId().toString(),
          agentKey: 'not-shared-with-me',
          authorization: 'Bearer user',
          idempotencyKey: 'denied',
          deadlineMs: 60_000,
          input: {
            query: 'q',
            recordIds: [],
            filters: {},
            attachments: [],
            model: { chatMode: 'auto' },
          },
        });
        expect.fail('admission should have been forbidden');
      } catch (error) {
        expect(error).to.be.instanceOf(HarnessForbiddenError);
      }
      for (const model of harnessModels()) {
        expect(await model.countDocuments()).to.equal(0);
      }
    });
    it('keeps the granted definition on the run document, never on the run', async () => {
      const backend = mongoBackend({ clock: contractClock() });
      const definition = agentDefinition();
      const outcome = await new RunAdmissionService({
        store: backend.admissionStore,
        rollout: fixedRollout(),
        agentAccess: grantingAccess(() => definition),
      }).submit({
        orgId: new Types.ObjectId().toString(),
        actorId: new Types.ObjectId().toString(),
        agentKey: 'agent-1',
        authorization: 'Bearer user',
        idempotencyKey: 'granted',
        deadlineMs: 60_000,
        input: {
          query: 'q',
          recordIds: [],
          filters: {},
          attachments: [],
          model: { chatMode: 'auto' },
        },
      });
      if (outcome.route !== 'durable') expect.fail('expected a durable run');
      const { run } = outcome.admission;
      const document = await AgentRunModel.findOne({ runId: run.runId }).lean();
      expect(document?.agentDefinition).to.deep.equal(definition);
      expect(run).not.to.have.property('agentDefinition');
      expect(
        await backend.executionStore.get(run.orgId, run.runId),
      ).not.to.have.property('agentDefinition');
    });
  });

  describe('sweeper projections and leadership', () => {
    beforeEach(resetHarnessMongo);

    it('marks the conversation failed when an idle run passes its deadline', async () => {
      const h = harness();
      const admitted = await h.admit();
      h.setNow(admitted.run.absoluteDeadlineAt);
      expect(await h.service.sweep(10)).to.equal(1);
      const session = await ChatSession.findById(admitted.sessionId).lean();
      expect(session?.status).to.equal('Failed');
    });

    it('WAITCHILD: a run whose deadline a child’s wait moved after the sweep read it is not failed', async () => {
      const h = harness();
      const admitted = await h.admit();
      const { orgId, runId, absoluteDeadlineAt } = admitted.run;
      h.setNow(absoluteDeadlineAt);
      let moved = false;
      // Between the sweep's read and its CAS, as a child's hold writes it: the deadline moves, the revision does not.
      const unprobe = probeStore(async (op, phase) => {
        if (moved || !op.inTransaction || phase !== 'before') return;
        moved = true;
        await AgentRunModel.updateOne({ orgId, runId }, { $set: { absoluteDeadlineAt: absoluteDeadlineAt + 60_000 } });
      });
      try {
        expect(await h.service.sweep(10)).to.equal(0);
      } finally {
        unprobe();
      }
      expect(moved).to.equal(true);
      expect((await h.service.get(orgId, runId))?.status).to.equal('queued');
    });

    it('elects one sweeper leader until its lease lapses', async () => {
      let now = 1_000;
      const lease = new MongoLeaderLease(fixedMongoTime(() => now));
      const at = (value: number) => {
        now = value;
        return lease;
      };
      expect(await at(1_000).tryAcquire('sweeper', 'a', 100)).to.equal(true);
      expect(await at(1_050).tryAcquire('sweeper', 'b', 100)).to.equal(false);
      expect(await at(1_050).tryAcquire('sweeper', 'a', 100)).to.equal(true);
      expect(await at(1_149).tryAcquire('sweeper', 'b', 100)).to.equal(false);
      expect(await at(1_150).tryAcquire('sweeper', 'b', 100)).to.equal(true);
      await lease.release('sweeper', 'a');
      expect(await at(1_160).tryAcquire('sweeper', 'a', 100)).to.equal(false);
      await lease.release('sweeper', 'b');
      expect(await at(1_160).tryAcquire('sweeper', 'a', 100)).to.equal(true);
    });
  });
});
