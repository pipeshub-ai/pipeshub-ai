/**
 * The two ways to move the store, on a real MongoDB replica set and PostgreSQL: `migrate --offline` (the application stopped) and the
 * online path (`onlineStart`, then `cutover` while the application is still writing). Each has its happy path and a refusal.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { MongoClient } from 'mongodb';
import { PG_BINS, haveStack } from '../../stores/pg-store-harness';
import { readMongoFence } from '../../../src/scripts/docstore-migrate/fence';
import { migrateOffline } from '../../../src/scripts/docstore-migrate/offline';
import { CutoverOptions, cutover, onlineStart } from '../../../src/scripts/docstore-migrate/online';
import { JobState } from '../../../src/scripts/docstore-migrate/state';
import { contentDocs } from './fixtures/content';
import { conversationDocs } from './fixtures/conversation';
import { ADMIN_A, identityDocs } from './fixtures/identity';
import { haveMongod } from './mongo-harness';
import { World, startWorld } from './world';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_switch';
const log = (): void => undefined;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: offline and online switch-over (${bin})`, function () {
    this.timeout(300_000);
    let w: World;
    const hooks = { hooks: process.env.MIGRATE_TEST_HOOKS, opts: process.env.MIGRATE_FENCE_CLIENT_OPTIONS };
    const base = (): Record<string, unknown[]> => ({ ...identityDocs(), ...conversationDocs(), ...contentDocs() });

    before(async () => {
      // the cut waits as long as the application's client could hold a write (two minutes with the usual options); the tests shorten it
      process.env.MIGRATE_TEST_HOOKS = '1';
      process.env.MIGRATE_FENCE_CLIENT_OPTIONS = JSON.stringify({ waitQueueTimeoutMS: 100, connectTimeoutMS: 100, serverSelectionTimeoutMS: 100, retryWrites: true });
      w = await startWorld(bin, 'migrate_switch', DB, { MIGRATE_WRITER_GRACE_MS: '300' });
    });
    after(async () => {
      await w?.stop();
      for (const [k, v] of [['MIGRATE_TEST_HOOKS', hooks.hooks], ['MIGRATE_FENCE_CLIENT_OPTIONS', hooks.opts]] as const)
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
    });
    beforeEach(async () => {
      await w.reseed(base());
      await w.truncate();
      await w.stack.admin((c) => c.query("UPDATE platform.store_epoch SET backend = 'mongo', readonly = true"));
    });

    const sql = async (q: string, p: unknown[] = []): Promise<any[]> => (await w.stack.admin((c) => c.query(q, p))).rows;
    const mode = async (): Promise<string> => (await readMongoFence(w.rt)).mode;
    const store = async (): Promise<{ backend: string; readonly: boolean; epoch: number }> => (await sql('SELECT backend, readonly, epoch FROM platform.store_epoch'))[0];
    const users = (): Promise<number> => w.mongo.client.db(DB).collection('users').countDocuments();
    const pgUsers = async (): Promise<number> => Number((await sql('SELECT count(*)::int AS n FROM identity.users'))[0].n);
    const cut = (extra: Partial<CutoverOptions> = {}): ReturnType<typeof cutover> =>
      cutover(w.rt, { log, dryRun: false, drainWaitMs: 5000, force: false, maxFreezeMs: 120_000, graceMs: 300, lagMaxMs: 60_000, fenceArmed: true, shadowClean: true, ...extra });

    describe('offline', () => {
      it('copies, checks and verifies, then retires MongoDB and makes PostgreSQL the store', async () => {
        const r = await migrateOffline(w.rt, { dryRun: false, allowWriters: false, log });
        expect(r, JSON.stringify(r.verify ?? r.postLoad ?? r.preflight?.red)).to.include({ ok: true, stage: 'done' });
        expect(r.verify!.ok).to.equal(true);
        expect(await pgUsers()).to.equal(await users());
        expect(await mode()).to.equal('retired');
        expect(await store()).to.include({ backend: 'postgres', readonly: false });
        expect(await JobState.current(w.rt), 'the job is finished').to.equal(null);
      });

      it('refuses while an application is connected, and once MongoDB is retired; a dry run copies nothing', async () => {
        const app = await new MongoClient(w.mongo.uri, { appName: 'pipeshub-app' }).connect();
        await app.db(DB).command({ ping: 1 });
        try {
          const r = await migrateOffline(w.rt, { dryRun: false, allowWriters: false, log });
          expect(r).to.include({ ok: false, stage: 'quiesce' });
          expect(r.message).to.match(/application is still connected/);
        } finally {
          await app.close();
        }
        expect(await pgUsers()).to.equal(0);
        expect(await mode()).to.equal('active');
        const dry = await migrateOffline(w.rt, { dryRun: true, allowWriters: false, log });
        expect(dry).to.include({ ok: true, stage: 'dry_run' });
        expect(await pgUsers()).to.equal(0);
        await w.mongo.client.db(DB).collection<any>('docstore_fence').replaceOne({ _id: 'fence' }, { _id: 'fence', mode: 'retired', epoch: 2, at: new Date() }, { upsert: true });
        const retired = await migrateOffline(w.rt, { dryRun: false, allowWriters: false, log });
        expect(retired).to.include({ ok: false, stage: 'fence' });
        expect(await pgUsers()).to.equal(0);
      });
    });

    describe('online', () => {
      it('start refuses a fence that is not active', async () => {
        await w.mongo.client.db(DB).collection<any>('docstore_fence').replaceOne({ _id: 'fence' }, { _id: 'fence', mode: 'draining', epoch: 1, at: new Date() }, { upsert: true });
        const r = await onlineStart(w.rt, { dryRun: false, log });
        expect(r).to.include({ ok: false, stage: 'fence' });
        expect(await pgUsers()).to.equal(0);
      });

      it('copies while the application writes, then cuts over with a final catch-up and a verify', async () => {
        const started = await onlineStart(w.rt, { dryRun: false, log });
        expect(started, JSON.stringify(started.verify)).to.include({ ok: true, stage: 'online_ready' });
        expect(await mode(), 'MongoDB stays writable until the cut').to.equal('active');
        expect(await store()).to.include({ backend: 'mongo', readonly: true });
        // the application writes after the copy; the cut must bring it across
        await w.mongo.client.db(DB).collection('users').updateOne({ _id: ADMIN_A } as never, { $set: { fullName: 'Written Before The Cut' } });
        const epoch = (await store()).epoch;
        const r = await cut();
        expect(r, r.message).to.include({ ok: true, stage: 'done' });
        expect(r.precheck!.ok).to.equal(true);
        expect(r.verify!.changed).to.be.greaterThan(0);
        expect(await mode()).to.equal('retired');
        expect(await store()).to.include({ backend: 'postgres', readonly: false, epoch: epoch + 1 });
        expect((await sql('SELECT full_name FROM identity.users WHERE id = $1', [String(ADMIN_A)]))[0].full_name).to.equal('Written Before The Cut');
        expect(await pgUsers()).to.equal(await users());
      });

      it('refuses at the pre-checks, naming the one that failed, and changes nothing', async () => {
        await onlineStart(w.rt, { dryRun: false, log });
        const r = await cut({ fenceArmed: false });
        expect(r).to.include({ ok: false, stage: 'F0' });
        expect(r.precheck!.items.filter((i) => !i.ok).map((i) => i.name)).to.deep.equal(['every writer carries the Mongo fence (attested)']);
        expect(await mode()).to.equal('active');
        expect(await store()).to.include({ backend: 'mongo', readonly: true });
      });

      it('aborts a failed cut and gives MongoDB back to the application', async () => {
        await onlineStart(w.rt, { dryRun: false, log });
        const r = await cut({ failAt: 'F7' });
        expect(r).to.include({ ok: false, stage: 'error', aborted: true });
        expect(r.message).to.contain('injected failure at F7');
        expect(r.message).to.contain('MongoDB is the store and writable again');
        expect(await mode()).to.equal('active');
        expect(await store()).to.include({ backend: 'mongo', readonly: true });
      });
    });
  });
}
