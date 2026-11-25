/** PERF-B (QA22-F2): the claim gives the worker the history its run document only names. */
import { expect } from 'chai';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunHistoryContent } from '../../../src/modules/agent-harness/application/run-history-content';
import {
  HarnessConflictError,
  HarnessUnavailableError,
  JournalPayloadMissingError,
  PayloadStoreUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { RunHistoryRef } from '../../../src/modules/agent-harness/domain/run-history';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { ExecutionStore } from '../../../src/modules/agent-harness/ports/execution-store';
import { PayloadStore } from '../../../src/modules/agent-harness/ports/payload-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import {
  contractClock,
  createContractHarness,
  DURABLE_CAPABILITIES,
} from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { testCipher } from './support/data-protection';
import { HARNESS_ENABLED } from './support/rollout';

const TURNS = [
  { role: 'user_query', content: 'first question' },
  { role: 'bot_response', content: 'first answer' },
];

const rejection = async (work: Promise<unknown>): Promise<unknown> =>
  work.then(
    () => expect.fail('expected a rejection'),
    (error: unknown) => error,
  );

/** Which read of the history a part's read belongs to: every part is read once a pass. */
const passes = (): ((ref: string) => number) => {
  const seen = new Map<string, number>();
  return (ref) => {
    const nth = (seen.get(ref) ?? 0) + 1;
    seen.set(ref, nth);
    return nth;
  };
};

