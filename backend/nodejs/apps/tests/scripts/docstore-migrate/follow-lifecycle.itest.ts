/**
 * The steps that run around the copy, on a real MongoDB replica set and PostgreSQL: following the change stream (a change after the
 * copy, a delete, a restart from the saved position), draining the running turns, resetting the target, and the schema step.
 */
import 'reflect-metadata';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { expect } from 'chai';
import { MongoClient, ObjectId } from 'mongodb';
import { PG_BINS, haveStack } from '../../stores/pg-store-harness';
import { FENCED_APP_NAME } from '../../../src/libs/repositories/fence';
import { bulk } from '../../../src/scripts/docstore-migrate/bulk';
import { cancelDrain, drain } from '../../../src/scripts/docstore-migrate/drain';
import { readMongoFence } from '../../../src/scripts/docstore-migrate/fence';
import { MARKER_COLLECTION, follow, followStart, resync } from '../../../src/scripts/docstore-migrate/follow';
import { reset } from '../../../src/scripts/docstore-migrate/reset';
import { postLoad, runMigrateJob } from '../../../src/scripts/docstore-migrate/schema';
import { JobState, canon } from '../../../src/scripts/docstore-migrate/state';
import { ADMIN_A, ORG_A, ORG_B, SERVICE_A, identityDocs } from './fixtures/identity';

