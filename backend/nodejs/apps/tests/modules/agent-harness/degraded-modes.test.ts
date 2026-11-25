/**
 * P1.17 degraded modes on the Node side (REPORT 6.17), driven by failpoints:
 * payload store down (DM-4, DM-5), KV store down under the authority
 * documents (DM-2), broker down (DM-3). Redis-down streaming (DM-1) is the S6
 * LiveBus fallback, covered in projection/stream-service.test.ts.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { AgentJournalPayloadModel } from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { CachedConfigDocument } from '../../../src/modules/agent-harness/application/config-documents';
import {
  ExecutionService,
  PAYLOAD_STORE_BACKOFF_MS,
} from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { WorkerCredentialAuthority } from '../../../src/modules/agent-harness/application/worker-credential-authority';
import {
  AUTHORITY_CACHE_TTL_MS,
  AUTHORITY_STALE_MAX_MS,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import {
  HarnessConflictError,
  HarnessUnauthorizedError,
  HarnessUnavailableError,
  PayloadStoreUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ConfigDocumentStore } from '../../../src/modules/agent-harness/ports/config-document-store';
import { PayloadStore } from '../../../src/modules/agent-harness/ports/payload-store';
import { failpointAsync, registry } from '../../support/harness/failpoints';
import { testCipher } from './support/data-protection';
import {
  contractClock,
  createContractHarness,
  DURABLE_CAPABILITIES,
} from './support/execution-store-contract';
import { workerEvent } from './support/journal-fixtures';
import { HARNESS_ENABLED } from './support/rollout';
import { uncheckedJournalHistory } from '../../support/harness/journal-history';

type Line = [level: string, message: string, meta: Record<string, unknown>];

const recording = (): HarnessLogger & { lines: Line[] } => {
  const lines: Line[] = [];
  const at =
    (level: string) =>
    (message: string, meta: Record<string, unknown> = {}) => {
      lines.push([level, message, meta]);
    };
  return {
    lines,
    debug: at('debug'),
    info: at('info'),
    warn: at('warn'),
    error: at('error'),
  };
};

/** The adapter's view of an outage: its I/O failed, it reports "unavailable". */
class FailpointPayloadStore implements PayloadStore {
  constructor(private readonly inner: PayloadStore) {}

  async put(orgId: string, hash: string, bytes: Buffer): Promise<string> {
    await failpointAsync('node.payload.put');
    return this.inner.put(orgId, hash, bytes);
  }

  async get(orgId: string, ref: string): Promise<Buffer | null> {
    await failpointAsync('node.payload.get');
    return this.inner.get(orgId, ref);
  }
}

const armOutage = (operation: 'put' | 'get'): void => {
  registry.arm(`node.payload.${operation}`, {
    action: {
      kind: 'raise',
      error: () => new PayloadStoreUnavailableError(operation),
    },
    trigger: { kind: 'always' },
  });
};

// Above run.yielded (~80 B), below the bulky fixture: small fixtures exercise the payload store.
const INLINE_MAX = 160;

const note = (text: string) =>
  workerEvent('tool.outcome', {
    invocationId: 'inv-1',
    status: 'ok',
    receipt: { note: text },
    labels: { integrity: 'external' },
  });

const setup = async (nodeSkewMs = 0) => {
  const clock = contractClock();
  const store = new InMemoryExecutionStore(clock);
  const payloads = new FailpointPayloadStore(new InMemoryPayloadStore());
  const h = createContractHarness(
    {
      executionStore: store,
      journal: store.journal,
      payloads: new InMemoryPayloadStore(),
      admissionStore: store,
      dispatches: async () => [],
      deletePayload: async () => undefined,
    },
    clock,
  );
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const logger = recording();
  const service = new ExecutionService({
    store,
    journal: store.journal,
    journals: new JournalService(payloads, testCipher(), INLINE_MAX),
    capabilities: DURABLE_CAPABILITIES,
    logger,
    // The Node process's clock; the store keeps its own (CH-14).
    clock: { now: () => clock.now() + nodeSkewMs },
  });
  const { run } = await h.admit({
    agentKey: 'agent-1',
    authorization: 'Bearer user',
    deadlineMs: 10 * 60_000,
  });
  const claim = (attemptId: string) =>
    service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'w',
      attemptId,
      leaseDurationMs: 60_000,
    });
  const commit = (claimed: HarnessRun, text: string, n = 1) =>
    service.commit({
      commandId: `${claimed.attemptId ?? ''}:tool:${String(n)}`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'w',
      attemptId: claimed.attemptId ?? '',
      fence: claimed.fence,
      events: [note(text)],
      legacyEvents: [],
      outbox: [],
    });
  const journalTypes = async () =>
    (await store.journal.readRun(run.orgId, run.runId, 0, 100)).map((event) => [
      event.type,
      event.data?.['reason'] ?? null,
    ]);
  return { clock, store, h, service, logger, run, claim, commit, journalTypes };
};

