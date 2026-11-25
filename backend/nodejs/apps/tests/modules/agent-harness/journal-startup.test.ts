import { expect } from 'chai';
import sinon from 'sinon';
import { MongoNetworkError, MongoNotConnectedError, MongoServerError } from 'mongodb';
import {
  prepareAgentHarnessStore,
  STORE_PREPARATION_RETRY,
  stopAgentHarnessStorePreparation,
} from '../../../src/modules/agent-harness/composition';
import {
  agentHarnessHealth,
  currentJournalCapability,
  JOURNAL_DETAIL,
  recordJournalCapability,
} from '../../../src/modules/agent-harness/config';
import * as journalStartup from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import {
  mongoBootMigration,
  probeJournalDurability,
  storeUnreachable,
  writablePrimary,
} from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const ready = {
  REPLICA_SET_AVAILABLE: 'true',
  AGENT_HARNESS_RELAY_ENABLED: 'true',
  AGENT_HARNESS_WORKER_ENABLED: 'true',
};

const logger = () => ({
  info: sinon.stub(),
  warn: sinon.stub(),
  error: sinon.stub(),
});

const fakeDb = (hello: Record<string, unknown>, updateOne = sinon.stub()) => ({
  admin: () => ({ command: sinon.stub().resolves(hello) }),
  collection: sinon.stub().returns({ updateOne }),
});

