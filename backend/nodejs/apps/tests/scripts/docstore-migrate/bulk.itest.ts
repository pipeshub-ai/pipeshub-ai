import 'reflect-metadata';
import { expect } from 'chai';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { loadConfig } from '../../../src/scripts/docstore-migrate/config';
import { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import { JobState } from '../../../src/scripts/docstore-migrate/state';
import { bulk, StoppedForTest } from '../../../src/scripts/docstore-migrate/bulk';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { syncCredentials, verifyCredentials, verifyCredentialsFor } from '../../../src/scripts/docstore-migrate/credentials';
import { ORG_A, identityDocs } from './fixtures/identity';
import { at, oid } from './roundtrip';
import { TestMongo, haveMongod, startMongo } from './mongo-harness';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_bulk';
const log = (): void => undefined;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: bulk copy (${bin})`, function () {
    let stack: PgStack;
    let mongo: TestMongo;
    let rt: MigrationRuntime;
    const UPPER_USER = oid();
    const UPPER_CRED = oid();
    // fixed: letters in both cases at both even and odd positions
    const MIXED_HEX = '5f3aBAc0D1e2F4a6b7C8d9Ea';
    const MIXED_USER = oid(MIXED_HEX);
    const MIXED_OLD = oid();
    const MIXED_CRED = oid();
    /** Letters alternate in case: neither the stored form nor its lower- or upper-case. */
    const mixed = (id: string): string => id.split('').map((ch, i) => (i % 2 ? ch.toUpperCase() : ch.toLowerCase())).join('');

    /** MongoDB refuses U+0000 in a key, so fixture keys that have one are written without it. */
    const mongoSafe = (v: unknown): unknown => {
      if (Array.isArray(v)) return v.map(mongoSafe);
      if (v && typeof v === 'object' && Object.getPrototypeOf(v) === Object.prototype)
        return Object.fromEntries(Object.entries(v as object).map(([k, x]) => [k.split('\u0000').join(''), mongoSafe(x)]));
      return v;
    };

    before(async () => {
      stack = await startPgStack(bin, 'migrate_bulk', { importer: true });
      mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp');
      const docs = identityDocs();
      // a source whose ids are upper-case hex strings (the importer stores them lower-case)
      docs.users.push({
        _id: UPPER_USER, slug: 'user-1090', orgId: ORG_A, fullName: 'Upper Case', email: 'upper@acme.example',
        createdAt: at('2026-01-08T00:00:00.000Z'), updatedAt: at('2026-01-08T00:00:00.000Z'), __v: 0,
      });
      docs.userCredentials.push({
        _id: UPPER_CRED, userId: String(UPPER_USER).toUpperCase(), orgId: String(ORG_A).toUpperCase(), ipAddress: '10.0.0.9',
        hashedPassword: '$2b$10$upperhash', createdAt: at('2026-01-08T00:00:00.000Z'), updatedAt: at('2026-01-08T00:00:00.000Z'), __v: 0,
      });
      docs.users.push({
        _id: MIXED_USER, slug: 'user-1091', orgId: ORG_A, fullName: 'Mixed Case', email: 'mixed@acme.example',
        createdAt: at('2026-01-09T00:00:00.000Z'), updatedAt: at('2026-01-09T00:00:00.000Z'), __v: 0,
      });
      docs.userCredentials.push({
        _id: MIXED_CRED, userId: MIXED_HEX, orgId: mixed(String(ORG_A)), ipAddress: '10.0.0.10',
        hashedPassword: '$2b$10$mixedhash', createdAt: at('2026-01-19T00:00:00.000Z'), updatedAt: at('2026-01-19T00:00:00.000Z'), __v: 0,
      });
      // the same account's older credential, stored in lower case: the newer mixed-case one must win
      docs.userCredentials.push({
        _id: MIXED_OLD, userId: MIXED_HEX.toLowerCase(), orgId: String(ORG_A), ipAddress: '10.0.0.11',
        hashedPassword: '$2b$10$olderhash', createdAt: at('2026-01-09T00:00:00.000Z'), updatedAt: at('2026-01-09T00:00:00.000Z'), __v: 0,
      });
      for (const [name, list] of Object.entries(docs))
        if (list.length) await mongo.client.db(DB).collection(name).insertMany(list.map(mongoSafe) as never[], { ordered: false });
      rt = await MigrationRuntime.open(
        loadConfig({
          ...process.env,
          MONGO_URI: mongo.uri,
          MONGO_DB_NAME: DB,
          PG_IMPORT_URL: stack.cluster.url('migrate_bulk', 'pipeshub_import').replace('pipeshub_import@', 'pipeshub_import:x@'),
          PG_NODE_URL: stack.cluster.url('migrate_bulk', 'pipeshub_node_login', stack.bouncer.port),
          PG_MIGRATE_URL: stack.cluster.url('migrate_bulk', 'pipeshub_migrate'),
          MIGRATE_SCHEMA_COMMAND: 'none',
          MIGRATE_BATCH_SIZE: '2', // several batches per collection
          MIGRATE_PARALLEL: '1',
        }),
      );
    });
    after(async () => {
      await rt?.close();
      await mongo?.stop();
      await stack?.stop();
    });

    const tables = async (): Promise<string[]> =>
      (await stack.admin((c) => c.query("SELECT c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'identity' AND c.relkind IN ('r','p') AND NOT c.relispartition ORDER BY 1"))).rows.map((r: { t: string }) => r.t);

    /** Every identity row, as text, per table, in a stable order. */
    const snapshot = async (): Promise<Record<string, string[]>> => {
      const out: Record<string, string[]> = {};
      for (const t of await tables())
        out[t] = (await stack.admin((c) => c.query(`SELECT to_jsonb(x)::text AS r FROM identity.${t} x`))).rows.map((r: { r: string }) => r.r).sort();
      return out;
    };

    it('stops after a checkpoint, resumes from it, and ends with the rows of a clean run, none twice', async () => {
      const js = await JobState.attach(rt);
      let stopped = false;
      try {
        await bulk(rt, js, { log, stopAfterBatches: 3 });
      } catch (e) {
        if (!(e instanceof StoppedForTest)) throw e;
        stopped = true;
      }
      expect(stopped, 'the run was interrupted').to.equal(true);
      // the checkpoint is in the database, not only in memory
      const saved = (await JobState.current(rt))!.progress;
      const running = Object.entries(saved.collections).filter(([, p]) => p.state === 'running');
      expect(running, 'one collection stopped part way').to.have.length(1);
      expect(running[0]![1].last, 'with its last id saved').to.be.a('string');

      const resumed = await JobState.attach(rt);
      expect(resumed.id).to.equal(js.id);
      await bulk(rt, resumed, { log });
      const done = (await JobState.current(rt))!.progress;
      expect(done.phase).to.equal('bulk_done');
      for (const m of MAPPINGS) {
        const p = done.collections[m.collection]!;
        const n = await mongo.client.db(DB).collection(m.collection).countDocuments();
        // a document copied twice would count twice
        expect(p.copied + p.skipped, m.collection).to.equal(n);
        expect(p.state, m.collection).to.equal('done');
      }
      const interrupted = await snapshot();
      await resumed.finish('done');

      await stack.admin((c) => c.query(`TRUNCATE ${Object.keys(interrupted).map((t) => `identity.${t}`).join(', ')} RESTART IDENTITY CASCADE`));
      const clean = await JobState.attach(rt);
      expect(clean.id).to.not.equal(js.id);
      await bulk(rt, clean, { log });
      expect(await snapshot()).to.deep.equal(interrupted);
      await clean.finish('done');
    });

    it('upper-case ids in the source: the copy, the verify and the follower agree, with no false difference', async () => {
      const row = async (): Promise<number> =>
        Number((await stack.admin((c) => c.query('SELECT count(*) AS n FROM identity.user_credentials WHERE user_id = $1', [String(UPPER_USER)]))).rows[0].n);
      expect(await row()).to.equal(1);
      const whole = await verifyCredentials(rt);
      expect(whole.diffs).to.deep.equal([]);
      expect(whole.countMismatches).to.deep.equal([]);
      expect(await verifyCredentialsFor(rt, [String(UPPER_CRED)])).to.deep.equal([]);
      // the follower writes the account again from what MongoDB holds (the id as the change gave it)
      await syncCredentials(rt, [String(UPPER_USER).toUpperCase()]);
      expect(await row()).to.equal(1);
      const after = await verifyCredentials(rt);
      expect(after.diffs).to.deep.equal([]);
      expect(after.countMismatches).to.deep.equal([]);
    });

    it('a mixed-case id in the source: the follower keeps the row and the verify finds no difference', async () => {
      const rows = async (): Promise<number> =>
        Number((await stack.admin((c) => c.query('SELECT count(*) AS n FROM identity.user_credentials WHERE user_id = $1', [String(MIXED_USER)]))).rows[0].n);
      const hex = [...MIXED_HEX.matchAll(/[a-f]/gi)].map((m) => m.index!);
      for (const parity of [0, 1]) {
        expect(hex.some((i) => i % 2 === parity && MIXED_HEX[i] === MIXED_HEX[i]!.toLowerCase()), `lower at ${parity}`).to.equal(true);
        expect(hex.some((i) => i % 2 === parity && MIXED_HEX[i] === MIXED_HEX[i]!.toUpperCase()), `upper at ${parity}`).to.equal(true);
      }
      expect(MIXED_HEX).to.not.equal(MIXED_HEX.toLowerCase());
      const hash = async (): Promise<string> =>
        (await stack.admin((c) => c.query('SELECT password_hash FROM identity.user_credentials WHERE user_id = $1', [String(MIXED_USER)]))).rows[0].password_hash;
      expect(await rows()).to.equal(1);
      expect(await hash(), 'the copy took the newer credential').to.equal('$2b$10$mixedhash');
      expect(await verifyCredentialsFor(rt, [String(MIXED_CRED)])).to.deep.equal([]);
      await syncCredentials(rt, [String(MIXED_USER)]);
      expect(await rows(), 'the row is replaced, not dropped').to.equal(1);
      expect(await hash(), 'and by the newer credential').to.equal('$2b$10$mixedhash');
      const after = await verifyCredentials(rt);
      expect(after.diffs).to.deep.equal([]);
      expect(after.countMismatches).to.deep.equal([]);
      // an id with no credential at all still has no row afterwards
      const none = String(oid());
      await syncCredentials(rt, [none]);
      expect(Number((await stack.admin((c) => c.query('SELECT count(*) AS n FROM identity.user_credentials WHERE user_id = $1', [none]))).rows[0].n)).to.equal(0);
    });
  });
}