describe('PERF-B: a claim reads its run history through the reference', () => {
  const world = async (options: { reader?: 'none'; blobs?: (stored: PayloadStore) => PayloadStore } = {}) => {
    const clock = contractClock();
    const backend = inMemoryBackend(clock);
    const h = createContractHarness(backend, clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const writer = new RunHistoryContent(testCipher(), backend.payloads, null, 16);
    const refs = new Map<string, RunHistoryRef>();
    /** The run document's `history` field: what admission on MongoDB writes beside the run. */
    const store = new Proxy(backend.executionStore, {
      get: (target, property, receiver) =>
        property === 'readClaimSubject'
          ? async (orgId: string, runId: string) => {
              const subject = await target.readClaimSubject(orgId, runId);
              return subject === null ? null : { ...subject, history: refs.get(runId) ?? null };
            }
          : (Reflect.get(target, property, receiver) as unknown),
    }) as ExecutionStore;
    const logs: Array<[level: string, message: string, meta: Record<string, unknown>]> = [];
    const record =
      (level: string) =>
      (message: string, meta?: unknown): void => {
        logs.push([level, message, (meta ?? {}) as Record<string, unknown>]);
      };
    const logger: HarnessLogger = { debug: record('debug'), info: record('info'), warn: record('warn'), error: record('error') };
    const service = new ExecutionService({
      store,
      journal: backend.journal,
      journals: new JournalService(backend.payloads, testCipher()),
      history:
        options.reader === 'none'
          ? null
          : new RunHistoryContent(testCipher(), options.blobs?.(backend.payloads) ?? backend.payloads),
      capabilities: DURABLE_CAPABILITIES,
      logger,
    });
    const admit = async (history: 'stored' | 'none' = 'stored'): Promise<HarnessRun> => {
      const { run } = await h.admit();
      if (history === 'stored') refs.set(run.runId, (await writer.store(run, TURNS))!);
      return run;
    };
    const claim = (run: HarnessRun) =>
      service.claim({ orgId: run.orgId, runId: run.runId, workerId: 'worker-a', attemptId: 'attempt-1', leaseDurationMs: 1_000 });
    const claimNext = () =>
      service.claimNext({ role: 'dispatched', workerId: 'worker-a', attemptId: 'attempt-1', leaseDurationMs: 1_000 });
    return { h, backend, service, refs, admit, claim, claimNext, logs };
  };

  it('PERFB-2: the claimed run carries the stored history; the run the store keeps does not', async () => {
    const w = await world();
    const run = await w.admit();

    const claimed = await w.claim(run);

    expect(claimed.run.input?.previousConversations).to.deep.equal(TURNS);
    expect(claimed.run.input?.query).to.equal(run.input?.query);
    expect(claimed.run).to.include({ status: 'running', attemptId: 'attempt-1' });
    const kept = await w.service.get(run.orgId, run.runId);
    expect(kept?.input?.previousConversations).to.deep.equal([]);
    const renewed = await w.service.renew({
      orgId: run.orgId, runId: run.runId, workerId: 'worker-a', attemptId: 'attempt-1', fence: claimed.run.fence, leaseDurationMs: 1_000,
    });
    expect(renewed.input?.previousConversations, 'a renew answer').to.deep.equal([]);
  });

  it('PERFB-2: the next claimable run is claimed with its history too', async () => {
    const w = await world();
    await w.admit();
    const claimed = await w.claimNext();
    expect(claimed?.run.input?.previousConversations).to.deep.equal(TURNS);
  });

  it('PERFB-3: a run whose document names no history is claimed with what its input holds', async () => {
    const w = await world();
    const run = await w.admit('none');
    const claimed = await w.claim(run);
    expect(claimed.run.input?.previousConversations).to.deep.equal([]);
  });

  it('PERFB-8: a history that is gone fails the run for good and the claim says so', async () => {
    const w = await world();
    const run = await w.admit();
    (w.backend.payloads as InMemoryPayloadStore).objects.clear();

    const error = await rejection(w.claim(run));

    expect(error).to.be.instanceOf(JournalPayloadMissingError);
    const failed = await w.service.get(run.orgId, run.runId);
    expect(failed).to.include({ status: 'failed', attemptId: null });
    const [level, , meta] = w.logs.find(([, message]) => message === 'run.history_unreadable')!;
    expect(level).to.equal('warn');
    expect(meta).to.include({ runId: run.runId, reason: 'missing', turns: 2, outcome: 'failed' });
    expect(JSON.stringify(meta)).to.not.include('first answer');
    expect(((await rejection(w.claim(run))) as HarnessConflictError).code, 'a later claim').to.equal('RUN_NOT_CLAIMABLE');
  });

  it('PERFB-8: the queue moves past a run whose history is gone', async () => {
    const w = await world();
    const broken = await w.admit();
    (w.backend.payloads as InMemoryPayloadStore).objects.clear();
    const healthy = await w.admit();

    const claimed = await w.claimNext();

    expect(claimed?.run.runId).to.equal(healthy.runId);
    expect((await w.service.get(broken.orgId, broken.runId))?.status).to.equal('failed');
    const [, , skipped] = w.logs.find(([, message]) => message === 'run.claim_next_skipped')!;
    expect(skipped).to.include({ runId: broken.runId, reason: 'journal_payload_missing' });
  });

  it('PERFB-8: a store that cannot answer refuses the claim and leaves the run claimable', async () => {
    let down = true;
    const w = await world({
      blobs: (stored) => ({
        put: stored.put.bind(stored),
        get: (orgId, ref) =>
          down ? Promise.reject(new PayloadStoreUnavailableError('get', new Error('down'))) : stored.get(orgId, ref),
      }),
    });
    const run = await w.admit();

    expect(await rejection(w.claim(run))).to.be.instanceOf(PayloadStoreUnavailableError);
    expect(await w.claimNext(), 'the queue skips it meanwhile').to.equal(null);
    expect(await w.service.get(run.orgId, run.runId)).to.include({ status: 'queued', attemptId: null, fence: 0 });

    down = false;
    expect((await w.claim(run)).run.input?.previousConversations).to.deep.equal(TURNS);
  });

  it('PERFB-B5: a run claimed before its admission is majority-visible is claimed with its history, not failed for it', async () => {
    // The first read of the parts answers as a majority read does in the instant after the admission committed.
    const pass = passes();
    const w = await world({
      blobs: (stored) => ({
        put: stored.put.bind(stored),
        get: (orgId, ref) => (pass(ref) === 1 ? Promise.resolve(null) : stored.get(orgId, ref)),
      }),
    });
    const run = await w.admit();

    const claimed = await w.claim(run);

    expect(claimed.run.input?.previousConversations).to.deep.equal(TURNS);
    expect(claimed.run.status).to.equal('running');
    expect(w.logs.some(([, message]) => message === 'run.history_unreadable')).to.equal(false);
  });

  it('PERFB-8: a store that stops answering between the first read and the claim gives the attempt back', async () => {
    const pass = passes();
    const w = await world({
      blobs: (stored) => ({
        put: stored.put.bind(stored),
        get: (orgId, ref) => {
          const nth = pass(ref);
          if (nth === 1) return Promise.resolve(null);
          if (nth === 2) return Promise.reject(new PayloadStoreUnavailableError('get', new Error('down')));
          return stored.get(orgId, ref);
        },
      }),
    });
    const run = await w.admit();

    expect(await rejection(w.claim(run))).to.be.instanceOf(PayloadStoreUnavailableError);
    expect(await w.service.get(run.orgId, run.runId)).to.include({ status: 'queued', attemptId: null });

    expect((await w.claim(run)).run.input?.previousConversations).to.deep.equal(TURNS);
  });

  it('a Node composed without a history reader refuses the claim and keeps the run', async () => {
    const w = await world({ reader: 'none' });
    const run = await w.admit();
    expect(await rejection(w.claim(run))).to.be.instanceOf(HarnessUnavailableError);
    expect((await w.service.get(run.orgId, run.runId))?.status).to.equal('queued');
  });
});