describe('agent harness store preparation', () => {
  afterEach(() => {
    stopAgentHarnessStorePreparation();
    sinon.restore();
    recordJournalCapability(null);
  });

  it('touches nothing on a deployment that did not ask for the harness', async () => {
    const log = logger();
    await prepareAgentHarnessStore(log as never, {});
    expect(currentJournalCapability()).to.equal(null);
    expect(log.info.called || log.warn.called || log.error.called).to.equal(
      false,
    );
  });

  const migrated = { droppedIndexes: [], createdIndexes: [], orphanedDispatchRows: 0 } as never;
  /** A capability service whose journal probe answers `reasons` in turn, then durable. */
  const probing = (...reasons: Array<'probe_failed' | 'majority_unavailable' | 'standalone' | 'arbiter'>) => {
    let refreshes = 0;
    return {
      refreshes: () => refreshes,
      refresh: async () => {
        const reason = reasons[refreshes];
        refreshes += 1;
        recordJournalCapability(
          reason === undefined ? { durable: true, reason: 'majority' } : { durable: false, reason },
        );
      },
    };
  };

  /** Waits the test releases one at a time, so a background retry runs exactly when the test says. */
  const steppedRetry = () => {
    const pending: Array<() => void> = [];
    const asked: number[] = [];
    return {
      asked,
      retry: {
        wait: (attempt: number) =>
          new Promise<void>((resolve) => {
            asked.push(attempt);
            pending.push(resolve);
          }),
      },
      /** Releases the oldest wait and lets the try after it run. */
      step: async (): Promise<void> => {
        pending.shift()?.();
        for (let turn = 0; turn < 20; turn += 1) await Promise.resolve();
      },
    };
  };
  const unauthorized = () => new MongoServerError({ message: 'not authorized to createIndex', code: 13 });

  it('OPS1-2-1: MongoDB unreachable: it keeps trying, however long that takes, and returns (so Node listens) only when it answers', async () => {
    const log = logger();
    const waited: number[] = [];
    const migrate = sinon.stub(journalStartup, 'migrateHarnessStore');
    for (let failed = 0; failed < 25; failed += 1) {
      migrate.onCall(failed).rejects(new MongoNetworkError('connection 3 to 127.0.0.1:27017 closed'));
    }
    migrate.resolves(migrated);
    const prepared: boolean[] = [];
    await prepareAgentHarnessStore(log as never, ready, probing() as never, {
      wait: async (attempt) => void waited.push(attempt),
    }, { prepared: (on) => void prepared.push(on) });
    expect(waited).to.deep.equal(Array.from({ length: 25 }, (_, index) => index + 1));
    expect(migrate.callCount).to.equal(26);
    expect(prepared).to.deep.equal([true]);
    expect(currentJournalCapability()).to.deep.equal({ durable: true, reason: 'majority' });
    expect(log.warn.callCount).to.equal(25);
    expect(log.warn.firstCall.args).to.deep.equal([
      'Agent harness store unreachable; not serving until it answers',
      { error: 'connection 3 to 127.0.0.1:27017 closed', attempt: 1 },
    ]);
  });

  it('OPS1-2-1: the wait before each further try doubles from one second and is capped at eight', async () => {
    const clock = sinon.useFakeTimers();
    try {
      const waits: number[] = [];
      for (const attempt of [1, 2, 3, 4, 5, 12]) {
        const started = clock.now;
        const waiting = STORE_PREPARATION_RETRY.wait(attempt);
        await clock.runAllAsync();
        await waiting;
        waits.push(clock.now - started);
      }
      expect(waits).to.deep.equal([1_000, 2_000, 4_000, 8_000, 8_000, 8_000]);
    } finally {
      clock.restore();
    }
  });

  it('OPS1-2-3: MongoDB answers but the harness cannot prepare: it returns at once with the harness unavailable, and turns it on by itself when a background try succeeds', async () => {
    const log = logger();
    const migrate = sinon.stub(journalStartup, 'migrateHarnessStore');
    migrate.onCall(0).rejects(unauthorized());
    migrate.onCall(1).rejects(unauthorized());
    migrate.resolves(migrated);
    const { retry, asked, step } = steppedRetry();
    const prepared: boolean[] = [];
    let started = 0;
    await prepareAgentHarnessStore(log as never, ready, probing() as never, retry, {
      prepared: (on) => void prepared.push(on),
      whenPrepared: () => void (started += 1),
    });

    expect(currentJournalCapability()).to.deep.equal({ durable: false, reason: 'preparation_failed' });
    expect(agentHarnessHealth(ready)).to.deep.equal({
      status: 'unavailable',
      reason: 'preparation_failed',
      detail: JOURNAL_DETAIL.preparation_failed,
    });
    expect(prepared).to.deep.equal([false]);
    expect(log.warn.firstCall.args[0]).to.equal(
      'Agent harness unavailable: its store preparation failed; serving everything else and trying again',
    );

    await step();
    expect(started, 'still failing').to.equal(0);
    await step();
    expect(asked).to.deep.equal([1, 2]);
    expect(started).to.equal(1);
    expect(prepared).to.deep.equal([false, true]);
    expect(agentHarnessHealth(ready)).to.deep.equal({ status: 'available' });
    expect(log.info.calledWith('Agent harness on: its store preparation succeeded', { attempts: 3 })).to.equal(true);
    expect(log.warn.callCount, 'one warning for the outage, not one per try').to.equal(1);
  });

  for (const reason of ['standalone', 'arbiter', 'majority_unavailable', 'probe_failed'] as const) {
    it(`OPS1-2-3: a journal that answers ${reason} leaves Node serving with the harness unavailable for that reason`, async () => {
      sinon.stub(journalStartup, 'migrateHarnessStore').resolves(migrated);
      sinon.stub(journalStartup, 'writablePrimary').resolves(true);
      const { retry } = steppedRetry();
      await prepareAgentHarnessStore(logger() as never, ready, probing(reason, reason) as never, retry, {
        prepared: () => undefined,
      });
      expect(agentHarnessHealth(ready)).to.deep.equal({ status: 'unavailable', reason, detail: JOURNAL_DETAIL[reason] });
      stopAgentHarnessStorePreparation();
    });
  }

  it('OP-11: every reason the harness is off for says why in plain words, never the code', () => {
    const reasons = ['standalone', 'arbiter', 'majority_unavailable', 'probe_failed', 'preparation_failed'] as const;
    for (const reason of reasons) {
      const health = agentHarnessHealth(ready, { durable: false, reason });
      if (health.status !== 'unavailable') throw new Error(reason);
      // A sentence, with no reason code (they are snake_case) standing in for the words.
      expect(health.detail, reason).to.match(/^[A-Z][^_]*\.$/);
    }
    const arbiter = agentHarnessHealth(ready, { durable: false, reason: 'arbiter' });
    expect(arbiter).to.have.property('detail').that.includes('arbiter').and.includes('values-cloud.yaml');
  });

  it('OP-11: the startup warning carries the plain words with the reason', async () => {
    sinon.stub(journalStartup, 'migrateHarnessStore').resolves(migrated);
    sinon.stub(journalStartup, 'writablePrimary').resolves(true);
    const log = logger();
    await prepareAgentHarnessStore(log as never, ready, probing('arbiter', 'arbiter') as never, steppedRetry().retry, {
      prepared: () => undefined,
    });
    expect(log.warn.firstCall.args[1]).to.deep.include({
      journal: { durable: false, reason: 'arbiter' },
      detail: JOURNAL_DETAIL.arbiter,
    });
    stopAgentHarnessStorePreparation();
  });

  for (const reason of ['majority_unavailable', 'probe_failed'] as const) {
    it(`OPS1-2-1: a journal that answers ${reason} while no primary answers is MongoDB away: Node does not listen until one does`, async () => {
      sinon.stub(journalStartup, 'migrateHarnessStore').resolves(migrated);
      const primary = sinon.stub(journalStartup, 'writablePrimary');
      primary.onFirstCall().resolves(false);
      const waited: number[] = [];
      await prepareAgentHarnessStore(logger() as never, ready, probing(reason) as never, {
        wait: async (attempt) => void waited.push(attempt),
      }, { prepared: () => undefined });
      expect(waited).to.deep.equal([1]);
      expect(agentHarnessHealth(ready)).to.deep.equal({ status: 'available' });
    });
  }

  it('OPS1-2-3: a stopping process ends the background preparation', async () => {
    sinon.stub(journalStartup, 'migrateHarnessStore').rejects(unauthorized());
    const { retry, step } = steppedRetry();
    let started = 0;
    await prepareAgentHarnessStore(logger() as never, ready, probing() as never, retry, {
      prepared: () => undefined,
      whenPrepared: () => void (started += 1),
    });
    (journalStartup.migrateHarnessStore as sinon.SinonStub).resolves(migrated);
    stopAgentHarnessStorePreparation();
    await step();
    expect(started).to.equal(0);
  });

  it('OPS1-2-4: only MongoDB being away for everyone counts as unreachable', () => {
    for (const away of [
      new MongoNetworkError('closed'),
      new MongoNotConnectedError('not connected'),
      new MongoServerError({ message: 'not primary', code: 10107 }),
      new MongoServerError({ message: 'stepped down', code: 189 }),
      // An index migration wraps what the driver threw.
      Object.assign(new Error('Migration core-store could not build x: not primary'), {
        cause: new MongoServerError({ message: 'not primary', code: 10107 }),
      }),
    ]) {
      expect(storeUnreachable(away), away.message).to.equal(true);
    }
    for (const harnessOnly of [
      unauthorized(),
      new MongoServerError({ message: 'index options conflict', code: 85 }),
      Object.assign(new Error('Migration core-store could not build x: E11000 duplicate key'), {
        cause: new MongoServerError({ message: 'E11000 duplicate key', code: 11000 }),
      }),
      new Error('journal not durable: standalone'),
    ]) {
      expect(storeUnreachable(harnessOnly), harnessOnly.message).to.equal(false);
    }
  });

  it('OPS1-2-5: the health route reports the harness off where it was not asked for', () => {
    expect(agentHarnessHealth({})).to.deep.equal({ status: 'off' });
    expect(agentHarnessHealth(ready, null)).to.deep.equal({ status: 'available' });
  });

  it('OPS1-2-1: a writable primary is one that says so; an error is none', async () => {
    expect(await writablePrimary(fakeDb({ setName: 'rs0', isWritablePrimary: true }) as never)).to.equal(true);
    expect(await writablePrimary(fakeDb({ setName: 'rs0', isWritablePrimary: false }) as never)).to.equal(false);
    const failing = { admin: () => ({ command: sinon.stub().rejects(new MongoNetworkError('closed')) }) };
    expect(await writablePrimary(failing as never)).to.equal(false);
  });

  describe('journal durability probe', () => {
    it('reports a standalone server as not durable', async () => {
      expect(await probeJournalDurability(fakeDb({}) as never)).to.deep.equal({
        durable: false,
        reason: 'standalone',
      });
    });

    it('reports a set with an arbiter (PSA) as not durable', async () => {
      const db = fakeDb({ setName: 'rs0', arbiters: ['arb:27017'] });
      expect(await probeJournalDurability(db as never)).to.deep.equal({
        durable: false,
        reason: 'arbiter',
      });
    });

    it('requires a majority-acknowledged probe write', async () => {
      const updateOne = sinon.stub().rejects(new Error('wtimeout'));
      const db = fakeDb({ setName: 'rs0' }, updateOne);
      expect(await probeJournalDurability(db as never, 10)).to.deep.equal({
        durable: false,
        reason: 'majority_unavailable',
      });
      expect(updateOne.firstCall.args[2]).to.deep.include({
        writeConcern: { w: 'majority', wtimeoutMS: 10 },
      });
    });
  });
});

