import { expect } from 'chai';
import { existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  HARNESS_STORES,
  HarnessStoreUnavailableError,
  harnessStoresReady,
  harnessStoresWithoutPostgres,
  pickHarnessStore,
} from '../../../src/libs/repositories/agent-harness/stores';
import { PgJournalDurabilityProbe } from '../../../src/libs/repositories/agent-harness/journal-durability-probe/pg';
import {
  agentHarnessHealth,
  isAgentHarnessInfrastructureReady,
  JournalCapability,
} from '../../../src/modules/agent-harness/config';

const ROOT = join(__dirname, '../../../src/libs/repositories');

/** Every store directory (one holding a `mongo.ts`) of the harness and the control plane. */
const storeDirectories = (): string[] =>
  ['agent-harness', 'control-plane'].flatMap((area) =>
    readdirSync(join(ROOT, area), { withFileTypes: true })
      .filter(
        (entry) =>
          entry.isDirectory() &&
          existsSync(join(ROOT, area, entry.name, 'mongo.ts')),
      )
      .map((entry) => `${area}/${entry.name}`),
  );

describe('WP-114 harness store registry', () => {
  const saved = process.env.DOC_STORE;
  afterEach(() => {
    if (saved === undefined) delete process.env.DOC_STORE;
    else process.env.DOC_STORE = saved;
  });

  it('lists every store directory, and only those', () => {
    expect(Object.keys(HARNESS_STORES).sort()).to.deep.equal(
      storeDirectories().sort(),
    );
  });

  it('says a store has a PostgreSQL implementation exactly when its pg.ts exists', () => {
    for (const [name, entry] of Object.entries(HARNESS_STORES)) {
      if (entry.postgres === 'not-needed') continue;
      expect(existsSync(join(ROOT, name, 'pg.ts')), name).to.equal(
        entry.postgres,
      );
    }
  });

  it('a store with a PostgreSQL implementation has an index.ts that picks it', () => {
    for (const [name, entry] of Object.entries(HARNESS_STORES)) {
      // the clock is handed to each store's constructor, never picked on its own
      if (entry.postgres !== true || name === 'agent-harness/time') continue;
      expect(existsSync(join(ROOT, name, 'index.ts')), name).to.equal(true);
    }
  });

  it('picks the Mongo store by default and the PostgreSQL one under DOC_STORE=postgres', () => {
    delete process.env.DOC_STORE;
    expect(
      pickHarnessStore<object>('x', {
        mongo: () => ({ m: 1 }),
        postgres: () => ({ p: 1 }),
      }),
    ).to.deep.equal({ m: 1 });
    process.env.DOC_STORE = 'postgres';
    expect(
      pickHarnessStore<object>('x', {
        mongo: () => ({ m: 1 }),
        postgres: () => ({ p: 1 }),
      }),
    ).to.deep.equal({ p: 1 });
  });

  describe('DOC_STORE=postgres with a store still Mongo-only (WP109-FU-1)', () => {
    const incomplete = {
      ...HARNESS_STORES,
      'agent-harness/example': { postgres: false as const },
    };
    const env = {
      DOC_STORE: 'postgres',
      AGENT_HARNESS_RELAY_ENABLED: 'true',
      AGENT_HARNESS_WORKER_ENABLED: 'true',
    };

    it('composing does not throw: the store is a stand-in that refuses every call with a plain error', async () => {
      process.env.DOC_STORE = 'postgres';
      const store = pickHarnessStore<{ get(id: string): Promise<unknown> }>(
        'agent-harness/example',
        {
          mongo: () => ({ get: () => Promise.resolve('mongo') }),
        },
      );
      let failure: unknown;
      try {
        await store.get('x');
      } catch (error) {
        failure = error;
      }
      expect(failure).to.be.instanceOf(HarnessStoreUnavailableError);
      expect((failure as Error).message).to.match(
        /no PostgreSQL implementation/,
      );
      expect(harnessStoresReady(incomplete)).to.equal(false);
      expect(harnessStoresWithoutPostgres(incomplete)).to.include(
        'agent-harness/example',
      );
    });

    it('the journal probe records the harness unavailable, and health tells the admin why in plain words', async () => {
      let recorded: JournalCapability | null = null;
      const probe = new PgJournalDurabilityProbe(
        () => harnessStoresWithoutPostgres(incomplete),
        (capability) => {
          recorded = capability;
        },
      );
      expect(await probe.probe()).to.deep.equal({ 'journal.durable': false });
      expect(recorded).to.deep.equal({
        durable: false,
        reason: 'postgres_incomplete',
      });
      expect(isAgentHarnessInfrastructureReady(env, recorded)).to.equal(false);
      const health = agentHarnessHealth(env, recorded);
      expect(health.status).to.equal('unavailable');
      expect((health as { detail: string }).detail).to.match(
        /PostgreSQL \(DOC_STORE=postgres\)/,
      );
    });

    it('with every store on PostgreSQL the journal is durable and the harness may run', async () => {
      let recorded: JournalCapability | null = null;
      await new PgJournalDurabilityProbe(
        () => [],
        (c) => {
          recorded = c;
        },
      ).probe();
      expect(recorded).to.deep.equal({ durable: true, reason: 'postgres' });
      expect(isAgentHarnessInfrastructureReady(env, recorded)).to.equal(true);
    });
  });
});
