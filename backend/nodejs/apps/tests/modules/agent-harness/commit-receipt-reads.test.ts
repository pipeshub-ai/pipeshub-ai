/**
 * PERF-D (PERFC-F1): a commit's appended events are read from the store once.
 * The receipt the worker is answered, the read-ahead for the commit that ends
 * the run and the run's journal reader share that read.
 */
import { expect } from 'chai';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEventInput } from '../../../src/modules/agent-harness/domain/journal';
import { ExecutionStore } from '../../../src/modules/agent-harness/ports/execution-store';
import { contractClock, createContractHarness, DURABLE_CAPABILITIES } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { testCipher } from './support/data-protection';
import { turnStarted } from './support/journal-fixtures';
import { HARNESS_ENABLED } from './support/rollout';

const RUN_ID = 'run-receipt-reads';
const WORKER = { workerId: 'worker-a', attemptId: 'attempt-1' };

async function world() {
  const clock = contractClock();
  const backend = inMemoryBackend(clock);
  const h = createContractHarness(backend, clock);
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const reads: string[] = [];
  const journals = new JournalService(backend.payloads, testCipher());
  /** Reads of the run outside a transaction, as the service asks the store for them. */
  let runReads = 0;
  /** Replay-check reads of the receipt, by the service or a caller through it. */
  let receiptReads = 0;
  const store = new Proxy(backend.executionStore, {
    get: (target, property, receiver) => {
      if (property === 'get') {
        return (...args: Parameters<ExecutionStore['get']>) => {
          runReads += 1;
          return target.get(...args);
        };
      }
      if (property === 'findReceipt') {
        return (...args: Parameters<ExecutionStore['findReceipt']>) => {
          receiptReads += 1;
          return target.findReceipt(...args);
        };
      }
      return Reflect.get(target, property, receiver) as unknown;
    },
  }) as ExecutionStore;
  const service = new ExecutionService({
    store,
    journal: {
      readStream: (...args: Parameters<typeof backend.journal.readStream>) => {
        reads.push('stream');
        return backend.journal.readStream(...args);
      },
      readRun: (...args: Parameters<typeof backend.journal.readRun>) => {
        reads.push('run');
        return backend.journal.readRun(...args);
      },
    },
    journals,
    capabilities: DURABLE_CAPABILITIES,
  });
  const admitted = await h.admit(
    { input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } },
    RUN_ID,
  );
  const { run } = await service.claim({ orgId: admitted.run.orgId, runId: RUN_ID, ...WORKER, leaseDurationMs: 60_000 });
  const commit = (commandId: string, events: JournalEventInput[], ending = false, read?: HarnessRun,
    prior?: ReturnType<ExecutionStore['findReceipt']>) =>
    service.commit(
      {
        commandId,
        orgId: run.orgId,
        runId: RUN_ID,
        ...WORKER,
        fence: run.fence,
        events,
        legacyEvents: [],
        outbox: [],
        ...(ending ? { transition: { status: 'completed' as const, outcome: 'answer' as const } } : {}),
      },
      read,
      prior,
    );
  /** The receipt's events as the store holds them, read around the service. */
  const stored = async (firstSeq: number, lastSeq: number) =>
    journals.resolve(await backend.journal.readStream(run.orgId, admitted.run.sessionId, firstSeq - 1, lastSeq - firstSeq + 1));
  return { service, run, reads, commit, stored, store: backend.executionStore, runReads: () => runReads,
    receiptReads: () => receiptReads };
}

