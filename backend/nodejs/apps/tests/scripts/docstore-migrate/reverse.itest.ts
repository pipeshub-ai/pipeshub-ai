/**
 * The way back, through the command line, on real servers: rollback after an offline move (nothing written to PostgreSQL, or writes
 * copied back), the reverse follower after an online cut, `rollback --online`, and a rollback whose verify fails.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { MongoClient, ObjectId } from 'mongodb';
import { PG_BINS, haveStack } from '../../stores/pg-store-harness';
import { runCommand } from './cli-run';
import { contentDocs } from './fixtures/content';
import { conversationDocs } from './fixtures/conversation';
import { MEMBER_A, identityDocs } from './fixtures/identity';
import { JobState } from '../../../src/scripts/docstore-migrate/state';
import { reverseFollow } from '../../../src/scripts/docstore-migrate/reverse-follow';
import { reverseSpecial } from '../../../src/scripts/docstore-migrate/reverse-special';
import { rollback } from '../../../src/scripts/docstore-migrate/reverse';
import { rollbackOnline } from '../../../src/scripts/docstore-migrate/reverse-follow';
import { haveMongod } from './mongo-harness';
import { World, startWorld } from './world';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_reverse';
const ONLINE = { MIGRATE_BATCH_DELAY_MS: '20', MIGRATE_TEST_HOOKS: '1' };
const FLAGS = ['--fence-armed', '--shadow-clean', '--drain-wait', '20', '--grace', '0.3'];

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: rollback and the reverse copy (${bin})`, function () {
    this.timeout(600_000);
    let w: World;
    const hooks = { hooks: process.env.MIGRATE_TEST_HOOKS, opts: process.env.MIGRATE_FENCE_CLIENT_OPTIONS };
    const base = (): Record<string, unknown[]> => ({ ...identityDocs(), ...conversationDocs(), ...contentDocs() });

    before(async () => {
      // the cut waits as long as the application's client could hold a write (two minutes with the usual options); the tests shorten it
      process.env.MIGRATE_TEST_HOOKS = '1';
      process.env.MIGRATE_FENCE_CLIENT_OPTIONS = JSON.stringify({ waitQueueTimeoutMS: 100, connectTimeoutMS: 100, serverSelectionTimeoutMS: 100, retryWrites: true });
      w = await startWorld(bin, 'migrate_reverse', DB);
    });
    after(async () => {
      await w?.stop();
      for (const [k, v] of [['MIGRATE_TEST_HOOKS', hooks.hooks], ['MIGRATE_FENCE_CLIENT_OPTIONS', hooks.opts]] as const)
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
    });
    beforeEach(async () => {
      await w.reseed(base());
      // (the fixture's error metadata has a dotted key, which a Mongoose map cannot hold: such a document cannot be written back through its schema)
      await w.mongo.client.db(DB).collection('chatSessions').updateMany({}, { $set: { conversationErrors: [] } });
      await w.truncate();
      await w.stack.admin((c) => c.query("UPDATE platform.store_epoch SET backend = 'mongo', readonly = true"));
    });

    const run = (args: string[], extra: NodeJS.ProcessEnv = {}) => runCommand(w, args, extra);
    const sql = async (q: string, p: unknown[] = []): Promise<any[]> => (await w.stack.admin((c) => c.query(q, p))).rows;
    const fence = async (): Promise<string | undefined> => (await w.mongo.client.db(DB).collection<any>('docstore_fence').findOne({ _id: 'fence' }))?.mode;
    const storeEpoch = async (): Promise<any> => (await sql('SELECT backend, epoch, readonly FROM platform.store_epoch'))[0];
    const cut = (): ReturnType<typeof run> => run(['cutover', ...FLAGS], ONLINE);

    it('rollback before any PostgreSQL write returns to MongoDB with nothing lost', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const r = await run(['rollback']);
      expect(r.out.mode).to.equal('no_pg_writes');
      expect(r.code).to.equal(0);
      expect(await fence()).to.equal('active');
    });

    it('rollback --reverse copies PostgreSQL writes back to MongoDB; plain rollback refuses once there are some', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      // the application, now on PostgreSQL, renames a user and deletes a session
      const u = (await sql('SELECT org_id, id FROM identity.users ORDER BY id LIMIT 1'))[0];
      await sql("UPDATE identity.users SET full_name = 'Renamed Afterwards' WHERE id = $1", [u.id]);
      await sql('DELETE FROM conversation.chat_sessions WHERE id = (SELECT id FROM conversation.chat_sessions ORDER BY id LIMIT 1)');
      const refused = await run(['rollback']);
      expect(refused.code).to.equal(1);
      expect(refused.out.mode).to.equal('refused');
      expect(await fence()).to.equal('retired');
      expect(await storeEpoch(), 'PostgreSQL is writable again after a refusal').to.include({ backend: 'postgres', readonly: false });
      const r = await run(['rollback', '--reverse']);
      expect(r.out.ok, r.out.message).to.equal(true);
      expect(r.code).to.equal(0);
      expect(await fence()).to.equal('active');
      expect(await w.mongo.client.db(DB).collection('users').countDocuments({ fullName: 'Renamed Afterwards' })).to.equal(1);
    });

    it('after the cut, the reverse follower writes application changes back and rollback --online makes MongoDB the store again', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      const r = await cut();
      expect(r.out.ok, JSON.stringify(r.out).slice(0, 600)).to.equal(true);
      // the application, now on PostgreSQL, changes things (here as the superuser: the triggers log whoever writes)
      const sess = (await sql('SELECT org_id, session_id FROM conversation.chat_session_members ORDER BY session_id LIMIT 1'))[0];
      const person = (await sql('SELECT id, org_id FROM identity.users ORDER BY id LIMIT 1'))[0];
      const newcomer = new ObjectId().toHexString(); // (members are not foreign keys to users)
      await sql('UPDATE conversation.chat_session_members SET access_level = $2, pos = pos + 1 WHERE session_id = $1', [sess.session_id, 'write']);
      await sql("INSERT INTO conversation.chat_session_members (org_id, session_id, user_id, access_level, pos, entry_id) VALUES ($1, $2, $3, 'read', 0, $4)", [sess.org_id, sess.session_id, newcomer, new ObjectId().toHexString()]);
      await sql("UPDATE identity.users SET full_name = 'Back On Mongo' WHERE id = $1", [person.id]);
      await sql("UPDATE identity.orgs SET registered_name = 'Renamed In PostgreSQL' WHERE org_id = $1", [person.org_id]);
      const rf = await run(['reverse', '--until-idle', '3'], ONLINE);
      expect(rf.code, JSON.stringify(rf.out)).to.equal(0);
      expect(rf.out.applied).to.be.greaterThan(2);
      const m = w.mongo.client.db(DB);
      const shared = (await m.collection<any>('chatSessions').findOne({ _id: new ObjectId(sess.session_id) })).sharedWith.map((x: any) => [String(x.userId), x.accessLevel]);
      expect(shared[0]).to.deep.equal([newcomer, 'read']); // the order is the position column's
      expect((await m.collection<any>('users').findOne({ _id: new ObjectId(person.id) })).fullName).to.equal('Back On Mongo');
      expect((await m.collection<any>('org').findOne({ _id: new ObjectId(person.org_id) })).registeredName).to.equal('Renamed In PostgreSQL');
      expect((await sql('SELECT count(*)::int AS n FROM platform.docstore_changes'))[0].n, 'the log is trimmed as it is applied').to.equal(0);
      const back = await run(['rollback', '--online'], ONLINE);
      expect(back.code, JSON.stringify({ stage: back.out.stage, msg: back.out.message })).to.equal(0);
      expect(await fence()).to.equal('active');
      expect(await storeEpoch()).to.include({ backend: 'mongo', readonly: true });
      // the move is over: the job ended, the follower's markers are gone, and status says so
      expect((await run(['status'])).out.status).to.equal('done');
      expect(await sql("SELECT id FROM platform.jobs WHERE status = 'running'")).to.deep.equal([]);
      expect(await m.listCollections({ name: 'docstore_migrate_marker' }).toArray()).to.have.length(0);
    });

    it('the reverse follower loses nothing when transactions commit in another order than their log rows were written (a lower log id commits later)', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const rows = (await sql('SELECT id FROM conversation.chat_sessions ORDER BY id LIMIT 3')).map((r: any) => r.id as string);
      expect(rows.length).to.equal(3);
      const [x, y, z] = rows;
      const { Client } = await import('pg');
      const open = async () => {
        const c = new Client({ connectionString: w.stack.cluster.url('migrate_reverse', 'postgres') });
        await c.connect();
        return c;
      };
      const b = await open();
      const a = await open();
      try {
        await b.query('BEGIN');
        await b.query("UPDATE conversation.chat_sessions SET title = 'B first' WHERE id = $1", [x]); // B: the lower transaction id
        await a.query('BEGIN');
        await a.query("UPDATE conversation.chat_sessions SET title = 'A writes' WHERE id = $1", [y]); // A: its log row is written next ...
        await b.query("UPDATE conversation.chat_sessions SET title = 'B second' WHERE id = $1", [z]); // ... B's last log row has a higher id than A's
        await b.query('COMMIT'); // B commits while A is still open
        const m = w.mongo.client.db(DB).collection<any>('chatSessions');
        expect((await run(['reverse', '--until-idle', '2'], ONLINE)).code).to.equal(0);
        expect([(await m.findOne({ _id: new ObjectId(x) })).title, (await m.findOne({ _id: new ObjectId(z) })).title]).to.deep.equal(['B first', 'B second']);
        expect((await m.findOne({ _id: new ObjectId(y) })).title).to.not.equal('A writes'); // not committed: not written back
        await a.query('COMMIT');
        expect((await run(['reverse', '--until-idle', '2'], ONLINE)).code).to.equal(0);
        expect((await m.findOne({ _id: new ObjectId(y) })).title, 'the change of the transaction that committed last is not lost').to.equal('A writes');
      } finally {
        await a.end().catch(() => undefined);
        await b.end().catch(() => undefined);
      }
    });

    it('the reverse follower writes back a transaction larger than a page, in pages, and alarms when it is late (status shows the lag)', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const sess = (await sql('SELECT id FROM conversation.chat_sessions ORDER BY id LIMIT 2')).map((r: any) => r.id as string);
      const user = (await sql('SELECT id FROM identity.users ORDER BY id LIMIT 1'))[0].id as string;
      // one transaction: 2,100 filler log rows (more than the 2,000-row page), then real changes whose log rows come after them
      await w.stack.admin(async (c) => {
        await c.query('BEGIN');
        await c.query("INSERT INTO platform.docstore_changes (org_id, rel, pk, op, txid) SELECT u.org_id, 'identity.users', u.id, 'U', txid_current() FROM identity.users u, generate_series(1, 2100) WHERE u.id = $1", [user]);
        for (const [i, id] of sess.entries()) await c.query('UPDATE conversation.chat_sessions SET title = $2 WHERE id = $1', [id, `Big transaction ${i}`]);
        await c.query('COMMIT');
      });
      const rf = await run(['reverse', '--until-idle', '2'], { ...ONLINE, MIGRATE_LAG_ALARM_MS: '1' });
      expect(rf.code, JSON.stringify(rf.out)).to.equal(0);
      expect(rf.out.applied).to.be.greaterThan(2100);
      expect(rf.out.alarms, 'late by more than the limit').to.be.greaterThan(0);
      const m = w.mongo.client.db(DB).collection<any>('chatSessions');
      expect((await Promise.all(sess.map((id) => m.findOne({ _id: new ObjectId(id) })))).map((d) => d.title)).to.deep.equal(['Big transaction 0', 'Big transaction 1']);
      expect((await sql('SELECT count(*)::int AS n FROM platform.docstore_changes'))[0].n).to.equal(0);
      expect((await run(['status'])).out.progress.reverse.lagMs).to.be.a('number');
    });

    it('slug numbers that repeat across organisations, the counters and a changed e-mail address all come back through the follower', async () => {
      await w.mongo.client.db(DB).collection('users').createIndex({ slug: 1 }, { unique: true, name: 'slug_1' });
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const two = await sql('SELECT org_id, id, slug, email FROM identity.users WHERE slug IS NOT NULL ORDER BY org_id, id');
      const a = two.find((u: any) => u.org_id === two[0].org_id)!;
      const b = two.find((u: any) => u.org_id !== a.org_id)!;
      // the same slug in two organisations: legal in PostgreSQL, a duplicate key in the old unique index
      await sql('UPDATE identity.users SET slug = $2 WHERE id = $1', [b.id, a.slug]);
      await sql('UPDATE identity.slug_counters SET next_value = next_value + 50');
      await sql("SELECT nextval('identity.org_slug_seq')");
      await sql("UPDATE identity.users SET email = 'moved@example.test' WHERE id = $1", [a.id]);
      const rf = await run(['reverse', '--until-idle', '3'], ONLINE);
      expect(rf.code, JSON.stringify(rf.out)).to.equal(0);
      const users = w.mongo.client.db(DB).collection<any>('users');
      expect((await users.findOne({ _id: new ObjectId(b.id) })).slug).to.equal(a.slug);
      expect(await users.countDocuments({ slug: a.slug })).to.equal(2);
      expect((await users.findOne({ _id: new ObjectId(a.id) })).email).to.equal('moved@example.test');
      expect(await w.mongo.client.db(DB).collection('userCredentials').countDocuments({ userId: a.id }), 'the rollback verify compares the credential row with it').to.equal(1);
      expect((await users.indexes()).find((x: any) => x.name === 'slug_1')?.unique).to.not.equal(true);
      const ctr = await w.mongo.client.db(DB).collection<any>('counters').findOne({ name: 'Org' });
      const seq = (await sql('SELECT last_value::int AS n FROM identity.org_slug_seq'))[0].n;
      expect(ctr.seq).to.be.at.least(seq - 1);
      const back = await run(['rollback', '--online'], ONLINE);
      expect(back.code, JSON.stringify({ stage: back.out.stage, msg: back.out.message })).to.equal(0);
    });

    it('rollback refuses while an application is connected, and changes nothing', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const app = await new MongoClient(w.mongo.uri, { appName: 'pipeshub-app' }).connect();
      await app.db(DB).command({ ping: 1 });
      try {
        const r = await run(['rollback']);
        expect(r.code).to.equal(1);
        expect(r.out.mode).to.equal('refused');
        expect(r.out.message).to.match(/application is still connected/);
        expect(await fence()).to.equal('retired');
      } finally {
        await app.close();
      }
    });

    it('rollback --online refuses a store that was not moved by an online cut', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      const r = await run(['rollback', '--online'], ONLINE);
      expect(r.code).to.equal(1);
      expect(r.out.stage).to.equal('fence');
      expect([undefined, 'active'], 'MongoDB stays the store').to.include(await fence());
    });

    it('rollback --online that runs out of its freeze budget opens PostgreSQL again and changes nothing else', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const back = await run(['rollback', '--online', '--max-freeze', '0'], ONLINE);
      expect(back.code).to.equal(1);
      expect(back.out.ok).to.equal(false);
      expect(await fence()).to.equal('retired');
      expect(await storeEpoch()).to.include({ backend: 'postgres', readonly: false });
    });

    it('rollback --online starts no job when it refuses, and finishes the one it started when the freeze fails', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const running = (): Promise<any[]> => sql("SELECT id FROM platform.jobs WHERE status = 'running'");
      expect(await running()).to.deep.equal([]);
      await sql("UPDATE platform.store_epoch SET backend = 'mongo'");
      expect((await rollbackOnline(w.rt, { log: () => undefined, maxFreezeMs: 1000 })).stage).to.equal('fence');
      expect(await running(), 'a refusal leaves no running job').to.deep.equal([]);
      await sql("UPDATE platform.store_epoch SET backend = 'postgres'");
      process.env.MIGRATE_TEST_HOOKS = '1';
      try {
        const f = await rollbackOnline(w.rt, { log: () => undefined, maxFreezeMs: 1000, failAt: 'freeze' });
        expect(f.stage).to.equal('freeze');
      } finally {
        process.env.MIGRATE_TEST_HOOKS = '1'; // the suite runs with the hooks on
      }
      expect(await running(), 'the job this call started is finished').to.deep.equal([]);
      expect((await sql("SELECT status FROM platform.jobs ORDER BY id DESC LIMIT 1"))[0].status).to.equal('failed');
    });

    it('a failed online rollback says PostgreSQL is read-only again when it was read-only before', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      await sql('UPDATE platform.store_epoch SET readonly = true');
      const r = await rollbackOnline(w.rt, { log: () => undefined, maxFreezeMs: 0 });
      expect(r.stage).to.equal('budget');
      expect(r.message).to.contain('read-only again').and.not.to.contain('writable');
      expect(await storeEpoch()).to.include({ readonly: true });
    });

    const withHooks = async <T>(f: () => Promise<T>): Promise<T> => {
      process.env.MIGRATE_TEST_HOOKS = '1';
      try {
        return await f();
      } finally {
        process.env.MIGRATE_TEST_HOOKS = '1'; // the suite runs with the hooks on
      }
    };
    const quiet = { reverse: false, allowWriters: false, log: () => undefined };

    it('offline: a fence that was applied and then failed keeps PostgreSQL read-only and says where things stand', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const r = await withHooks(() => rollback(w.rt, { ...quiet, failAt: 'fence-applied' }));
      expect(r.ok).to.equal(false);
      expect(r.message).to.contain("fence reads 'active'").and.to.contain('stays read-only');
      expect(await fence()).to.equal('active');
      expect(await storeEpoch()).to.include({ backend: 'postgres', readonly: true });
    });

    it('offline: a fence that was not applied is read back as retired and PostgreSQL is reopened', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      let msg = '';
      await withHooks(() => rollback(w.rt, { ...quiet, failAt: 'fence-not-applied' })).catch((e: Error) => (msg = e.message));
      expect(msg).to.contain('the fence was not applied');
      expect(await fence()).to.equal('retired');
      expect(await storeEpoch()).to.include({ backend: 'postgres', readonly: false });
    });

    it('a freeze whose commit answer is lost is read back: applied is carried on, unknown reopens nothing, a retry never opens it', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      await sql('DELETE FROM conversation.chat_sessions WHERE id = (SELECT id FROM conversation.chat_sessions ORDER BY id LIMIT 1)');
      const a = await withHooks(() => rollback(w.rt, { ...quiet, failAt: 'freeze-commit' }));
      expect(a.mode, 'the freeze was read back as applied and the rollback went on').to.equal('refused');
      expect(a.message).to.contain('differs from MongoDB');
      expect(await storeEpoch(), 'and put back to what it was').to.include({ readonly: false });
      const u = await withHooks(() => rollback(w.rt, { ...quiet, failAt: 'freeze-unknown' }));
      expect(u.message).to.contain('unknown').and.to.contain('platform.store_epoch.readonly');
      expect(await storeEpoch(), 'it may be frozen: nothing reopened it').to.include({ readonly: true });
      const retry = await rollback(w.rt, quiet);
      expect(retry.mode).to.equal('refused');
      expect(await storeEpoch(), 'a retry does not take readonly = true for the value from before').to.include({ readonly: true });
    });

    it('two rollbacks at once: one hands over, the other is refused, and the epoch moves once', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const before = Number((await storeEpoch()).epoch);
      const both = await Promise.all([rollback(w.rt, quiet), rollback(w.rt, quiet)]);
      expect(both.filter((x) => x.ok)).to.have.length(1);
      expect(both.filter((x) => !x.ok)[0]?.mode).to.equal('refused');
      expect(Number((await storeEpoch()).epoch)).to.equal(before + 1);
    });

    it('online: a fence applied and then an error, or unreadable, keeps PostgreSQL read-only and reports the state', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const a = await withHooks(() => rollbackOnline(w.rt, { log: () => undefined, maxFreezeMs: 600000, failAt: 'fence-applied' }));
      expect(a.stage).to.equal('finalize');
      expect(a.message).to.contain("fence reads 'active'");
      expect(await storeEpoch()).to.include({ backend: 'postgres', readonly: true });
      // the operator's repair: retired again, PostgreSQL reopened; then the fence cannot be read back
      await w.mongo.client.db(DB).collection<any>('docstore_fence').updateOne({ _id: 'fence' }, { $set: { mode: 'retired' } });
      await sql('UPDATE platform.store_epoch SET readonly = false');
      const u = await withHooks(() => rollbackOnline(w.rt, { log: () => undefined, maxFreezeMs: 600000, failAt: 'fence-unknown' }));
      expect(u.stage).to.equal('finalize');
      expect(u.message).to.contain('could not be read back').and.to.contain('stays read-only');
      expect(await storeEpoch()).to.include({ readonly: true });
    });

    it('online: a freeze budget spent by the verify is caught before MongoDB is made live', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const r = await withHooks(() => rollbackOnline(w.rt, { log: () => undefined, maxFreezeMs: 600000, failAt: 'over-after-verify' }));
      expect(r.stage).to.equal('budget');
      expect(await fence()).to.equal('retired');
      expect(await storeEpoch()).to.include({ backend: 'postgres', readonly: false });
    });

    it('an offline rollback holds PostgreSQL read-only while it compares and copies, and hands it over closed', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const probes: Array<Promise<any[]>> = [];
      const r = await rollback(w.rt, {
        reverse: false,
        allowWriters: false,
        log: () => {
          // the verify reports as it goes: the store row is read at each report
          probes.push(sql('SELECT readonly FROM platform.store_epoch'));
        },
      });
      expect(r).to.include({ ok: true, mode: 'no_pg_writes' });
      expect(probes.length, 'the verify reported').to.be.greaterThan(0);
      for (const p of probes) expect((await p)[0].readonly, 'read-only while the rollback verifies').to.equal(true);
      expect(await storeEpoch()).to.include({ backend: 'mongo', readonly: true });
      expect(await fence()).to.equal('active');
    });

    it('the reverse follower writes a counter bump that arrives late in its run before it returns', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const js = await JobState.attach(w.rt);
      // the first pass of the follower sweeps at once; the bump comes after it, and the run ends on idleness before the next sweep is due
      const running = reverseFollow(w.rt, js, { log: () => undefined, untilIdleMs: 2500 });
      await new Promise((r) => setTimeout(r, 1200));
      await sql('UPDATE identity.slug_counters SET next_value = next_value + 500');
      await running;
      const top = Number((await sql("SELECT max(next_value)::int AS n FROM identity.slug_counters WHERE kind = 'User'"))[0].n);
      const ctr = await w.mongo.client.db(DB).collection<any>('counters').findOne({ name: 'User' });
      expect(ctr.seq, 'MongoDB counters stay above every number PostgreSQL handed out').to.be.at.least(top);
    });

    it('credentials: several live documents for one account end as exactly one, holding the row of PostgreSQL', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      const col = w.mongo.client.db(DB).collection<any>('userCredentials');
      expect(await col.countDocuments({ userId: String(MEMBER_A), isDeleted: { $ne: true } }), 'the fixture has two for this account').to.be.greaterThan(1);
      await reverseSpecial(w.rt, 'userCredentials');
      const live = await col.find({ userId: String(MEMBER_A), isDeleted: { $ne: true } }).toArray();
      expect(live).to.have.length(1);
      const row = (await sql('SELECT password_hash FROM identity.user_credentials WHERE user_id = $1', [String(MEMBER_A)]))[0];
      expect(live[0].hashedPassword).to.equal(row.password_hash);
    });

    it('a failure after MongoDB is live again never unfreezes PostgreSQL, and says where things stand', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const r = await run(['rollback', '--online', '--fail-at', 'finalize-epoch'], ONLINE);
      expect(r.code).to.equal(1);
      expect(r.out.stage).to.equal('finalize');
      expect(r.out.message).to.contain('MongoDB is the live store again').and.to.contain('was not updated').and.to.contain('not unfrozen');
      expect(await fence()).to.equal('active');
      expect(await storeEpoch(), 'PostgreSQL stays read-only').to.include({ backend: 'postgres', readonly: true });
    });

    it('a failure after the store row is written is reported as done: MongoDB is the store, PostgreSQL closed', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      const r = await run(['rollback', '--online', '--fail-at', 'finalize-checkpoint'], ONLINE);
      expect(r.code).to.equal(0);
      expect(r.out.message).to.contain('MongoDB is the store again').and.to.contain('job record could not be finished');
      expect(await fence()).to.equal('active');
      expect(await storeEpoch()).to.include({ backend: 'mongo', readonly: true });
    });

    it('a second rollback, with MongoDB already the store, is refused and changes nothing', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      expect((await rollback(w.rt, { reverse: false, allowWriters: false, log: () => undefined })).ok).to.equal(true);
      const before = await storeEpoch();
      const again = await rollback(w.rt, { reverse: false, allowWriters: false, log: () => undefined });
      expect(again).to.include({ ok: false, mode: 'refused' });
      expect(again.message).to.contain('nothing to roll back');
      const online = await rollbackOnline(w.rt, { log: () => undefined, maxFreezeMs: 1000 });
      expect(online.ok).to.equal(false);
      expect(online.message).to.contain('nothing to roll back');
      expect(await storeEpoch()).to.deep.include({ backend: before.backend, readonly: true, epoch: before.epoch });
    });

    it('a refused rollback puts readonly back to the value it found, and says so when it cannot', async () => {
      expect((await run(['migrate', '--offline'])).code).to.equal(0);
      await sql('DELETE FROM conversation.chat_sessions WHERE id = (SELECT id FROM conversation.chat_sessions ORDER BY id LIMIT 1)');
      await sql('UPDATE platform.store_epoch SET readonly = true');
      const r = await rollback(w.rt, { reverse: false, allowWriters: false, log: () => undefined });
      expect(r.mode).to.equal('refused');
      expect(await storeEpoch(), 'it was read-only before, and stays so').to.include({ backend: 'postgres', readonly: true });
      await sql('UPDATE platform.store_epoch SET readonly = false');
      process.env.MIGRATE_TEST_HOOKS = '1';
      try {
        const f = await rollback(w.rt, { reverse: false, allowWriters: false, log: () => undefined, failAt: 'unfreeze' });
        expect(f.message).to.contain('PostgreSQL is still read-only').and.to.contain('UPDATE platform.store_epoch SET readonly = false');
        expect(f.message).to.contain('MongoDB is NOT made live');
        expect(await storeEpoch()).to.include({ readonly: true });
      } finally {
        process.env.MIGRATE_TEST_HOOKS = '1'; // the suite runs with the hooks on
      }
    });

    it('a rollback whose reverse verify fails leaves PostgreSQL the store and writable', async () => {
      expect((await run(['migrate', '--online'], ONLINE)).code).to.equal(0);
      expect((await cut()).out.ok).to.equal(true);
      // a change the follower cannot see (no trigger fires for the import role): MongoDB would come back different
      await w.stack.admin(async (c) => {
        await c.query('SET ROLE pipeshub_import');
        await c.query("UPDATE identity.users SET full_name = 'Unlogged' WHERE id = (SELECT id FROM identity.users LIMIT 1)");
      });
      const back = await run(['rollback', '--online'], ONLINE);
      expect(back.code).to.equal(1);
      expect(back.out.stage).to.equal('verify');
      expect(await fence()).to.equal('retired');
      expect(await storeEpoch()).to.include({ backend: 'postgres', readonly: false });
    });
  });
}
