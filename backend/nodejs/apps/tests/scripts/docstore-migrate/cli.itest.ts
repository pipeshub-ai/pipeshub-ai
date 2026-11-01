/* eslint-disable @typescript-eslint/restrict-plus-operands, @typescript-eslint/no-unused-vars -- assertions over untyped driver and CLI output */
/**
 * The migrator end to end against a real MongoDB (replica set) and a real PostgreSQL behind PgBouncer: the compose offline cut,
 * rollback in both directions, follow mode, drain, and the failure drills (kill mid-bulk and resume, a verify diff aborts the flip,
 * a wrong organisation stamp is caught, a target that is too small).
 *
 *   MIGRATE_TEST_MONGOD=… PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=…
 */
import 'reflect-metadata';
import { ChildProcess, spawn } from 'node:child_process';
import { MongoClient, ObjectId } from 'mongodb';
import { expect } from 'chai';
import { Pool } from 'pg';
import { runCli } from '../../../src/scripts/docstore-migrate/cli';
import { loadConfig } from '../../../src/scripts/docstore-migrate/config';
import { makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext';
import { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import { verifyAll } from '../../../src/scripts/docstore-migrate/verify';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { conversationDocs } from './fixtures/conversation';
import { contentDocs } from './fixtures/content';
import { identityDocs } from './fixtures/identity';
import { TestMongo, haveMongod, startMongo } from './mongo-harness';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_test';

const TABLE_SCHEMAS = ['identity', 'conversation', 'access', 'content', 'oauth', 'platform', 'integrations'];
const KEEP = new Set(['platform.store_epoch', 'platform.deployment_profile', 'platform.consumer_inbox']);

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate CLI end to end (${bin})`, function () {
    this.timeout(600_000);
    let stack: PgStack;
    let mongo: TestMongo;
    let env: NodeJS.ProcessEnv;
    const base = (): Record<string, unknown[]> => ({ ...identityDocs(), ...conversationDocs(), ...contentDocs() });

    before(async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_cli', { importer: true });
      mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp');
      env = {
        ...process.env,
        MONGO_URI: mongo.uri,
        MONGO_DB_NAME: DB,
        PG_IMPORT_URL: stack.cluster.url('migrate_cli', 'pipeshub_import').replace('pipeshub_import@', 'pipeshub_import:x@'),   // trust auth ignores it; an empty password reads as a shut login
        PG_NODE_URL: stack.cluster.url('migrate_cli', 'pipeshub_node_login', stack.bouncer.port),
        PG_MIGRATE_URL: stack.cluster.url('migrate_cli', 'pipeshub_migrate'),
        MIGRATE_SCHEMA_COMMAND: 'none',
        MIGRATE_BATCH_SIZE: '2',
        // the cutover waits as long as the client could hold a write (2 minutes with the usual options); the tests shorten it
        MIGRATE_TEST_HOOKS: '1',
        MIGRATE_FENCE_CLIENT_OPTIONS: JSON.stringify({ waitQueueTimeoutMS: 100, connectTimeoutMS: 100, serverSelectionTimeoutMS: 100, retryWrites: true }),
        MIGRATE_PG_FREE_BYTES: String(10 ** 12),
      };
    });
    after(async () => {
      await mongo?.stop();
      await stack?.stop();
    });

    /** MongoDB itself refuses U+0000 in a key (only values can hold it), so fixture keys that have one are written without it. */
    const mongoSafe = (v: unknown): unknown => {
      if (Array.isArray(v)) return v.map(mongoSafe);
      if (v && typeof v === 'object' && Object.getPrototypeOf(v) === Object.prototype) {
        return Object.fromEntries(Object.entries(v as object).map(([k, x]) => [k.split('\u0000').join(''), mongoSafe(x)]));
      }
      return v;
    };

    /** Fresh data on both sides before every test. */
    beforeEach(async () => {
      await mongo.client.db(DB).dropDatabase();
      const docs = base();
      for (const [name, list] of Object.entries(docs)) if (list.length) await mongo.client.db(DB).collection(name).insertMany(list.map(mongoSafe) as never[], { ordered: false, forceServerObjectId: false });
      await stack.admin(async (c) => {
        const t = await c.query(
          `SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r','p') AND NOT c.relispartition AND n.nspname = ANY($1)`,
          [TABLE_SCHEMAS],
        );
        const names = t.rows.map((r: { t: string }) => r.t).filter((n: string) => !KEEP.has(n) && n !== 'platform.jobs');
        await c.query(`TRUNCATE ${names.join(', ')} RESTART IDENTITY CASCADE`);
        await c.query('DELETE FROM platform.jobs');
        await c.query("ALTER SEQUENCE identity.org_slug_seq RESTART WITH 1001");
        await c.query("UPDATE platform.store_epoch SET backend = 'mongo', readonly = true");
      });
    });

    afterEach(function () {
      if (this.currentTest?.state === 'failed' && process.env.MIGRATE_TEST_PGLOG) {
        const log = require('node:fs').readFileSync(`${stack.cluster.root}/log`, 'utf8').split('\n');
        process.stderr.write(log.filter((l: string) => /ERROR|STATEMENT/.test(l)).slice(-12).join('\n') + '\n');
      }
    });

    /** Runs a command in this process and returns its exit code and printed JSON. */
    async function run(args: string[], extra: NodeJS.ProcessEnv = {}): Promise<{ code: number; out: any }> {
      let text = '';
      const w = process.stdout.write.bind(process.stdout);
      const e = process.stderr.write.bind(process.stderr);
      (process.stdout as any).write = (s: string): boolean => ((text += s), true);
      (process.stderr as any).write = (): boolean => true;
      try {
        const code = await runCli(args, { ...env, ...extra });
        return { code, out: text ? JSON.parse(text) : null };
      } finally {
        (process.stdout as any).write = w;
        (process.stderr as any).write = e;
      }
    }

    const jobs = async (): Promise<any[]> => (await stack.admin((c) => c.query('SELECT id, status, progress FROM platform.jobs ORDER BY id'))).rows;
    const fence = async (): Promise<string | undefined> => (await mongo.client.db(DB).collection<any>('docstore_fence').findOne({ _id: 'fence' }))?.mode;

    it('preflight reads the data and is clean (a leftover follower marker is not an unmapped collection)', async () => {
      await mongo.client.db(DB).collection<any>('docstore_migrate_marker').insertOne({ _id: 'left-over', at: new Date() });
      const { code, out } = await run(['preflight']);
      expect(out.red, JSON.stringify(out.red)).to.deep.equal([]);
      expect(code).to.equal(0);
      expect(out.info.mongoTopology).to.have.property('replicaSet');
    });

    it('preflight with an empty import password is yellow, not red', async () => {
      const url = new URL(env.PG_IMPORT_URL!);
      url.password = '';
      const { code, out } = await run(['preflight'], { PG_IMPORT_URL: url.toString() });
      expect(out.red, JSON.stringify(out.red)).to.deep.equal([]);
      expect(out.yellow.map((y: any) => y.check)).to.include('target');
      expect(code).to.equal(0);
    });

    it('status warns when the import login is open and no move is running; not otherwise', async () => {
      await stack.admin((c) => c.query('ALTER ROLE pipeshub_import NOLOGIN'));
      try {
        expect((await run(['status'])).out.warning).to.equal(undefined);
      } finally {
        await stack.admin((c) => c.query('ALTER ROLE pipeshub_import LOGIN'));
      }
      expect((await run(['status'])).out.warning).to.match(/pipeshub_import/);
    });

    it('--allow-clients waits after readonly for writes that began before the fence (grace), then copies', async () => {
      const app = await new MongoClient(mongo.uri, { appName: 'pipeshub-app' }).connect();
      await app.db(DB).command({ ping: 1 });
      try {
        const modes: string[] = [];
        const cs = mongo.client.db(DB).collection<any>('docstore_fence').watch([], { fullDocument: 'updateLookup' });
        cs.on('change', (c: any) => c.fullDocument?.mode && modes.push(c.fullDocument.mode));
        const t0 = Date.now();
        const r = await run(['migrate', '--offline', '--allow-clients'], { MIGRATE_WRITER_GRACE_MS: '1500' });
        await cs.close();
        // draining comes first, so a long write admitted before readonly carries the write deadline
        expect(modes.slice(0, 2)).to.deep.equal(['draining', 'readonly']);
        expect(Date.now() - t0).to.be.least(1500); // no upper bound: host load must not fail it (the default grace alone is 6 s)
        expect(r.out.stage).to.equal('done');
      } finally {
        await app.close();
      }
    });

    it('preflight with the import login shut is yellow, not red (a dry run stays green)', async () => {
      await stack.admin((c) => c.query('ALTER ROLE pipeshub_import NOLOGIN'));
      try {
        const { code, out } = await run(['preflight']);
        expect(out.red, JSON.stringify(out.red)).to.deep.equal([]);
        expect(out.yellow.map((y: any) => y.check)).to.include('target');
        expect(code).to.equal(0);
      } finally {
        await stack.admin((c) => c.query('ALTER ROLE pipeshub_import LOGIN'));
      }
    });

    it('the compose cut: migrate --offline copies, verifies and fences the old store; a second verify is clean', async () => {
      const { code, out } = await run(['migrate', '--offline']);
      if (out.stage !== 'done') process.stderr.write(`${JSON.stringify({ stage: out.stage, global: out.verify?.global?.filter((g: any) => !g.ok), isolation: out.verify?.isolation, bad: out.verify?.collections?.filter((c: any) => c.diffCount || c.countMismatches.length || c.unmappedPaths.length || c.nullInOmitField.length || c.invariantViolations.length).map((c: any) => ({ c: c.collection, d: c.diffs.slice(0, 2), cm: c.countMismatches.slice(0, 2), up: c.unmappedPaths, n: c.nullInOmitField, i: c.invariantViolations })) }, null, 1)}\n`);
      expect(out.message).to.match(/copied and verified/);
      expect(out.stage, JSON.stringify(out.verify?.collections?.filter((c: any) => c.diffCount || c.invariantViolations.length))).to.equal('done');
      expect(code).to.equal(0);
      expect(await fence()).to.equal('retired');
      const j = await jobs();
      expect(j).to.have.length(1);
      expect(j[0].status).to.equal('done');
      const again = await run(['verify']);
      expect(again.code, JSON.stringify(again.out.collections.filter((c: any) => c.diffCount))).to.equal(0);
    });

    it('rollback before any PostgreSQL write returns to MongoDB with nothing lost', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const r = await run(['rollback']);
      expect(r.out.mode).to.equal('no_pg_writes');
      expect(r.code).to.equal(0);
      expect(await fence()).to.equal('active');
    });

    it('rollback --reverse copies PostgreSQL writes back to MongoDB', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      // the application, now on PostgreSQL, renames a user, adds a session and deletes another
      await stack.admin(async (c) => {
        const u = await c.query('SELECT org_id, id FROM identity.users ORDER BY id LIMIT 1');
        await c.query("UPDATE identity.users SET full_name = 'Renamed Afterwards' WHERE id = $1", [u.rows[0].id]);
        await c.query("DELETE FROM conversation.chat_sessions WHERE id = (SELECT id FROM conversation.chat_sessions ORDER BY id LIMIT 1)");
      });
      const refused = await run(['rollback']);
      expect(refused.code).to.equal(1);
      expect(refused.out.mode).to.equal('refused');
      expect(await fence()).to.equal('retired');
      const r = await run(['rollback', '--reverse']);
      process.stderr.write(JSON.stringify({ ok: r.out.ok, msg: r.out.message, notReversed: r.out.notReversed, bad: r.out.verify?.collections?.filter((c: any) => c.diffCount || c.countMismatches.length).map((c: any) => ({ c: c.collection, d: c.diffs.slice(0, 2), cm: c.countMismatches.slice(0, 2) })) }, null, 1) + '\n');
      expect(r.out.ok, r.out.message).to.equal(true);
      expect(r.code).to.equal(0);
      expect(await fence()).to.equal('active');
      const renamed = await mongo.client.db(DB).collection('users').countDocuments({ fullName: 'Renamed Afterwards' });
      expect(renamed).to.equal(1);
    });

    it('reset empties the target and the copy can run again; it refuses once MongoDB is retired', async () => {
      expect((await run(['bulk'])).code).to.equal(0);
      expect((await run(['reset'])).code).to.equal(2);
      expect((await run(['reset', '--yes'])).code).to.equal(0);
      expect(Number((await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM identity.users'))).rows[0].n)).to.equal(0);
      expect(Number((await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM identity.user_credentials'))).rows[0].n)).to.equal(0);
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const refused = await run(['reset', '--yes']);
      expect(refused.code).to.equal(1);
      expect(Number((await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM identity.users'))).rows[0].n)).to.be.greaterThan(0);
    });

    describe('drills', () => {
      it('kill the migrator mid-bulk and resume: no loss, no duplicates', async () => {
        const child: ChildProcess = spawn(process.execPath, ['-r', 'ts-node/register/transpile-only', '-r', 'reflect-metadata', 'src/scripts/docstore-migrate.ts', 'bulk'], {
          env: { ...env, MIGRATE_BATCH_DELAY_MS: '400', MIGRATE_BATCH_SIZE: '1', TS_NODE_PROJECT: 'tsconfig.test.json', TS_NODE_TRANSPILE_ONLY: '1' },
          stdio: 'ignore',
        });
        let killed = false;
        for (let i = 0; i < 300 && !killed; i++) {
          await new Promise((r) => setTimeout(r, 300));
          const j = await jobs();
          const cols = Object.values((j[0]?.progress?.collections ?? {}) as Record<string, any>);
          if (cols.some((c) => c.state === 'running' || c.state === 'done') && cols.reduce((a, c) => a + c.copied, 0) >= 6) {
            child.kill('SIGKILL');
            killed = true;
          }
        }
        expect(killed, 'the migrator was killed while copying').to.equal(true);
        await new Promise((r) => child.once('exit', r));
        const mid = (await jobs())[0];
        expect(mid.status).to.equal('running');
        const before = Object.values(mid.progress.collections as Record<string, any>).reduce((a: number, c: any) => a + c.copied, 0);
        expect(before).to.be.greaterThan(0);
        const resumed = await run(['bulk']);
        expect(resumed.code).to.equal(0);
        const v = await run(['verify']);
        expect(v.code, JSON.stringify(v.out.collections.filter((c: any) => c.diffCount || c.countMismatches.length))).to.equal(0);
        const j = await jobs();
        expect(j).to.have.length(1);
      });

      it('a verify diff aborts the flip and leaves MongoDB the store', async () => {
        expect((await run(['bulk'])).code).to.equal(0);
        await stack.admin((c) => c.query("UPDATE identity.users SET full_name = full_name || ' (tampered)' WHERE id = (SELECT id FROM identity.users ORDER BY id LIMIT 1)"));
        const r = await run(['migrate', '--offline']);
        expect(r.code).to.equal(1);
        expect(r.out.stage).to.equal('verify');
        expect(r.out.verify.collections.find((c: any) => c.collection === 'users').diffCount).to.equal(1);
        expect(await fence()).to.not.equal('retired');
        expect((await jobs())[0].status).to.equal('running');
      });

      it('a wrong organisation stamp is caught by the invariants', async () => {
        expect((await run(['bulk'])).code).to.equal(0);
        await stack.admin((c) => c.query("UPDATE conversation.citations SET org_id = (SELECT org_id FROM identity.orgs WHERE org_id <> conversation.citations.org_id LIMIT 1) WHERE id = (SELECT id FROM conversation.citations ORDER BY id LIMIT 1)"));
        const v = await run(['verify']);
        expect(v.code).to.equal(1);
        const c = v.out.collections.find((x: any) => x.collection === 'citations');
        expect(c.diffCount + c.invariantViolations.length + c.countMismatches.length).to.be.greaterThan(0);
      });

      it('preflight refuses a target that is too small', async () => {
        const r = await run(['preflight'], { MIGRATE_PG_FREE_BYTES: '1000' });
        expect(r.code).to.equal(1);
        expect(r.out.red.map((x: any) => x.check)).to.include('disk');
      });

      it('preflight names a field the mapping does not know', async () => {
        await mongo.client.db(DB).collection('chatSessions').updateOne({}, { $set: { 'x.y': 1 } });
        const r = await run(['preflight']);
        expect(r.code).to.equal(1);
        expect(r.out.red.some((x: any) => x.check === 'path_census' && /chatSessions\.x\.y/.test(x.message))).to.equal(true);
      });

      it('preflight blocks a citation stamped with another organisation than the message that cites it', async () => {
        const msg = await mongo.client.db(DB).collection<any>('chatSessionMessages').findOne({ 'citations.0': { $exists: true } });
        expect(msg, 'the fixtures have a message with a citation').to.not.equal(null);
        await mongo.client.db(DB).collection('citations').updateOne({ _id: msg.citations[0].citationId }, { $set: { 'metadata.orgId': 'fake-org' } });
        const r = await run(['preflight']);
        expect(r.code).to.equal(1);
        expect(r.out.red.map((x: any) => x.check)).to.include('citation_org');
      });

      it('preflight blocks legacy conversations that still hold messages', async () => {
        await mongo.client.db(DB).collection('conversations').insertOne({ messages: [] });
        const r = await run(['preflight']);
        expect(r.out.red.some((x: any) => x.check === 'legacy_conversations')).to.equal(true);
      });

      it('migrate --offline refuses while an application is connected, even an idle one, and not for writes by other tools', async () => {
        // an idle application: a connected client named as the application names its connections, no writes at all
        const app = await new MongoClient(mongo.uri, { appName: 'pipeshub-app' }).connect();
        await app.db(DB).command({ ping: 1 });
        try {
          const r = await run(['migrate', '--offline']);
          expect(r.code).to.equal(1);
          expect(r.out.stage).to.equal('quiesce');
          expect(r.out.message).to.match(/application is still connected/);
          const pre = await run(['preflight']);
          expect(pre.code).to.equal(1);
          expect(pre.out.red.map((x: any) => x.check)).to.include('app_running');
          // a build from before the connections were named, with a name of its own in MONGO_URI: any Node client counts
          await app.close();
          const old = await new MongoClient(mongo.uri, { appName: 'someone-elses-name' }).connect();
          await old.db(DB).command({ ping: 1 });
          try {
            expect((await run(['migrate', '--offline', '--dry-run'])).out.stage).to.equal('quiesce');
          } finally {
            await old.close();
          }
        } finally {
          await app.close().catch(() => undefined);
        }
        // the override: with --allow-clients the connected application is a warning, not a stop
        const again = await new MongoClient(mongo.uri, { appName: 'pipeshub-app' }).connect();
        await again.db(DB).command({ ping: 1 });
        try {
          const forced = await run(['migrate', '--offline', '--dry-run', '--allow-clients']);
          expect(forced.out.stage).to.equal('dry_run');
          expect(forced.out.preflight.yellow.map((x: any) => x.check)).to.include('app_running');
        } finally {
          await again.close();
        }
        // writes by a tool that is not the application are no reason to refuse (and make the check deterministic)
        let go = true;
        const writer = (async () => {
          while (go) {
            await mongo.client.db(DB).collection('scratch').insertOne({ t: Date.now() });
            await new Promise((res) => setTimeout(res, 50));
          }
        })();
        try {
          expect((await run(['migrate', '--offline', '--dry-run'])).out.stage).to.equal('dry_run');
        } finally {
          go = false;
          await writer;
        }
      });

      it('a bug in how the copy reads a value is not hidden from the verifier (shifted dates)', async () => {
        const imp = require('../../../src/libs/repositories/import');
        const real = imp.neutral;
        const shift = (v: unknown): unknown => {
          if (typeof v === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(v)) return new Date(Date.parse(v) + 3600_000).toISOString();
          if (Array.isArray(v)) return v.map(shift);
          if (v && typeof v === 'object' && !Buffer.isBuffer(v)) return Object.fromEntries(Object.entries(v as object).map(([k, x]) => [k, shift(x)]));
          return v;
        };
        imp.neutral = (v: unknown) => shift(real(v));
        let v: { code: number; out: any };
        try {
          // the bug lives in the shared layer, so it is there for the copy and for anything else that uses it
          expect((await run(['bulk'])).code).to.equal(0);
          v = await run(['verify']);
        } finally {
          imp.neutral = real;
        }
        expect(v.code).to.equal(1);
        expect(v.out.collections.some((c: any) => c.diffCount > 0)).to.equal(true);
      });

      it('two unsent outbox events that fold to one table id are named by preflight', async () => {
        const col = mongo.client.db(DB).collection('outbox_events');
        const a = new ObjectId('6ad8b333aaaaaaaaaa000001');
        const b = new ObjectId('6ad8b333bbbbbbbbbb000001'); // same second, same counter, other process bytes
        await col.insertMany([
          { _id: a, topic: 't', key: 'k', value: '{"orgId":"6ad8b333061f2015b22fc6c0"}', status: 'pending', createdAt: new Date(), updatedAt: new Date() },
          { _id: b, topic: 't', key: 'k', value: '{"orgId":"6ad8b333061f2015b22fc6c0"}', status: 'pending', createdAt: new Date(), updatedAt: new Date() },
        ] as never[]);
        const r = await run(['preflight']);
        expect(r.out.red.map((x: any) => x.check)).to.include('outbox_id_collision');
      });

      it('preflight is red when the schema and this build disagree', async () => {
        await stack.admin((c) => c.query("UPDATE kernel.schema_epoch SET min_code_epoch = 99 WHERE branch = 'identity'"));
        try {
          const r = await run(['preflight']);
          expect(r.out.red.map((x: any) => x.check)).to.include('app_version');
        } finally {
          await stack.admin((c) => c.query("UPDATE kernel.schema_epoch SET min_code_epoch = 0 WHERE branch = 'identity'"));
        }
      });
    });

    describe('drain', () => {
      it('never moves the fence of a store that was moved: drain and drain --cancel leave retired and readonly alone', async () => {
        expect((await run(['migrate', '--offline'])).code).to.equal(0);
        expect(await fence()).to.equal('retired');
        const d = await run(['drain', '--wait', '0']);
        expect(d.code).to.equal(1);
        expect(await fence()).to.equal('retired');
        const c = await run(['drain', '--cancel']);
        expect(c.out.changed).to.equal(false);
        expect(await fence()).to.equal('retired');
        // the guards that depend on retired are still armed
        expect((await run(['reset', '--yes'])).code).to.equal(1);
        expect((await run(['migrate', '--offline'])).code).to.equal(1);
        await mongo.client.db(DB).collection<any>('docstore_fence').updateOne({ _id: 'fence' }, { $set: { mode: 'readonly' } });
        expect((await run(['drain', '--wait', '0'])).code).to.equal(1);
        expect((await run(['drain', '--cancel'])).out.changed).to.equal(false);
        expect(await fence()).to.equal('readonly');
      });

      it('pauses briefly after the fence moves, for a turn admitted just before it, before it counts the runs in flight', async () => {
        const t = Date.now();
        const d = await run(['drain', '--wait', '0']);
        expect(d.code).to.equal(0);
        expect(Date.now() - t).to.be.greaterThan(900); // the turn admission reads the fence afresh, so one second covers a turn admitted just before
        expect((await run(['drain', '--cancel'])).out.changed).to.equal(true);
      });

      it('waits for runs in flight, can close them as interrupted, and can be cancelled', async () => {
        const sessions = mongo.client.db(DB).collection('chatSessions');
        await sessions.insertOne({ _id: new (require('mongodb').ObjectId)(), orgId: new (require('mongodb').ObjectId)(), userId: new (require('mongodb').ObjectId)(), initiator: new (require('mongodb').ObjectId)(), status: 'Inprogress', lastActivityAt: Date.now(), activeRunId: 'r1', isDeleted: false, createdAt: new Date(), updatedAt: new Date() } as never);
        const waited = await run(['drain', '--wait', '1']);
        expect(waited.code).to.equal(1);
        expect(waited.out.remaining).to.equal(1);
        expect(await fence()).to.equal('draining');
        const forced = await run(['drain', '--wait', '1', '--force']);
        expect(forced.code).to.equal(0);
        expect(forced.out.stoppedRuns).to.equal(1);
        expect(await sessions.countDocuments({ status: 'Stopped', failReason: 'interrupted' })).to.equal(1);
        const cancelled = await run(['drain', '--cancel']);
        expect(cancelled.code).to.equal(0);
        expect(await fence()).to.equal('active');
      });
    });

    describe('the running application', () => {
      it('takes no new chat turn while the fence says draining, and takes them again after the drain is cancelled', async () => {
        process.env.DOC_STORE_FENCE = 'on'; // the drain check is part of the opt-in fence
        const mongoose = (await import('mongoose')).default;
        const { assertAcceptingRuns, MaintenanceError } = await import('../../../src/libs/repositories/drain');
        await mongoose.connect(mongo.uri, { dbName: DB });
        try {
          expect((await run(['drain', '--wait', '0'])).code).to.equal(0);
          process.env.DOC_STORE = 'mongo'; // the migrator sets postgres for itself
          let err: any;
          try {
            await assertAcceptingRuns();
          } catch (e) {
            err = e;
          }
          expect(err).to.be.instanceOf(MaintenanceError);
          expect(err.metadata.retryAfter).to.equal('120');
          expect((await run(['drain', '--cancel'])).code).to.equal(0);
          process.env.DOC_STORE = 'mongo';
          await new Promise((r) => setTimeout(r, 3200)); // the application re-reads the fence every few seconds
          await assertAcceptingRuns();
        } finally {
          delete process.env.DOC_STORE_FENCE;
          process.env.DOC_STORE = 'postgres';
          await mongoose.disconnect();
        }
      });
    });

    describe('the HTTP path of a turn while draining', () => {
      it('answers 503 with Retry-After for a new chat, a follow-up and a regenerate, before any stream starts', async () => {
        process.env.DOC_STORE_FENCE = 'on'; // the drain check is part of the opt-in fence
        const mongoose = (await import('mongoose')).default;
        const es = await import('../../../src/modules/enterprise_search/controller/es_controller');
        await mongoose.connect(mongo.uri, { dbName: DB });
        try {
          expect((await run(['drain', '--wait', '0'])).code).to.equal(0);
          process.env.DOC_STORE = 'mongo';
          for (const [name, handler] of [['new chat', es.streamChat], ['follow-up', es.addMessageStream], ['regenerate', es.regenerateAnswers], ['agent chat', es.streamAgentConversation], ['agent follow-up', es.addMessageStreamToAgentConversation]] as const) {
            const res: any = { headersSent: false, headers: {} as Record<string, string>, code: 0, body: undefined as unknown };
            res.status = (c: number) => ((res.code = c), res);
            res.set = (k: string, v: string) => ((res.headers[k] = v), res);
            res.json = (b: unknown) => ((res.body = b), res);
            const req: any = { body: { query: 'q' }, query: {}, params: { conversationId: 'c', agentKey: 'a', messageId: 'm' }, context: { requestId: 'r' }, user: { userId: 'u', orgId: 'o' } };
            await (handler as any)({} as never)(req, res);
            expect(res.code, name).to.equal(503);
            expect(res.headers['Retry-After'], name).to.equal('120');
          }
        } finally {
          delete process.env.DOC_STORE_FENCE;
          process.env.DOC_STORE = 'postgres';
          await mongoose.disconnect();
        }
      });
    });

    describe('follow', () => {
      it('catches up inserts, updates and deletes made during the copy', async () => {
        expect((await run(['follow', 'start'])).code).to.equal(0);
        expect((await run(['bulk'])).code).to.equal(0);
        const users = mongo.client.db(DB).collection<any>('users');
        const one = await users.findOne({});
        await users.updateOne({ _id: one._id }, { $set: { fullName: 'Changed During Copy' } });
        const gone = await users.findOne({ _id: { $ne: one._id } });
        await users.deleteOne({ _id: gone._id });
        const f = await run(['follow', 'run', '--until-idle', '3']);
        expect(f.code).to.equal(0);
        expect(f.out.applied).to.be.greaterThan(1);
        const v = await run(['verify', '--only', 'users']);
        expect(v.code, JSON.stringify(v.out.collections[0].diffs)).to.equal(0);
        const name = await stack.admin((c) => c.query('SELECT full_name FROM identity.users WHERE id = $1', [String(one._id)]));
        expect(name.rows[0].full_name).to.equal('Changed During Copy');
      });
    });

    describe('online path', () => {
      const ONLINE = { MIGRATE_BATCH_DELAY_MS: '20', MIGRATE_TEST_HOOKS: '1' };
      const FLAGS = ['--fence-armed', '--shadow-clean', '--drain-wait', '20', '--grace', '0.3'];
      const cut = (extra: string[] = [], e: NodeJS.ProcessEnv = {}) => run(['cutover', ...FLAGS, ...extra], { ...ONLINE, ...e });
      const storeEpoch = async () => (await stack.admin((c) => c.query('SELECT backend, epoch, readonly FROM platform.store_epoch'))).rows[0];

      /** An application stand-in: reads the fence before every write, as the wrapper does, and writes users until the store is read-only. */
      function startWriter() {
        const db = mongo.client.db(DB);
        const users = db.collection<any>('users');
        const fences = db.collection<any>('docstore_fence');
        let go = true;
        let n = 0;
        let refusedAt = 0;
        const added: ObjectId[] = [];
        const done = (async () => {
          const tmpl = (await users.findOne({}))!;
          const ids = (await users.find({}, { projection: { _id: 1 } }).toArray()).map((u) => u._id);
          while (go) {
            const mode = (await fences.findOne({ _id: 'fence' }))?.mode ?? 'active';
            if (mode === 'readonly' || mode === 'retired') {
              refusedAt ||= Date.now();
              await new Promise((r) => setTimeout(r, 20));
              continue;
            }
            refusedAt = 0;
            n += 1;
            if (n % 5 === 0) {
              const { _id, ...rest } = tmpl;
              const id = new ObjectId();
              await users.insertOne({ ...rest, _id: id, email: `w${n}@live.example`, slug: `user-${9000 + n}`, fullName: `Live ${n}` });
              added.push(id);
            } else if (n % 7 === 0 && added.length) {
              await users.deleteOne({ _id: added.shift() });
            } else await users.updateOne({ _id: ids[n % ids.length] }, { $set: { fullName: `Writer ${n}` } });
            await new Promise((r) => setTimeout(r, 5));
          }
        })();
        return { stop: async () => { go = false; await done; }, writes: () => n, refused: () => refusedAt > 0, resume: () => { go = true; } };
      }

      it('copies while the application writes, then cuts over: no lost write, a bounded freeze, MongoDB retired, PostgreSQL the store', async () => {
        const w = startWriter();
        try {
          const start = await run(['migrate', '--online'], ONLINE);
          expect(start.out.ok, JSON.stringify(start.out).slice(0, 600)).to.equal(true);
          expect(start.out.stage).to.equal('online_ready');
          expect(w.writes()).to.be.greaterThan(10);
          const inc = await run(['verify', '--incremental'], ONLINE);
          expect(inc.code, JSON.stringify(inc.out).slice(0, 400)).to.equal(0);
          const r = await cut();
          expect(r.out.ok, JSON.stringify(r.out).slice(0, 900)).to.equal(true);
          expect(r.out.freezeMs).to.be.lessThan(600_000);
          expect(r.out.steps.map((x: any) => x.step.split(' ')[0])).to.deep.equal(['F0', 'F1-F2', 'F3', 'F4', 'F4', 'F5', 'F6', 'F7', 'F8', 'F8']);
          expect(await fence()).to.equal('retired');
          const ep = await storeEpoch();
          expect([ep.backend, ep.readonly]).to.deep.equal(['postgres', false]);
        } finally {
          await w.stop();
        }
        const full = await run(['verify'], ONLINE);
        expect(full.code, JSON.stringify(full.out.collections?.map((c: any) => c.diffs).flat().slice(0, 3))).to.equal(0);
      });

      it('a failed step unfreezes: MongoDB writable again, PostgreSQL still closed, and a second cut succeeds', async () => {
        const w = startWriter();
        try {
          expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
          const failed = await cut(['--fail-at', 'F5']);
          expect(failed.code).to.equal(1);
          expect(failed.out.aborted).to.equal(true);
          expect(failed.out.stage).to.equal('error');
          expect(await fence()).to.equal('active');
          expect((await storeEpoch()).backend).to.equal('mongo');
          const before = w.writes();
          await new Promise((r) => setTimeout(r, 300));
          expect(w.writes(), 'the application writes again').to.be.greaterThan(before);
          const ok = await cut();
          expect(ok.out.ok, JSON.stringify(ok.out).slice(0, 600)).to.equal(true);
        } finally {
          await w.stop();
        }
        expect((await run(['verify'], ONLINE)).code).to.equal(0);
      });

      it('a write that reaches MongoDB after the final verify (a leaked fence) aborts at F8 and unfreezes', async () => {
        expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
        const r = await cut(['--fail-at', 'leak-after-F7']);
        expect(r.code).to.equal(1);
        expect([r.out.aborted, r.out.stage]).to.deep.equal([true, 'F8']);
        expect(await fence()).to.equal('active');
        expect((await storeEpoch()).backend).to.equal('mongo');
      });

      it('the follower raises the lag alarm; and when its resume token has fallen out of the oplog it starts again and compares everything', async () => {
        expect((await run(['follow', 'start'])).code).to.equal(0);
        expect((await run(['bulk'])).code).to.equal(0);
        const users = mongo.client.db(DB).collection<any>('users');
        const one = await users.findOne({});
        await users.updateOne({ _id: one._id }, { $set: { fullName: 'Behind' } });
        await new Promise((r) => setTimeout(r, 1200));
        const lag = await run(['follow', 'run', '--until-idle', '2'], { MIGRATE_LAG_ALARM_MS: '0' });
        expect(lag.out.alarms).to.be.greaterThan(0);
        expect(lag.out.maxLagMs).to.be.greaterThan(1000);
        // now lose the token: the stream refuses to resume (error 286), as it does when the oplog has rolled past it
        const gone = await users.findOne({ _id: { $ne: one._id } });
        await users.deleteOne({ _id: gone._id });
        await users.updateOne({ _id: one._id }, { $set: { fullName: 'After The Token Was Lost' } });
        const real = MongoClient.prototype.watch;
        let lost = 0;
        (MongoClient.prototype as any).watch = function (this: MongoClient, ...a: unknown[]) {
          if (lost++ === 0)
            return { tryNext: async () => { throw Object.assign(new Error('Resume of change stream was not possible, as the resume point may no longer be in the oplog.'), { code: 286 }); }, close: async () => undefined, resumeToken: null };
          return (real as any).apply(this, a);
        };
        try {
          const f = await run(['follow', 'run', '--until-idle', '2']);
          expect(f.code, JSON.stringify(f.out)).to.equal(0);
          expect(f.out.resyncs).to.equal(1);
        } finally {
          MongoClient.prototype.watch = real;
        }
        const name = await stack.admin((c) => c.query('SELECT full_name FROM identity.users WHERE id = $1', [String(one._id)]));
        expect(name.rows[0].full_name).to.equal('After The Token Was Lost');
        expect((await stack.admin((c) => c.query('SELECT 1 FROM identity.users WHERE id = $1', [String(gone._id)]))).rowCount).to.equal(0);
        expect((await run(['verify'])).code).to.equal(0);
      });

      it('after the flip, the reverse follower writes application changes back and rollback --online makes MongoDB the store again', async () => {
        // (the fixture's error metadata has a dotted key, which a Mongoose map cannot hold: such a document cannot be written back through its schema)
        await mongo.client.db(DB).collection('chatSessions').updateMany({}, { $set: { conversationErrors: [] } });
        expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
        const r = await cut();
        expect(r.out.ok, JSON.stringify(r.out).slice(0, 600)).to.equal(true);
        // the application, now on PostgreSQL, changes things (here as the superuser: the triggers log whoever writes)
        const sess = (await stack.admin((c) => c.query('SELECT org_id, session_id FROM conversation.chat_session_members ORDER BY session_id LIMIT 1'))).rows[0];
        const org: string = sess.org_id;
        const sid: string = sess.session_id;
        const person = (await stack.admin((c) => c.query('SELECT id, org_id FROM identity.users ORDER BY id LIMIT 1'))).rows[0];
        const newcomer = new ObjectId().toHexString();       // (members are not foreign keys to users)
        await stack.admin(async (c) => {
          await c.query('UPDATE conversation.chat_session_members SET access_level = $2, pos = pos + 1 WHERE session_id = $1', [sid, 'write']);
          await c.query("INSERT INTO conversation.chat_session_members (org_id, session_id, user_id, access_level, pos, entry_id) VALUES ($1, $2, $3, 'read', 0, $4)", [org, sid, newcomer, new ObjectId().toHexString()]);
          await c.query("UPDATE identity.users SET full_name = 'Back On Mongo' WHERE id = $1", [person.id]);
          await c.query("UPDATE identity.orgs SET registered_name = 'Renamed In PostgreSQL' WHERE org_id = $1", [person.org_id]);
        });
        const rf = await run(['reverse', '--until-idle', '3'], ONLINE);
        expect(rf.code, JSON.stringify(rf.out)).to.equal(0);
        expect(rf.out.applied).to.be.greaterThan(2);
        const m = mongo.client.db(DB);
        const shared = (await m.collection<any>('chatSessions').findOne({ _id: new ObjectId(sid) })).sharedWith.map((x: any) => [String(x.userId), x.accessLevel]);
        expect(shared[0]).to.deep.equal([newcomer, 'read']);                 // the order is the position column's
        expect(shared.slice(1).every((x: any) => x[1] === 'write')).to.equal(true);
        expect((await m.collection<any>('users').findOne({ _id: new ObjectId(person.id) })).fullName).to.equal('Back On Mongo');
        expect((await m.collection<any>('org').findOne({ _id: new ObjectId(person.org_id) })).registeredName).to.equal('Renamed In PostgreSQL');
        // the log is trimmed as it is applied
        expect((await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM platform.docstore_changes'))).rows[0].n).to.equal(0);
        // the way back
        const back = await run(['rollback', '--online'], ONLINE);
        expect(back.code, JSON.stringify({ stage: back.out.stage, msg: back.out.message, bad: back.out.verify?.collections?.filter((c: any) => c.diffCount || c.countMismatches.length || c.invariantViolations.length || c.unmappedPaths.length).map((c: any) => ({ c: c.collection, d: c.diffs.slice(0, 2), cm: c.countMismatches, i: c.invariantViolations })), g: back.out.verify?.global?.filter((g: any) => !g.ok) })).to.equal(0);
        expect(await fence()).to.equal('active');
        const ep = await storeEpoch();
        expect([ep.backend, ep.readonly]).to.deep.equal(['mongo', true]);
        // the move is over: the job ended, the follower's markers are gone, and status says so
        const st = await run(['status']);
        expect(st.out.status).to.equal('done');
        expect((await jobs()).filter((j) => j.status === 'running')).to.deep.equal([]);
        expect((await mongo.client.db(DB).listCollections({ name: 'docstore_migrate_marker' }).toArray()).length).to.equal(0);
      });

      it('the reverse follower loses nothing when transactions commit in another order than their log rows were written (a lower log id commits later)', async () => {
        await mongo.client.db(DB).collection('chatSessions').updateMany({}, { $set: { conversationErrors: [] } });
        expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
        expect((await cut()).out.ok).to.equal(true);
        const rows = (await stack.admin((c) => c.query('SELECT id FROM conversation.chat_sessions ORDER BY id LIMIT 3'))).rows.map((r: any) => r.id as string);
        expect(rows.length).to.equal(3);
        const [x, y, z] = rows;
        const { Client } = await import('pg');
        const open = async () => { const c = new Client({ connectionString: stack.cluster.url('migrate_cli', 'postgres') }); await c.connect(); return c; };
        const b = await open();
        const a = await open();
        try {
          await b.query('BEGIN');
          await b.query("UPDATE conversation.chat_sessions SET title = 'B first' WHERE id = $1", [x]);       // B: the lower transaction id
          await a.query('BEGIN');
          await a.query("UPDATE conversation.chat_sessions SET title = 'A writes' WHERE id = $1", [y]);      // A: its log row is written next ...
          await b.query("UPDATE conversation.chat_sessions SET title = 'B second' WHERE id = $1", [z]);     // ... B's last log row has a higher id than A's
          await b.query('COMMIT');                                                                           // B commits while A is still open
          const m = mongo.client.db(DB).collection<any>('chatSessions');
          expect((await run(['reverse', '--until-idle', '2'], ONLINE)).code).to.equal(0);
          expect([(await m.findOne({ _id: new ObjectId(x) })).title, (await m.findOne({ _id: new ObjectId(z) })).title]).to.deep.equal(['B first', 'B second']);
          expect((await m.findOne({ _id: new ObjectId(y) })).title).to.not.equal('A writes');           // not committed: not written back
          await a.query('COMMIT');
          expect((await run(['reverse', '--until-idle', '2'], ONLINE)).code).to.equal(0);
          expect((await m.findOne({ _id: new ObjectId(y) })).title, 'the change of the transaction that committed last is not lost').to.equal('A writes');
        } finally {
          await a.end().catch(() => undefined);
          await b.end().catch(() => undefined);
        }
      });

      it('the reverse follower writes back a transaction larger than a page, in pages, and alarms when it is late (status shows the lag)', async () => {
        await mongo.client.db(DB).collection('chatSessions').updateMany({}, { $set: { conversationErrors: [] } });
        expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
        expect((await cut()).out.ok).to.equal(true);
        const sess = (await stack.admin((c) => c.query('SELECT id FROM conversation.chat_sessions ORDER BY id LIMIT 2'))).rows.map((r: any) => r.id as string);
        const user = (await stack.admin((c) => c.query('SELECT id FROM identity.users ORDER BY id LIMIT 1'))).rows[0].id as string;
        // one transaction: 2,100 filler log rows (more than the 2,000-row page), then real changes whose log rows come after them
        await stack.admin(async (c) => {
          await c.query('BEGIN');
          await c.query("INSERT INTO platform.docstore_changes (org_id, rel, pk, op, txid) SELECT u.org_id, 'identity.users', u.id, 'U', txid_current() FROM identity.users u, generate_series(1, 2100) WHERE u.id = $1", [user]);
          for (const [i, id] of sess.entries()) await c.query('UPDATE conversation.chat_sessions SET title = $2 WHERE id = $1', [id, `Big transaction ${i}`]);
          await c.query('COMMIT');
        });
        const rf = await run(['reverse', '--until-idle', '2'], { ...ONLINE, MIGRATE_LAG_ALARM_MS: '1' });
        expect(rf.code, JSON.stringify(rf.out)).to.equal(0);
        expect(rf.out.applied).to.be.greaterThan(2100);
        expect(rf.out.alarms, 'late by more than the limit').to.be.greaterThan(0);
        const m = mongo.client.db(DB).collection<any>('chatSessions');
        expect((await Promise.all(sess.map((id) => m.findOne({ _id: new ObjectId(id) })))).map((d) => d.title)).to.deep.equal(['Big transaction 0', 'Big transaction 1']);
        expect((await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM platform.docstore_changes'))).rows[0].n).to.equal(0);
        const st = await run(['status']);
        expect(st.out.progress.reverse.lagMs).to.be.a('number');
      });

      it('slug numbers that repeat across organisations, the counters and a changed e-mail address all come back through the follower', async () => {
        await mongo.client.db(DB).collection('chatSessions').updateMany({}, { $set: { conversationErrors: [] } });
        await mongo.client.db(DB).collection('users').createIndex({ slug: 1 }, { unique: true, name: 'slug_1' });
        expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
        expect((await cut()).out.ok).to.equal(true);
        const two = (await stack.admin((c) => c.query('SELECT org_id, id, slug, email FROM identity.users WHERE slug IS NOT NULL ORDER BY org_id, id'))).rows;
        const a = two.find((u: any) => u.org_id === two[0].org_id)!;
        const b = two.find((u: any) => u.org_id !== a.org_id)!;
        await stack.admin(async (c) => {
          // the same slug in two organisations: legal in PostgreSQL, a duplicate key in the old unique index
          await c.query('UPDATE identity.users SET slug = $2 WHERE id = $1', [b.id, a.slug]);
          await c.query("UPDATE identity.slug_counters SET next_value = next_value + 50");
          await c.query("SELECT nextval('identity.org_slug_seq')");
          await c.query("UPDATE identity.users SET email = 'moved@example.test' WHERE id = $1", [a.id]);
        });
        const rf = await run(['reverse', '--until-idle', '3'], ONLINE);
        expect(rf.code, JSON.stringify(rf.out)).to.equal(0);
        const users = mongo.client.db(DB).collection<any>('users');
        expect((await users.findOne({ _id: new ObjectId(b.id) })).slug).to.equal(a.slug);
        expect(await users.countDocuments({ slug: a.slug })).to.equal(2);
        expect((await users.findOne({ _id: new ObjectId(a.id) })).email).to.equal('moved@example.test');
        expect(await mongo.client.db(DB).collection('userCredentials').countDocuments({ userId: a.id })).to.equal(1);   // (the rollback verify compares the credential row with it)
        const idx = await users.indexes();
        expect(idx.find((x: any) => x.name === 'slug_1')?.unique).to.not.equal(true);
        const ctr = await mongo.client.db(DB).collection<any>('counters').findOne({ name: 'Org' });
        const seq = (await stack.admin((c) => c.query('SELECT last_value::int AS n FROM identity.org_slug_seq'))).rows[0].n;
        expect(ctr.seq).to.be.at.least(seq - 1);
        const back = await run(['rollback', '--online'], ONLINE);
        expect(back.code, JSON.stringify({ stage: back.out.stage, msg: back.out.message, bad: back.out.verify?.collections?.filter((c: any) => c.diffCount).map((c: any) => [c.collection, c.diffs.slice(0, 2)]) })).to.equal(0);
      });

      it('a rollback whose reverse verify fails leaves PostgreSQL the store and writable', async () => {
        expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
        expect((await cut()).out.ok).to.equal(true);
        // a change the follower cannot see (no trigger fires for the import role): MongoDB would come back different
        await stack.admin(async (c) => {
          await c.query("SET ROLE pipeshub_import");
          await c.query("UPDATE identity.users SET full_name = 'Unlogged' WHERE id = (SELECT id FROM identity.users LIMIT 1)");
        });
        const back = await run(['rollback', '--online'], ONLINE);
        expect(back.code).to.equal(1);
        expect(back.out.stage).to.equal('verify');
        expect(await fence()).to.equal('retired');
        expect(await storeEpoch()).to.include({ backend: 'postgres', readonly: false });
      });

      it('a big collection is copied by parallel workers on their own id ranges; a killed run continues the same ranges', async () => {
        const P = { MIGRATE_PARALLEL: '3', MIGRATE_PARALLEL_MIN_DOCS: '2', MIGRATE_BATCH_SIZE: '1' };
        const stopped = await run(['bulk', '--stop-after-batches', '4'], P);
        expect(stopped.code).to.equal(1);
        const mid = (await jobs())[0].progress.collections;
        const ranged = Object.entries(mid).filter(([, c]: [string, any]) => c.ranges);
        expect(ranged.length).to.be.greaterThan(0);
        expect((ranged[0]![1] as any).ranges.length).to.equal(3);
        const rest = await run(['bulk'], P);
        expect(rest.code, JSON.stringify(rest.out)).to.equal(0);
        const done = (await jobs())[0].progress.collections;
        for (const [, c] of Object.entries(done) as Array<[string, any]>) {
          expect(c.state, JSON.stringify(Object.entries(done).filter(([, x]: [string, any]) => x.state !== 'done').map(([n, x]: [string, any]) => [n, x.state, x.last, x.ranges]))).to.equal('done');
          if (c.ranges) expect(c.ranges.every((r: any) => r.done)).to.equal(true);
        }
        expect((await run(['verify'], P)).code).to.equal(0);
      });

      it('credentials are copied in small batches and the newest credential of an account still wins; pending device sign-ins come along', async () => {
        const one = { MIGRATE_BATCH_SIZE: '1' };
        const dup = (await mongo.client.db(DB).collection<any>('userCredentials').findOne({ isDeleted: { $ne: true } }))!;
        await mongo.client.db(DB).collection<any>('userCredentials').insertOne({ ...dup, _id: new ObjectId(), hashedPassword: '$2b$10$newest-wins', updatedAt: new Date(Date.now() + 1000) });
        expect((await run(['bulk'], one)).code).to.equal(0);
        const v = await run(['verify'], one);
        expect(v.code, JSON.stringify(v.out.global?.filter((g: any) => !g.ok))).to.equal(0);
        expect((await stack.admin((c) => c.query('SELECT password_hash FROM identity.user_credentials WHERE user_id = $1', [dup.userId]))).rows[0].password_hash).to.equal('$2b$10$newest-wins');
        expect((await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM oauth.device_authorizations'))).rows[0].n).to.equal(2);
      });

      it('the Slack bot keeps its links in a database of its own: they are copied, followed and verified from there', async () => {
        const SLACK = 'es_slack_test';
        const S = { MONGO_SLACK_DB_NAME: SLACK };
        const main = mongo.client.db(DB).collection<any>('slack_conversations');
        const session = (await mongo.client.db(DB).collection<any>('chatSessions').findOne({}))!;
        // (the fixture's links point at sessions of another group's fixture: point them at one this data holds)
        const links = (await main.find({}).toArray()).map((l) => ({ ...l, conversationId: session._id }));
        expect(links.length).to.equal(2);
        const other = mongo.client.db(SLACK);
        try {
          await other.dropDatabase();
          await other.collection('slack_conversations').insertMany(links);
          await main.drop();
          const start = await run(['migrate', '--online'], { ...ONLINE, ...S });
          expect(start.code, JSON.stringify(start.out).slice(0, 500)).to.equal(0);
          expect((await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM integrations.slack_threads'))).rows[0].n).to.equal(2);
          // a link made after the copy, in the bot's database, reaches PostgreSQL through the stream
          const { _id, ...rest } = links[0];
          _id;
          await other.collection('slack_conversations').insertOne({ ...rest, _id: new ObjectId(), threadId: '1700000000.000900', updatedAt: new Date() });
          expect((await run(['follow', 'run', '--until-idle', '2'], { ...ONLINE, ...S })).code).to.equal(0);
          expect((await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM integrations.slack_threads'))).rows[0].n).to.equal(3);
          const v = await run(['verify'], { ...ONLINE, ...S });
          expect(v.code, JSON.stringify(v.out.collections?.filter((c: any) => c.diffCount).map((c: any) => [c.collection, c.diffs.slice(0, 2)]))).to.equal(0);
        } finally {
          await other.dropDatabase();
        }
      });

      it('shadow reads: MongoDB answers, PostgreSQL is asked the same afterwards, and only a real difference is counted and logged (ids, no content)', async () => {
        expect((await run(['bulk'])).code).to.equal(0);
        const mongoose = require('mongoose');
        const { createPgPool, PgRuntime, configurePg } = require('../../../src/libs/db');
        const { MongoUserRepository } = require('../../../src/libs/repositories/user/mongo');
        const { PgUserRepository } = require('../../../src/libs/repositories/user/pg');
        const { shadowWrap } = require('../../../src/libs/repositories/shadow');
        const { docstoreMetrics } = require('../../../src/libs/repositories/metrics');
        const { Logger } = require('../../../src/libs/services/logger.service');
        // (not MongoService: it would arm the write fence on the driver for the rest of this process)
        await mongoose.connect(mongo.uri, { dbName: DB });
        const pool = createPgPool({ connectionString: env.PG_NODE_URL!, service: 'node', version: 'shadow-test', max: 2 });
        const rt = new PgRuntime({ pool, defaultService: 'node', readOnly: true, pooler: 'pgbouncer_txn' });
        configurePg(rt);
        const counts: Record<string, number> = {};
        const realInc = docstoreMetrics.shadowReads.inc.bind(docstoreMetrics.shadowReads);
        docstoreMetrics.shadowReads.inc = (l: any) => { counts[l.result] = (counts[l.result] ?? 0) + 1; };
        const warned: unknown[] = [];
        const log = Logger.getInstance({ service: 'DocStoreShadow' });
        const realWarn = log.warn.bind(log);
        log.warn = (_m: string, meta: unknown) => { warned.push(meta); };
        const saved = { ...process.env };
        Object.assign(process.env, { DOC_STORE_SHADOW_SAMPLE: '1', DOC_STORE_SHADOW_SETTLE_MS: '50', DOC_STORE_SHADOW_TIMEOUT_MS: '5000' });
        try {
          const users = (await mongo.client.db(DB).collection<any>('users').find({ isDeleted: false }).sort({ _id: 1 }).limit(3).toArray()).map((u) => ({ org_id: String(u.orgId), id: String(u._id) }));
          expect(users.length).to.be.greaterThan(1);
          const shadow = shadowWrap('user', new MongoUserRepository(), () => new PgUserRepository());
          const answers = [];
          // (a request carries its organisation in the context; the shadow read, started inside it, inherits it)
          const inOrg = <T>(u: { org_id: string; id: string }, fn: () => T): T => runWithOrgContext(makeOrgContext({ orgId: u.org_id, userId: u.id, kind: 'user', source: 'jwt', requestId: 'shadow-test' }), fn);
          for (const u of users) answers.push(await inOrg(u, () => shadow.getLive(u.org_id, u.id)));
          answers.forEach((a: any, i) => expect(String(a?._id), 'the caller gets MongoDB\'s answer').to.equal(users[i].id));
          await new Promise((r) => setTimeout(r, 500));
          expect(counts).to.deep.equal({ equal: users.length });
          // PostgreSQL drifts: counted once, logged without the value
          await stack.admin((c) => c.query("UPDATE identity.users SET full_name = 'Secret Drift Name' WHERE id = $1", [users[0].id]));
          await inOrg(users[0], () => shadow.getLive(users[0].org_id, users[0].id));
          await new Promise((r) => setTimeout(r, 600));
          expect(counts.diff).to.equal(1);
          expect(JSON.stringify(warned)).to.not.match(/Secret Drift Name/);
          expect(warned[0]).to.include({ repo: 'user', method: 'getLive' });
          expect((warned[0] as any).ids).to.deep.equal([users[0].id]);
          // the shadow runtime cannot write, whatever the method is called
          let refused = false;
          await inOrg(users[0], () => new PgUserRepository().create(users[0].org_id, { email: 'shadow-write@example.test', fullName: 'x' })).catch(() => (refused = true));
          expect(refused).to.equal(true);
          expect((await stack.admin((c) => c.query("SELECT 1 FROM identity.users WHERE email = 'shadow-write@example.test'"))).rowCount).to.equal(0);
        } finally {
          process.env = saved;
          docstoreMetrics.shadowReads.inc = realInc;
          log.warn = realWarn;
          configurePg(undefined);
          await pool.end();
          await mongoose.disconnect();
        }
      });

      it('two commands saving the job state do not overwrite each other: each keeps what only the other changed', async () => {
        const { JobState } = require('../../../src/scripts/docstore-migrate/state');
        // a process holds one runtime; two commands are two JobState objects, each with its own saved baseline and save queue
        const rt = await MigrationRuntime.open(loadConfig(env));
        try {
          const first = await JobState.attach(rt);      // the follower
          const second = await JobState.attach(rt);     // a verify started a moment later
          first.progress.follow = { resumeToken: { _data: 'one' }, appliedUntil: null, events: 5 };
          await first.checkpoint();
          second.progress.verify = { at: 'now', ok: true, diffs: 0, token: { _data: 'verified' } };
          await second.checkpoint();                    // must not drop `follow`
          first.progress.follow = { resumeToken: { _data: 'two' }, appliedUntil: null, events: 9 };
          await first.checkpoint();                     // must not drop `verify`
          const saved = (await jobs())[0].progress;
          expect(saved.follow.events).to.equal(9);
          expect(saved.verify.token._data).to.equal('verified');
          expect(first.progress.verify?.ok).to.equal(true);       // and the follower has seen it
        } finally {
          await rt.close();
        }
      });

      it('a verify can be sampled, or limited to the documents that changed', async () => {
        expect((await run(['bulk'])).code).to.equal(0);
        const sampled = await run(['verify', '--sample', '0.4']);
        expect(sampled.code).to.equal(0);
        const users = sampled.out.collections.find((c: any) => c.collection === 'users');
        expect(users.comparedDocs).to.be.lessThan(users.sourceDocs + 1);
        // a row changed behind the copy's back is found by the changed-ids verify when its id is named, and not otherwise
        const u = await mongo.client.db(DB).collection<any>('users').findOne({});
        await stack.admin((c) => c.query("UPDATE identity.users SET full_name = 'Tampered' WHERE id = $1", [String(u._id)]));
        const rt = await MigrationRuntime.open(loadConfig(env));
        try {
          const named = await verifyAll(rt, { log: () => undefined, ids: { users: [String(u._id)] } });
          expect(named.collections.find((c) => c.collection === 'users')!.diffCount).to.equal(1);
          const other = await verifyAll(rt, { log: () => undefined, ids: { users: [String(new ObjectId())] } });
          expect(other.collections.find((c) => c.collection === 'users')?.diffCount ?? 0).to.equal(0);
        } finally {
          await rt.close();
        }
      });

      it('following a changed chat keeps its messages (the row is updated in place, not removed and inserted); two users that swap an address in one batch still apply', async () => {
        expect((await run(['follow', 'start'])).code).to.equal(0);
        expect((await run(['bulk'])).code).to.equal(0);
        const db = mongo.client.db(DB);
        const sess = (await db.collection<any>('chatSessionMessages').findOne({}))!;
        const before = (await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM conversation.chat_messages WHERE session_id = $1', [String(sess.sessionId)]))).rows[0].n;
        expect(before).to.be.greaterThan(0);
        await db.collection('chatSessions').updateOne({ _id: sess.sessionId }, { $set: { title: 'Renamed while following' } });
        const [a, b] = await db.collection<any>('users').find({ isDeleted: false }).sort({ _id: 1 }).limit(2).toArray();
        const org = a.orgId;
        const two = await db.collection<any>('users').find({ orgId: org, isDeleted: false }).sort({ _id: 1 }).limit(2).toArray();
        expect(two.length).to.equal(2);
        // swap the two addresses: no order of single updates keeps them unique, one batch does
        await db.collection('users').updateOne({ _id: two[0]._id }, { $set: { email: 'swap-tmp@example.test' } });
        await db.collection('users').updateOne({ _id: two[1]._id }, { $set: { email: two[0].email } });
        await db.collection('users').updateOne({ _id: two[0]._id }, { $set: { email: two[1].email } });
        b;
        const f = await run(['follow', 'run', '--until-idle', '3']);
        expect(f.code, JSON.stringify(f.out)).to.equal(0);
        const after = (await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM conversation.chat_messages WHERE session_id = $1', [String(sess.sessionId)]))).rows[0].n;
        expect(after).to.equal(before);
        expect((await stack.admin((c) => c.query("SELECT title FROM conversation.chat_sessions WHERE id = $1", [String(sess.sessionId)]))).rows[0].title).to.equal('Renamed while following');
        const v = await run(['verify']);
        expect(v.code, JSON.stringify(v.out.collections?.filter((c: any) => c.diffCount).map((c: any) => [c.collection, c.diffs.slice(0, 2)]))).to.equal(0);
      });

      it('a chat started during the copy: its message was listed as skipped, the follower applies it later, and verify compares it instead of calling it extra', async () => {
        expect((await run(['follow', 'start'])).code).to.equal(0);
        expect((await run(['bulk'])).code).to.equal(0);
        const db = mongo.client.db(DB);
        const sess = (await db.collection<any>('chatSessions').findOne({}))!;
        const first = (await db.collection<any>('chatSessionMessages').findOne({ sessionId: sess._id }))!;
        const sid = new ObjectId();
        const mid = new ObjectId();
        await db.collection<any>('chatSessions').insertOne({ ...sess, _id: sid });
        await db.collection<any>('chatSessionMessages').insertOne({ ...first, _id: mid, sessionId: sid });
        const fr = await run(['follow', 'run', '--until-idle', '3']);
        expect(fr.code, JSON.stringify(fr.out).slice(0, 800)).to.equal(0);
        // what the copy would have recorded had it read the message before the chat existed in PostgreSQL
        await stack.admin((c) => c.query(
          `UPDATE platform.jobs SET progress = jsonb_set(progress, '{collections,chatSessionMessages,skippedIds}', COALESCE(progress #> '{collections,chatSessionMessages,skippedIds}', '{}'::jsonb) || jsonb_build_object($1::text, 'message without a session in its organisation'), true) WHERE kind = 'docstore_migration'`,
          [String(mid)]));
        const v = await run(['verify']);
        expect(v.code, JSON.stringify(v.out.collections?.filter((c: any) => c.diffCount).map((c: any) => [c.collection, c.diffs.slice(0, 2)]))).to.equal(0);
      });

      it('credentials followed: a changed password reaches PostgreSQL, a changed address leaves the row alone, a removed account leaves no credential', async () => {
        expect((await run(['follow', 'start'])).code).to.equal(0);
        expect((await run(['bulk'])).code).to.equal(0);
        const db = mongo.client.db(DB);
        const users = db.collection<any>('users');
        // the winning credential of the first three accounts that have a row
        const owners = (await stack.admin((c) => c.query('SELECT user_id FROM identity.user_credentials ORDER BY user_id'))).rows.map((r: { user_id: string }) => r.user_id);
        expect(owners.length).to.be.greaterThan(2);
        const [c1, c2, c3] = await Promise.all(owners.slice(0, 3).map(async (u: string) => (await db.collection<any>('userCredentials').find({ userId: u }).sort({ updatedAt: -1 }).limit(1).toArray())[0]));
        await db.collection('userCredentials').updateOne({ _id: c1._id }, { $set: { hashedPassword: '$2b$10$changed-while-following', wrongCredentialCount: 3, updatedAt: new Date() } });
        await users.updateOne({ _id: new ObjectId(c2.userId) }, { $set: { email: 'moved-address@example.test' } });
        await users.updateOne({ _id: new ObjectId(c3.userId) }, { $set: { isDeleted: true } });
        const f = await run(['follow', 'run', '--until-idle', '3']);
        expect(f.code, JSON.stringify(f.out)).to.equal(0);
        const row = async (userId: string) => (await stack.admin((c) => c.query('SELECT password_hash, wrong_count FROM identity.user_credentials WHERE user_id = $1', [userId]))).rows[0];
        expect((await row(c1.userId)).password_hash).to.equal('$2b$10$changed-while-following');
        expect((await row(c1.userId)).wrong_count).to.equal(3);
        expect((await row(c2.userId)).password_hash).to.equal(c2.hashedPassword);
        expect(await row(c3.userId)).to.equal(undefined);                                  // the removed account's credential is gone
        // the credentials of an account deleted in MongoDB leave no row either
        await db.collection('userCredentials').deleteMany({ userId: c2.userId });
        expect((await run(['follow', 'run', '--until-idle', '3'])).code).to.equal(0);
        expect(await row(c2.userId)).to.equal(undefined);
        const v = await run(['verify']);
        expect(v.code, JSON.stringify(v.out.collections?.filter((c: any) => c.diffCount).map((c: any) => [c.collection, c.diffs.slice(0, 3)]))).to.equal(0);
      });

      it('the pre-check refuses without the attestations or a verified state, and changes nothing', async () => {
        expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
        const r = await run(['cutover', '--drain-wait', '5'], ONLINE);
        expect(r.code).to.equal(1);
        expect(r.out.stage).to.equal('F0');
        expect(r.out.precheck.items.filter((i: any) => !i.ok).map((i: any) => i.name)).to.have.members([
          'every writer carries the Mongo fence (attested)', 'shadow reads showed no difference (attested)']);
        expect((await fence()) ?? 'active').to.equal('active');
        const dry = await cut(['--dry-run']);
        expect(dry.out.stage).to.equal('dry_run');
        expect((await fence()) ?? 'active').to.equal('active');
      });

      it('online start is refused when the MongoDB store is not active', async () => {
        await mongo.client.db(DB).collection('docstore_fence').replaceOne({ _id: 'fence' } as never, { _id: 'fence', mode: 'retired', epoch: 2 } as never, { upsert: true });
        const r = await run(['migrate', '--online'], ONLINE);
        expect(r.code).to.equal(1);
        expect(r.out.stage).to.equal('fence');
      });
    });

    it('the job state is only reachable through the migrate login: neither the import login nor the jobs login can read it', async () => {
      for (const url of [env.PG_IMPORT_URL!, stack.cluster.url('migrate_cli', 'pipeshub_jobs', stack.bouncer.port)]) {
        const pool = new Pool({ connectionString: url });
        let denied = false;
        try {
          await pool.query('SELECT * FROM platform.jobs');
        } catch {
          denied = true;
        }
        await pool.end();
        expect(denied, url.replace(/:[^:@/]*@/, ':***@')).to.equal(true);
      }
    });
  });
}