describe('PERF-D: a commit reads the events it appended once', () => {
  it('PERFD-8: the receipt answered to the worker and the read-ahead are one store read', async () => {
    const w = await world();
    await w.service.receiptEvents(w.run.orgId, await w.commit('c1', [turnStarted(1)]));
    w.reads.length = 0;

    const receipt = await w.commit('c2', [turnStarted(2), turnStarted(3)]);
    const events = await w.service.receiptEvents(w.run.orgId, receipt);

    expect(w.reads).to.deep.equal(['run']);
    expect(events.map((event) => event.seq)).to.deep.equal([receipt.journal!.firstSeq, receipt.journal!.lastSeq]);
    expect(events).to.deep.equal(await w.stored(receipt.journal!.firstSeq, receipt.journal!.lastSeq));
  });

  it('PERFD-8: a receipt asked for again is answered with its own events and nothing later', async () => {
    const w = await world();
    const first = await w.commit('c1', [turnStarted(1)]);
    const wanted = await w.service.receiptEvents(w.run.orgId, first);
    await w.service.receiptEvents(w.run.orgId, await w.commit('c2', [turnStarted(2)]));

    const again = await w.service.receiptEvents(w.run.orgId, { ...first, replayed: true });

    expect(again).to.deep.equal(wanted);
    expect(again).to.deep.equal(await w.stored(first.journal!.firstSeq, first.journal!.lastSeq));
  });

  it('PERFD-8: a commit that appended nothing reads nothing', async () => {
    const w = await world();
    w.reads.length = 0;
    const receipt = await w.commit('c1', []);
    expect(receipt.journal ?? null).to.equal(null);
    expect(await w.service.receiptEvents(w.run.orgId, receipt)).to.deep.equal([]);
    expect(w.reads).to.deep.equal([]);
  });
});

describe('PERF-D: a commit does not read the run its attempt check just read', () => {
  it('PERFD-13: given the run, the commit reads it only in its transaction, and commits the same', async () => {
    const w = await world();
    const without = await w.commit('c1', [turnStarted(1)]);
    const alone = w.runReads();
    expect(alone, 'the commit reads the run itself when it is given none').to.be.greaterThan(0);

    const read = (await w.store.get(w.run.orgId, RUN_ID))!;
    const given = await w.commit('c2', [turnStarted(2)], false, read);

    expect(w.runReads(), 'no read outside the transaction').to.equal(alone);
    expect(given.run.revision).to.equal(without.run.revision + 1);
    expect(given.journal!.firstSeq).to.equal(without.journal!.lastSeq + 1);
    expect((await w.store.get(w.run.orgId, RUN_ID))!.revision).to.equal(given.run.revision);
  });

  it('PERFD-13: a run that moved after it was read is refused by the store, as a commit that read it itself is', async () => {
    const w = await world();
    const read = (await w.store.get(w.run.orgId, RUN_ID))!;
    await w.commit('c1', [turnStarted(1)]);
    await w.commit('end', [], true);

    const refused = await w.commit('c2', [turnStarted(2)], false, read).then(
      () => null,
      (error: unknown) => error,
    );
    const alone = await w.commit('c3', [turnStarted(2)]).then(
      () => null,
      (error: unknown) => error,
    );

    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect(alone).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal((alone as HarnessConflictError).code);
  });

  it('PERFD-13: a retried commit replays its receipt whatever run it is given', async () => {
    const w = await world();
    const read = (await w.store.get(w.run.orgId, RUN_ID))!;
    const first = await w.commit('c1', [turnStarted(1)]);
    const again = await w.commit('c1', [turnStarted(1)], false, read);
    expect(again.replayed).to.equal(true);
    expect(again.journal).to.deep.equal(first.journal);
  });
});

describe('WP-88: a commit\'s replay check can be read alongside its attempt check', () => {
  it('WP88-PF05-3: given the replay check\'s read, the commit does not read the receipt again, and commits the same', async () => {
    const w = await world();
    await w.commit('c1', [turnStarted(1)]);
    const before = w.receiptReads();
    const given = await w.commit('c2', [turnStarted(2)], false, undefined, w.service.priorCommit(w.run.orgId, RUN_ID, 'c2'));
    expect(w.receiptReads(), 'only the read the caller started').to.equal(before + 1);
    expect(given.replayed ?? false).to.equal(false);
  });

  it('WP88-PF05-3: a retried commit replays from the read it was given', async () => {
    const w = await world();
    const first = await w.commit('c1', [turnStarted(1)]);
    const again = await w.commit('c1', [turnStarted(1)], false, undefined, w.service.priorCommit(w.run.orgId, RUN_ID, 'c1'));
    expect(again.replayed).to.equal(true);
    expect(again.journal).to.deep.equal(first.journal);
  });
});
