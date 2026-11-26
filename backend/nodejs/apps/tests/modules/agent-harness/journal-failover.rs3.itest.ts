/**
 * J-7 (nightly): commits acknowledged with w:majority survive a hard kill of
 * the primary. Needs a 3-member replica set whose members run in one
 * container; `deployment/docker-compose/tests/agent_harness_journal_failover_test.sh`
 * starts it and sets MONGO_RS3_IT_URI / MONGO_RS3_CONTAINER.
 */
import 'reflect-metadata';
import { migrateHarnessIndexes } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import { execFileSync } from 'node:child_process';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { probeJournalDurability } from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import { CommitReceipt } from '../../../src/modules/agent-harness/domain/execution';
import { harnessModels, mongoBackend } from './support/harness-mongo';
import { turnStarted } from './support/journal-fixtures';
import { fixedRollout, HARNESS_ENABLED } from './support/rollout';
import { testCipher } from './support/data-protection';
import { DURABLE_CAPABILITIES } from './support/execution-store-contract';

const uri = process.env.MONGO_RS3_IT_URI;
const container = process.env.MONGO_RS3_CONTAINER;
const BATCHES = 30;
const KILL_AFTER = 10;
const RETRY_FOR_MS = 90_000;

const sleep = (ms: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/** What a worker does with a transient failure: retry the same command. */
async function untilAcknowledged<T>(work: () => Promise<T>): Promise<T> {
  const deadline = Date.now() + RETRY_FOR_MS;
  for (;;) {
    try {
      return await work();
    } catch (error) {
      if (Date.now() > deadline) throw error;
      await sleep(250);
    }
  }
}

const primaryOf = async (): Promise<string> =>
  String(
    (await mongoose.connection.db!.admin().command({ hello: 1 }))['primary'] ??
      '',
  );

(uri && container ? describe : describe.skip)(
  'J-7 journal commits survive primary failover (3-member replica set)',
  function () {
    this.timeout(300_000);

    before(async () => {
      await mongoose.connect(uri as string, {
        dbName: `agent_harness_failover_${Date.now()}`,
        serverSelectionTimeoutMS: 30_000,
      });
      await Promise.all(
        (harnessModels() as Array<Model<never>>).map((model) => model.init()),
      );
      await migrateHarnessIndexes(mongoose.connection.db!);
    });
    after(async () => {
      await mongoose.disconnect();
    });

    it('loses no acknowledged commit and applies each exactly once', async () => {
      expect(await probeJournalDurability()).to.deep.equal({
        durable: true,
        reason: 'majority',
      });
      const backend = mongoBackend();
      const journal = backend.journal;
      const executions = new ExecutionService({
        store: backend.executionStore,
        inputs: backend.inputs ?? null,
        journal,
        journals: new JournalService(backend.payloads, testCipher()),
        capabilities: DURABLE_CAPABILITIES,
      });
      const outcome = await new RunAdmissionService({
        store: backend.admissionStore,
        rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
      }).submit({
        orgId: new mongoose.Types.ObjectId().toString(),
        actorId: new mongoose.Types.ObjectId().toString(),
        idempotencyKey: 'failover',
        deadlineMs: 60 * 60_000,
        input: {
          query: 'q',
          recordIds: [],
          filters: {},
          attachments: [],
          model: { chatMode: 'auto' },
        },
      });
      if (outcome.route !== 'durable') expect.fail('expected durable');
      const { run } = await executions.claim({
        orgId: outcome.admission.run.orgId,
        runId: outcome.admission.run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-a',
        leaseDurationMs: 5 * 60_000,
      });

      const before = await primaryOf();
      const acknowledged: CommitReceipt[] = [];
      let killed = false;
      for (let turn = 1; turn <= BATCHES; turn += 1) {
        const receipt = await untilAcknowledged(() =>
          executions.commit({
            commandId: `attempt-a:turn:${String(turn)}`,
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'worker-a',
            attemptId: 'attempt-a',
            fence: run.fence,
            events: [turnStarted(turn), turnStarted(turn)],
            legacyEvents: [],
            outbox: [],
          }),
        );
        acknowledged.push(receipt);
        if (turn === KILL_AFTER) {
          const port = before.split(':').pop() as string;
          // SIGKILL, no clean shutdown: whatever was only on the primary is gone.
          execFileSync('docker', [
            'exec',
            container as string,
            'bash',
            '-c',
            `kill -9 $(cat /data/rs${port}/mongod.lock)`,
          ]);
          killed = true;
        }
      }
      expect(killed).to.equal(true);
      const after = await untilAcknowledged(async () => {
        const primary = await primaryOf();
        if (!primary || primary === before) throw new Error('no new primary');
        return primary;
      });
      expect(after).to.not.equal(before);

      const events = await untilAcknowledged(() =>
        journal.readStream(run.orgId, run.sessionId, 0, 10_000),
      );
      // Gap-free, and the worker's turns next to the Node-written run events (P-11).
      expect(events.map((event) => event.seq)).to.deep.equal(
        Array.from({ length: events.length }, (_, index) => index + 1),
      );
      expect(
        events.filter((event) => event.type === 'turn.started'),
      ).to.have.length(BATCHES * 2);
      for (const receipt of acknowledged) {
        const range = receipt.journal!;
        const turn = Number(receipt.commandId.split(':').pop());
        const stored = events.slice(range.firstSeq - 1, range.lastSeq);
        expect(
          stored.map((event) => (event.data as { turn: number }).turn),
        ).to.deep.equal([turn, turn]);
      }
    });
  },
);