const conflictCode = async (work: Promise<unknown>): Promise<string> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessConflictError);
    return (error as HarnessConflictError).code;
  }
  return expect.fail('expected a conflict');
};

describe('P1.17 degraded modes', () => {
  afterEach(() => {
    registry.reset();
  });

  describe('DM-4 payload store down on a commit', () => {
    it('journals nothing of the commit, yields the run with a backoff and tells the worker to stop', async () => {
      const t = await setup();
      const { run: claimed } = await t.claim('a1');
      armOutage('put');
      expect(await conflictCode(t.commit(claimed, 'x'.repeat(200)))).to.equal(
        'STALE_ATTEMPT',
      );
      const after = await t.service.get(t.run.orgId, t.run.runId);
      expect(after?.status).to.equal('queued');
      expect(after?.notBefore).to.be.at.least(
        t.clock.now() + PAYLOAD_STORE_BACKOFF_MS - 1_000,
      );
      const types = await t.journalTypes();
      expect(types).to.deep.include([
        'run.yielded',
        'payload_store_unavailable',
      ]);
      expect(types.map(([type]) => type)).not.to.include('tool.outcome');
      const line = t.logger.lines.find(
        ([, message]) => message === 'run.payload_store_unavailable',
      );
      expect(line?.[0]).to.equal('warn');
      expect(line?.[2]).to.include({
        runId: t.run.runId,
        attemptId: 'a1',
        operation: 'put',
        outcome: 'yielded',
      });

      // After the backoff and the outage, the next claim commits normally.
      registry.reset();
      t.clock.set(t.clock.now() + PAYLOAD_STORE_BACKOFF_MS + 1_000);
      const { run: again } = await t.claim('a2');
      expect(again.fence).to.equal(claimed.fence + 1);
      await t.commit(again, 'x'.repeat(200));
      expect((await t.journalTypes()).map(([type]) => type)).to.include(
        'tool.outcome',
      );
    });

    for (const skewMs of [60 * 60_000, -60 * 60_000]) {
      it(`CH-14: a Node clock ${skewMs > 0 ? 'ahead' : 'behind'} by an hour parks the run for the backoff on the store's clock`, async () => {
        const t = await setup(skewMs);
        const { run: claimed } = await t.claim('a1');
        armOutage('put');
        expect(await conflictCode(t.commit(claimed, 'x'.repeat(200)))).to.equal(
          'STALE_ATTEMPT',
        );
        const after = await t.service.get(t.run.orgId, t.run.runId);
        expect(after?.notBefore).to.equal(
          t.clock.now() + PAYLOAD_STORE_BACKOFF_MS,
        );
      });
    }

    it('leaves commits under the inline threshold unaffected', async () => {
      uncheckedJournalHistory(
        'a bare tool outcome stands in for a worker commit',
      );
      const t = await setup();
      const { run: claimed } = await t.claim('a1');
      armOutage('put');
      armOutage('get');
      await t.commit(claimed, 'ok');
      expect((await t.service.get(t.run.orgId, t.run.runId))?.status).to.equal(
        'running',
      );
    });

    it('keeps the 503 when the yield cannot be written (the worker retries)', async () => {
      const t = await setup();
      const { run: claimed } = await t.claim('a1');
      await t.service.control({
        orgId: t.run.orgId,
        runId: t.run.runId,
        commandId: 'cancel-1',
        dispatchCommandId: 'cancel-dispatch-1',
        action: 'cancel',
        expectedRevision: claimed.revision,
      });
      armOutage('put');
      try {
        await t.commit(claimed, 'x'.repeat(200));
        expect.fail('expected the payload store to be unavailable');
      } catch (error) {
        expect(error).to.be.instanceOf(PayloadStoreUnavailableError);
        expect(error).to.be.instanceOf(HarnessUnavailableError);
      }
      expect((await t.service.get(t.run.orgId, t.run.runId))?.status).to.equal(
        'cancelling',
      );
      expect(
        t.logger.lines.find(
          ([, message]) => message === 'run.payload_store_unavailable',
        )?.[2],
      ).to.include({ outcome: 'retry' });
    });
  });

  describe('DM-5 payload store down on a read', () => {
    it('never fails the run as journal_payload_missing; the owning attempt yields', async () => {
      const t = await setup();
      const { run: claimed } = await t.claim('a1');
      await t.commit(claimed, 'x'.repeat(200));
      armOutage('get');
      const read = () =>
        t.service.readRunJournal({
          orgId: t.run.orgId,
          runId: t.run.runId,
          workerId: 'w',
          attemptId: 'a1',
          fence: claimed.fence,
          afterSeq: 0,
          limit: 100,
          scope: 'run',
        });
      expect(await conflictCode(read())).to.equal('STALE_ATTEMPT');
      const after = await t.service.get(t.run.orgId, t.run.runId);
      expect(after?.status).to.equal('queued');
      expect(after?.status).not.to.equal('failed');
      // The attempt that left keeps reading until its token expires: a 503, not a yield.
      try {
        await read();
        expect.fail('expected the payload store to be unavailable');
      } catch (error) {
        expect(error).to.be.instanceOf(PayloadStoreUnavailableError);
      }
    });

    it('a first claim that cannot claim-check its pin is refused 503; the run stays queued and claim-next moves on', async () => {
      const t = await setup();
      armOutage('put');
      try {
        await t.claim('a1');
        expect.fail('expected the payload store to be unavailable');
      } catch (error) {
        expect(error).to.be.instanceOf(PayloadStoreUnavailableError);
      }
      const after = await t.service.get(t.run.orgId, t.run.runId);
      expect(after?.status).to.equal('queued');
      expect(after?.fence).to.equal(t.run.fence);
      expect(
        await t.service.claimNext({
          role: 'dispatched',
          workerId: 'w',
          attemptId: 'a2',
          leaseDurationMs: 60_000,
        }),
      ).to.equal(null);
      expect(
        t.logger.lines.find(
          ([, message]) => message === 'run.claim_next_skipped',
        )?.[2],
      ).to.include({ reason: 'payload_store_unavailable' });
    });
  });

  describe('DM-4/DM-5 adapters report an outage, not a missing object', () => {
    afterEach(() => {
      sinon.restore();
    });

    it('the Mongo payload store turns a failed write or read into "unavailable"', async () => {
      const store = new MongoPayloadStore();
      sinon
        .stub(AgentJournalPayloadModel, 'updateOne')
        .rejects(new Error('connection reset'));
      try {
        await store.put('org-1', 'sha256:abc', Buffer.from('x'));
        expect.fail('expected unavailable');
      } catch (error) {
        expect(error).to.be.instanceOf(PayloadStoreUnavailableError);
        expect((error as PayloadStoreUnavailableError).operation).to.equal(
          'put',
        );
      }
      const failing = {
        read: () => failing,
        readConcern: () => failing,
        lean: () => Promise.reject(new Error('not primary')),
      };
      sinon.stub(AgentJournalPayloadModel, 'findOne').returns(failing as never);
      try {
        await store.get('org-1', 'mongo:org-1:sha256:abc');
        expect.fail('expected unavailable');
      } catch (error) {
        expect(error).to.be.instanceOf(PayloadStoreUnavailableError);
        expect((error as PayloadStoreUnavailableError).operation).to.equal(
          'get',
        );
      }
    });
  });

  describe('DM-3 broker down', () => {
    it('a run whose wake hint was never delivered is claimed by claim-next polling', async () => {
      const t = await setup();
      const claimed = await t.service.claimNext({
        role: 'dispatched',
        workerId: 'w',
        attemptId: 'a1',
        leaseDurationMs: 60_000,
      });
      expect(claimed?.run.runId).to.equal(t.run.runId);
    });
  });

  describe('DM-2 KV store down under the authority documents', () => {
    class FlakyStore<T> implements ConfigDocumentStore<T> {
      down = false;
      constructor(private readonly inner: ConfigDocumentStore<T>) {}
      read() {
        if (this.down) return Promise.reject(new Error('ECONNREFUSED'));
        return this.inner.read();
      }
      replace(expected: string | null, value: T) {
        return this.inner.replace(expected, value);
      }
    }

    it('serves the last good copy for a bounded time, logs once, then fails closed', async () => {
      let now = 0;
      const inner = new InMemoryConfigDocumentStore<{ v: number }>();
      await inner.replace(null, { v: 1 });
      const flaky = new FlakyStore(inner);
      const logger = recording();
      const cached = new CachedConfigDocument(flaky, () => now, 1_000, 100, {
        name: 'doc',
        maxMs: 10_000,
        logger,
      });
      expect(await cached.get()).to.deep.equal({ v: 1 });
      flaky.down = true;
      now = 1_500;
      expect(await cached.get()).to.deep.equal({ v: 1 });
      now = 5_000;
      expect(await cached.get()).to.deep.equal({ v: 1 });
      expect(
        logger.lines.map(([level, message]) => `${level}:${message}`),
      ).to.deep.equal(['warn:authority.config_stale']);
      now = 11_000;
      try {
        await cached.get();
        expect.fail('expected the stale bound to fail closed');
      } catch (error) {
        expect((error as Error).message).to.equal('ECONNREFUSED');
      }
      // A forced refresh never serves stale.
      try {
        await cached.refresh();
        expect.fail('expected refresh to fail');
      } catch (error) {
        expect((error as Error).message).to.equal('ECONNREFUSED');
      }
      flaky.down = false;
      now = 12_000;
      expect(await cached.get()).to.deep.equal({ v: 1 });
      expect(logger.lines.at(-1)?.[1]).to.equal('authority.config_recovered');
    });

    it('QA17-F3: once a reload failed, a hanging store costs no caller a wait; one reload runs behind the copy', async () => {
      let now = 0;
      const inner = new InMemoryConfigDocumentStore<{ v: number }>();
      await inner.replace(null, { v: 1 });
      let reads = 0;
      let down = false;
      const hanging: ConfigDocumentStore<{ v: number }> = {
        read: () => {
          reads += 1;
          if (!down) return inner.read();
          return reads === 2
            ? Promise.reject(new Error('MaxRetriesPerRequestError'))
            : new Promise(() => undefined);
        },
        replace: (expected, value) => inner.replace(expected, value),
      };
      const cached = new CachedConfigDocument(hanging, () => now, 1_000, 100, {
        name: 'doc',
        maxMs: 10_000,
      });
      expect(await cached.get()).to.deep.equal({ v: 1 });
      down = true;
      now = 1_500;
      expect(await cached.get()).to.deep.equal({ v: 1 });
      now = 2_500;
      expect(await cached.get()).to.deep.equal({ v: 1 });
      now = 3_500;
      expect(await cached.get()).to.deep.equal({ v: 1 });
      expect(reads).to.equal(3);
    });

    it('keeps accepting the worker credential while KV is down, within the bound only', async () => {
      let now = 1_000_000;
      const flaky = new FlakyStore(
        new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      );
      const logger = recording();
      const authority = new WorkerCredentialAuthority(
        flaky,
        { now: () => now },
        () => 'phw_test-credential',
        logger,
      );
      await authority.provision(null);
      flaky.down = true;
      now += AUTHORITY_CACHE_TTL_MS + 1;
      await authority.verify('phw_test-credential');
      now += AUTHORITY_STALE_MAX_MS;
      try {
        await authority.verify('phw_test-credential');
        expect.fail('expected the credential check to fail closed');
      } catch (error) {
        expect(error).not.to.be.instanceOf(HarnessUnauthorizedError);
        expect((error as Error).message).to.equal('ECONNREFUSED');
      }
      expect(JSON.stringify(logger.lines)).not.to.contain(
        'phw_test-credential',
      );
    });
  });
});