import { haveMongod } from './mongo-harness';
import { World, startWorld } from './world';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_life';
const log = (): void => undefined;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: follow, drain, reset and schema (${bin})`, function () {
    this.timeout(300_000);
    let w: World;

    before(async () => {
      w = await startWorld(bin, 'migrate_life', DB);
    });
    after(async () => {
      await w?.stop();
    });
    beforeEach(async () => {
      await w.reseed(identityDocs());
      await w.truncate();
    });

    const sql = async (q: string, p: unknown[] = []): Promise<any[]> => (await w.stack.admin((c) => c.query(q, p))).rows;
    const name = async (id: unknown): Promise<string | undefined> => (await sql('SELECT full_name FROM identity.users WHERE id = $1', [String(id)]))[0]?.full_name;
    /** Every identity row as text, per table, in a stable order. */
    const snapshot = async (): Promise<Record<string, string[]>> => {
      const out: Record<string, string[]> = {};
      const tables = await sql("SELECT c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'identity' AND c.relkind IN ('r','p') AND NOT c.relispartition ORDER BY 1");
      for (const { t } of tables) out[t] = (await sql(`SELECT to_jsonb(x)::text AS r FROM identity.${t} x`)).map((r) => r.r).sort();
      return out;
    };

    describe('follow', () => {
      it('catches up an update, an insert and a delete made after the copy, then resumes from the saved position', async () => {
        const js = await JobState.attach(w.rt);
        await followStart(w.rt, js);
        await bulk(w.rt, js, { log });
        const users = w.mongo.client.db(DB).collection('users');
        await users.updateOne({ _id: ADMIN_A } as never, { $set: { fullName: 'Changed After Copy' } });
        await users.deleteOne({ _id: SERVICE_A } as never);
        const late = new ObjectId();
        await users.insertOne({ _id: late, slug: 'user-2000', orgId: ORG_A, fullName: 'Late Arrival', email: 'late@acme.example', createdAt: new Date(), updatedAt: new Date(), __v: 0 } as never);
        const first = await follow(w.rt, js, { untilIdleMs: 1500, log });
        expect(first.applied).to.equal(3);
        expect(await name(ADMIN_A)).to.equal('Changed After Copy');
        expect(await name(late)).to.equal('Late Arrival');
        expect(await name(SERVICE_A)).to.equal(undefined);

        // a restart: a new process reads the position from the job row and applies only what came after it
        const saved = js.progress.follow!.resumeToken;
        const again = await JobState.attach(w.rt);
        expect(again.id).to.equal(js.id);
        expect(canon(again.progress.follow!.resumeToken)).to.equal(canon(saved));
        await users.updateOne({ _id: ADMIN_A } as never, { $set: { fullName: 'Changed Again' } });
        const second = await follow(w.rt, again, { untilIdleMs: 1500, log });
        expect(second.applied).to.equal(1);
        expect(await name(ADMIN_A)).to.equal('Changed Again');
        expect(await name(late)).to.equal('Late Arrival');
      });

      it('applies an outbox event and then its delete, though the table keys an event by a number folded from its id', async () => {
        const js = await JobState.attach(w.rt);
        await followStart(w.rt, js);
        await bulk(w.rt, js, { log });
        const events = w.mongo.client.db(DB).collection('outbox_events');
        const _id = new ObjectId();
        await events.insertOne({ _id, topic: 'follow-probe', key: 'k', value: JSON.stringify({ payload: { orgId: String(ORG_A) } }), status: 'pending', createdAt: new Date(), updatedAt: new Date(), __v: 0 } as never);
        await follow(w.rt, js, { untilIdleMs: 1500, log });
        const rows = async (): Promise<number> => Number((await sql("SELECT count(*)::int AS n FROM platform.outbox WHERE topic = 'follow-probe'"))[0].n);
        expect(await rows()).to.equal(1);
        await events.deleteOne({ _id } as never);
        const r = await follow(w.rt, js, { untilIdleMs: 1500, log });
        expect(r.applied).to.equal(1);
        expect(await rows(), 'the delete reached the row').to.equal(0);
      });

      it('does not guess which organisation an outbox delete meant when two hold the same folded id: both rows stay and the clash is named', async () => {
        const js = await JobState.attach(w.rt);
        await followStart(w.rt, js);
        await bulk(w.rt, js, { log });
        const events = w.mongo.client.db(DB).collection('outbox_events');
        // same seconds and same low 21 bits of the counter: one table id, two organisations
        const hexA = (1577836800 + 400_000_000).toString(16).padStart(8, '0') + 'abcdef0123' + '000005';
        const hexB = hexA.slice(0, 18) + '800005';
        const event = (id: string, org: unknown) => ({ _id: new ObjectId(id), topic: 'clash-probe', key: 'k', orderingKey: 'global', value: JSON.stringify({ payload: { orgId: String(org) } }), status: 'pending', attempts: 0, nextAttemptAt: new Date(), createdAt: new Date(), updatedAt: new Date(), __v: 0 });
        await events.insertMany([event(hexA, ORG_A), event(hexB, ORG_B)] as never[]);
        await follow(w.rt, js, { untilIdleMs: 1500, log });
        const rows = async (): Promise<number> => Number((await sql("SELECT count(*)::int AS n FROM platform.outbox WHERE topic = 'clash-probe'"))[0].n);
        expect(await rows()).to.equal(2);
        await events.deleteOne({ _id: new ObjectId(hexA) } as never);
        let err: Error | undefined;
        await follow(w.rt, js, { untilIdleMs: 1500, log }).catch((e: Error) => (err = e));
        expect(err?.message).to.contain('more than one organisation').and.to.contain(String(ORG_A)).and.to.contain(String(ORG_B)).and.to.contain(hexA);
        expect(await rows(), 'neither row was deleted').to.equal(2);
      });

      it('a resync that cannot make the copy level fails, keeps the next run comparing, and finishes when the cause is gone', async () => {
        const events = w.mongo.client.db(DB).collection('outbox_events');
        await events.insertOne({ _id: new ObjectId(), topic: 'resync-probe', key: 'k', orderingKey: `org:${String(ORG_A)}`, value: JSON.stringify({ payload: { orgId: String(ORG_A) } }), status: 'pending', attempts: 0, nextAttemptAt: new Date('2026-10-01T10:00:00.000Z'), createdAt: new Date('2026-10-01T10:00:00.000Z'), updatedAt: new Date('2026-10-01T10:00:00.000Z'), __v: 0 } as never);
        const js = await JobState.attach(w.rt);
        await followStart(w.rt, js);
        await bulk(w.rt, js, { log });
        // an outbox row differing from MongoDB is something the repair does not touch: the compare stays red with nothing written
        await sql("UPDATE platform.outbox SET topic = 'tampered'");
        let err: Error | undefined;
        await resync(w.rt, js, log).catch((e: Error) => (err = e));
        expect(err?.message).to.contain('could not be brought level');
        expect(js.progress.follow!.resyncs ?? 0, 'not reported as done').to.equal(0);
        expect(js.progress.follow!.needsResync).to.equal(true);
        await sql("UPDATE platform.outbox SET topic = 'resync-probe'");
        const r = await follow(w.rt, js, { untilIdleMs: 1000, log });
        expect(r.resyncs, 'the next run compares first').to.equal(1);
        expect(js.progress.follow!.needsResync).to.equal(undefined);
        expect(js.progress.follow!.resyncs).to.equal(1);
      });

      it('refuses to run before a start has saved a position', async () => {
        const js = await JobState.attach(w.rt);
        let err: Error | undefined;
        await follow(w.rt, js, { untilIdleMs: 100, log }).catch((e: Error) => (err = e));
        expect(err?.message).to.contain('run `follow start` before the copy');
      });
    });

    describe('drain', () => {
      const session = async (): Promise<ObjectId> => {
        const _id = new ObjectId();
        await w.mongo.client.db(DB).collection('chatSessions').insertOne({ _id, orgId: ORG_A, userId: ADMIN_A, initiator: ADMIN_A, status: 'Inprogress', lastActivityAt: Date.now(), activeRunId: 'r1', isDeleted: false, createdAt: new Date(), updatedAt: new Date() } as never);
        return _id;
      };

      it('waits for the turn in flight to finish, then reports clean with the fence draining; cancelling reopens it', async () => {
        const _id = await session();
        setTimeout(() => void w.mongo.client.db(DB).collection('chatSessions').updateOne({ _id } as never, { $set: { status: 'Completed' } }), 400);
        const d = await drain(w.rt, { waitMs: 10_000, force: false, log, pollMs: 100, settleMs: 50 });
        expect(d).to.include({ ok: true, remaining: 0, forced: false, stoppedRuns: 0, fence: 'draining' });
        expect(d.waitedMs).to.be.at.least(350);
        expect((await readMongoFence(w.rt)).mode).to.equal('draining');
        expect(await cancelDrain(w.rt)).to.equal(true);
        expect((await readMongoFence(w.rt)).mode).to.equal('active');
        expect(await cancelDrain(w.rt), 'nothing left to cancel').to.equal(false);
      });

      it('gives up at its limit with the turn still running; forced, it closes the turn as interrupted', async () => {
        const _id = await session();
        const waited = await drain(w.rt, { waitMs: 300, force: false, log, pollMs: 50, settleMs: 10 });
        expect(waited).to.include({ ok: false, remaining: 1, forced: false });
        const forced = await drain(w.rt, { waitMs: 300, force: true, log, pollMs: 50, settleMs: 10 });
        expect(forced).to.include({ ok: true, stoppedRuns: 1, forced: true });
        expect(await w.mongo.client.db(DB).collection('chatSessions').findOne({ _id } as never, { projection: { status: 1, failReason: 1 } })).to.include({ status: 'Stopped', failReason: 'interrupted' });
        await cancelDrain(w.rt);
      });

      it('does not drain while a connected application client does not carry the fence, unless told they all do', async () => {
        const unarmed = await new MongoClient(w.mongo.uri, { appName: 'pipeshub-app' }).connect();
        await unarmed.db(DB).command({ ping: 1 });
        try {
          const refused = await drain(w.rt, { waitMs: 0, force: false, log, settleMs: 0 });
          expect(refused).to.include({ ok: false, fence: 'active' });
          expect(refused.message).to.contain('do not carry the Mongo fence');
          expect((await readMongoFence(w.rt)).mode, 'the fence did not move').to.equal('active');
          const told = await drain(w.rt, { waitMs: 0, force: false, log, settleMs: 0, confirmArmed: true });
          expect(told).to.include({ ok: true, fence: 'draining' });
          await cancelDrain(w.rt);
        } finally {
          await unarmed.close();
        }
        const armed = await new MongoClient(w.mongo.uri, { appName: FENCED_APP_NAME }).connect();
        await armed.db(DB).command({ ping: 1 });
        try {
          expect(await drain(w.rt, { waitMs: 0, force: false, log, settleMs: 0 })).to.include({ ok: true, fence: 'draining' });
          await cancelDrain(w.rt);
        } finally {
          await armed.close();
        }
      });

      it('does not move the fence of a store that was already moved', async () => {
        await w.mongo.client.db(DB).collection<any>('docstore_fence').replaceOne({ _id: 'fence' }, { _id: 'fence', mode: 'readonly', epoch: 2, at: new Date() }, { upsert: true });
        const d = await drain(w.rt, { waitMs: 0, force: false, log, settleMs: 0 });
        expect(d).to.include({ ok: false, fence: 'readonly' });
        expect(await cancelDrain(w.rt)).to.equal(false);
        expect((await readMongoFence(w.rt)).mode).to.equal('readonly');
      });
    });

    describe('reset', () => {
      it('empties the target and cancels the job, and the copy then gives the same rows as before', async () => {
        const js = await JobState.attach(w.rt);
        await bulk(w.rt, js, { log });
        const before = await snapshot();
        expect(before.users!.length).to.be.greaterThan(0);
        await w.mongo.client.db(DB).collection(MARKER_COLLECTION).insertOne({ _id: 'left-over', at: new Date() } as never);
        const r = await reset(w.rt, { force: false });
        expect(r.ok).to.equal(true);
        expect(r.tables).to.be.greaterThan(5);
        for (const rows of Object.values(await snapshot())) expect(rows).to.deep.equal([]);
        expect(await JobState.current(w.rt), 'the job was cancelled').to.equal(null);
        expect((await sql("SELECT status FROM platform.jobs WHERE id = $1", [js.id]))[0].status).to.equal('cancelled');
        expect(await w.mongo.client.db(DB).listCollections({ name: MARKER_COLLECTION }).toArray()).to.have.length(0);
        const fresh = await JobState.attach(w.rt);
        expect(fresh.id).to.not.equal(js.id);
        await bulk(w.rt, fresh, { log });
        expect(await snapshot()).to.deep.equal(before);
      });

      it('run during a copy, stops the copy and leaves the target empty: nothing is written after the rows are removed', async () => {
        const js = await JobState.attach(w.rt);
        Object.assign(w.rt.cfg, { batchDelayMs: 1500 });
        try {
          const copy = bulk(w.rt, js, { log }).then(() => 'finished', (e: Error) => e.message);
          for (let i = 0; i < 100 && Number((await sql('SELECT count(*)::int AS n FROM identity.users'))[0].n) === 0; i++) await new Promise((r) => setTimeout(r, 50));
          expect(Number((await sql('SELECT count(*)::int AS n FROM identity.users'))[0].n), 'the copy is part way through the users').to.be.greaterThan(0);
          expect((await reset(w.rt, { force: false })).ok).to.equal(true);
          expect(await copy, 'the copy was stopped, not finished').to.contain('no longer running');
          for (const [t, rows] of Object.entries(await snapshot())) expect(rows, t).to.deep.equal([]);
        } finally {
          Object.assign(w.rt.cfg, { batchDelayMs: 0 });
        }
      });

      it('refuses once MongoDB is marked retired, and goes ahead when forced', async () => {
        const js = await JobState.attach(w.rt);
        await bulk(w.rt, js, { log });
        await w.mongo.client.db(DB).collection<any>('docstore_fence').replaceOne({ _id: 'fence' }, { _id: 'fence', mode: 'retired', epoch: 2, at: new Date() }, { upsert: true });
        const refused = await reset(w.rt, { force: false });
        expect(refused.ok).to.equal(false);
        expect(refused.message).to.contain('MongoDB is marked retired');
        expect(Number((await sql('SELECT count(*)::int AS n FROM identity.users'))[0].n)).to.be.greaterThan(0);
        expect((await reset(w.rt, { force: true })).ok).to.equal(true);
        expect(Number((await sql('SELECT count(*)::int AS n FROM identity.users'))[0].n)).to.equal(0);
      });
    });

    describe('schema', () => {
      const withCommand = (cmd: string) => ({ cfg: { migrateCommand: cmd, pgMigrateUrl: w.env.PG_MIGRATE_URL! } }) as never;

      it('runs no command when none is set, runs one when set, and says why one failed', async () => {
        expect(await runMigrateJob(withCommand('none'), log)).to.equal(false);
        expect(await runMigrateJob(withCommand(' '), log)).to.equal(false);
        expect(await runMigrateJob(withCommand('true'), log)).to.equal(true);
        let err: Error | undefined;
        await runMigrateJob(withCommand('echo broken >&2; exit 3'), log).catch((e: Error) => (err = e));
        expect(err?.message).to.contain('exited with 3').and.to.contain('broken');
      });

      it('the real migrate job builds the tables on an empty database, and a second run changes nothing', async () => {
        const python = process.env.PG_TEST_MIGRATE_PYTHON!;
        const backend = join(__dirname, '../../../../..');
        const cluster = w.stack.cluster;
        const clean = 'migrate_life_clean';
        await cluster.admin((c) => c.query(`CREATE DATABASE ${clean}`));
        // the roles and the extension need a superuser: the administrator's step, not the migrator's
        execFileSync(python, ['-m', 'db.bootstrap_roles'], {
          cwd: backend,
          stdio: 'pipe',
          env: { ...process.env, PYTHONPATH: backend, PG_ADMIN_URL: `postgresql://postgres@127.0.0.1:${cluster.port}/${clean}`, PG_BOOTSTRAP_EXTENSIONS: 'pg_trgm' },
        });
        const saved = { cwd: process.env.MIGRATE_SCHEMA_CWD, py: process.env.PYTHONPATH };
        process.env.MIGRATE_SCHEMA_CWD = backend;
        process.env.PYTHONPATH = backend;
        const rt = { cfg: { migrateCommand: `${python} -m db.migrate`, pgMigrateUrl: cluster.url(clean, 'pipeshub_migrate') } } as never;
        const tables = async (): Promise<string[]> =>
          (await cluster.admin((c) => c.query("SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname IN ('identity','conversation','content','oauth','platform','integrations') ORDER BY 1"), clean)).rows.map((r: { t: string }) => r.t);
        try {
          expect(await tables()).to.deep.equal([]);
          // the surface check wants the import login shut, as it is outside a move
          await cluster.admin((c) => c.query('ALTER ROLE pipeshub_import NOLOGIN'));
          expect(await runMigrateJob(rt, log)).to.equal(true);
          const built = await tables();
          for (const t of ['identity.users', 'identity.user_credentials', 'conversation.chat_sessions', 'content.documents', 'oauth.apps', 'platform.outbox', 'integrations.slack_threads'])
            expect(built, t).to.include(t);
          expect(await runMigrateJob(rt, log), 'a second run').to.equal(true);
          expect(await tables()).to.deep.equal(built);
        } finally {
          await cluster.admin((c) => c.query('ALTER ROLE pipeshub_import LOGIN'));
          for (const [k, v] of [['MIGRATE_SCHEMA_CWD', saved.cwd], ['PYTHONPATH', saved.py]] as const)
            if (v === undefined) delete process.env[k];
            else process.env[k] = v;
        }
      });

      it('post-load names a constraint that was left unvalidated', async () => {
        await sql('ALTER TABLE identity.org_logos ADD CONSTRAINT nv_probe CHECK (true) NOT VALID');
        try {
          const report = await postLoad(w.rt, log);
          expect(report.ok).to.equal(false);
          expect(report.problems).to.include('constraint nv_probe on identity.org_logos is not validated');
        } finally {
          await sql('ALTER TABLE identity.org_logos DROP CONSTRAINT nv_probe');
        }
        expect((await postLoad(w.rt, log)).ok).to.equal(true);
      });
    });
  });
}