describe('WP114-FU-E2E: the Mongo boot migrations under each document store', () => {
  const saved = process.env.DOC_STORE;
  afterEach(() => {
    if (saved === undefined) delete process.env.DOC_STORE;
    else process.env.DOC_STORE = saved;
  });

  it('under DOC_STORE=postgres a boot migration answers its no-op value and never asks for a Mongo connection', async () => {
    process.env.DOC_STORE = 'postgres';
    const migrate = sinon.stub().rejects(new Error('must not run'));
    expect(await mongoBootMigration({ created: [] as string[] }, migrate)).to.deep.equal({ created: [] });
    expect(migrate.called).to.equal(false);
  });

  it('on Mongo without a connection the migration fails as a rejected promise, not a throw at the call that started it', async () => {
    process.env.DOC_STORE = 'mongo';
    let started: Promise<number> | undefined;
    expect(() => {
      started = mongoBootMigration(0, (db) => Promise.resolve(db() ? 1 : 2));
    }).not.to.throw();
    let failure: unknown;
    await started!.catch((e: unknown) => (failure = e));
    expect(failure).to.be.instanceOf(MongoNotConnectedError);
  });

  it('the composition reaches the Mongo collections at boot only through mongoBootMigration', () => {
    // The stack on DOC_STORE=postgres stopped at boot on a direct database() call (LCFIX's run-input sealing).
    const source = readFileSync(join(__dirname, '../../../src/modules/agent-harness/composition.ts'), 'utf8');
    expect(source).not.to.match(/\bdatabase\(\)/);
  });
});
