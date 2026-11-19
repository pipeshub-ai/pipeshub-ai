/** Each pre-flight check, against a real MongoDB and PostgreSQL: the data it refuses, and the message it gives. */
import 'reflect-metadata';
import { expect } from 'chai';
import { MongoClient, ObjectId } from 'mongodb';
import { PG_BINS, haveStack } from '../../stores/pg-store-harness';
import { loadConfig } from '../../../src/scripts/docstore-migrate/config';
import { preflight } from '../../../src/scripts/docstore-migrate/preflight';
import { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import { conversationDocs } from './fixtures/conversation';
import { contentDocs } from './fixtures/content';
import { identityDocs } from './fixtures/identity';
import { haveMongod, startMongo } from './mongo-harness';
import { World, startWorld } from './world';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_preflight';

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: pre-flight checks (${bin})`, function () {
    this.timeout(300_000);
    let w: World;
    const base = (): Record<string, unknown[]> => ({ ...identityDocs(), ...conversationDocs(), ...contentDocs() });

    before(async () => {
      w = await startWorld(bin, 'migrate_pre', DB);
    });
    after(async () => {
      await w?.stop();
    });
    beforeEach(async () => {
      await w.reseed(base());
      await w.truncate();
    });

    const col = (name: string) => w.mongo.client.db(DB).collection<any>(name);
    const run = (opts: Parameters<typeof preflight>[1] = {}) => preflight(w.rt, opts);
    const reds = (r: Awaited<ReturnType<typeof preflight>>): string[] => r.red.map((x) => x.check);
    const redMessage = (r: Awaited<ReturnType<typeof preflight>>, check: string): string => r.red.find((x) => x.check === check)?.message ?? '';

    it('is clean on good data and says what it found', async () => {
      await col('docstore_migrate_marker').insertOne({ _id: 'left-over', at: new Date() });
      const r = await run();
      expect(r.red, JSON.stringify(r.red)).to.deep.equal([]);
      expect(r.ok).to.equal(true);
      expect(r.info.mongoTopology).to.have.property('replicaSet');
      expect(r.info.collections).to.have.property('users');
    });

    it('is yellow, not red, when the import login is shut', async () => {
      const url = new URL(w.env.PG_IMPORT_URL!);
      url.password = '';
      const saved = w.rt.cfg.pgImportUrl;
      Object.assign(w.rt.cfg, { pgImportUrl: url.toString() });
      try {
        const r = await run();
        expect(r.red).to.deep.equal([]);
        expect(r.yellow.map((y) => y.check)).to.include('target');
      } finally {
        Object.assign(w.rt.cfg, { pgImportUrl: saved });
      }
    });

    it('refuses a target that is too small, with the sizes', async () => {
      Object.assign(w.rt.cfg, { pgFreeBytes: 1000 });
      try {
        const r = await run();
        expect(reds(r)).to.include('disk');
        expect(redMessage(r, 'disk')).to.match(/the target has 1000 bytes free and the copy needs about \d+/);
      } finally {
        Object.assign(w.rt.cfg, { pgFreeBytes: 10 ** 12 });
      }
    });

    it('names a field in the data that no mapping covers', async () => {
      await col('chatSessions').updateOne({}, { $set: { 'x.y': 1 } });
      const r = await run();
      expect(reds(r)).to.include('path_census');
      expect(redMessage(r, 'path_census')).to.match(/chatSessions\.x is in the data .*no mapping covers it/);
    });

    it('names a collection with documents and no mapping', async () => {
      await col('mystery').insertOne({ a: 1 });
      const r = await run();
      expect(redMessage(r, 'unmapped_collection')).to.contain("collection 'mystery' holds 1 documents");
    });

    it('blocks legacy conversations that still hold messages, and tells the operator what to do', async () => {
      await col('conversations').insertOne({ messages: [] });
      const r = await run();
      expect(reds(r)).to.include('legacy_conversations');
      expect(redMessage(r, 'legacy_conversations')).to.contain('start the application on MongoDB once');
    });

    it('refuses an _id that is not an ObjectId, with its type', async () => {
      await col('users').insertOne({ _id: 'a'.repeat(24), orgId: new ObjectId(), slug: 'user-3000', email: 'text-id@x.example' });
      const r = await run();
      expect(reds(r)).to.include('id_shape');
      expect(JSON.stringify(r.red.find((x) => x.check === 'id_shape')!.data)).to.contain('"type":"string"');
    });

    it('refuses two users with one address in one organisation', async () => {
      const u = await col('users').findOne({});
      await col('users').insertOne({ ...u, _id: new ObjectId(), slug: 'user-3001' });
      expect(reds(await run())).to.include('duplicate_users');
    });

    it('blocks a citation stamped with another organisation than the message that cites it', async () => {
      const msg = await col('chatSessionMessages').findOne({ 'citations.0': { $exists: true } });
      expect(msg, 'the fixtures have a message with a citation').to.not.equal(null);
      await col('citations').updateOne({ _id: msg.citations[0].citationId }, { $set: { 'metadata.orgId': 'fake-org' } });
      const r = await run();
      expect(reds(r)).to.include('citation_org');
      expect(redMessage(r, 'citation_org')).to.contain('correct their metadata.orgId first');
    });

    it('names two unsent outbox events that would get one table id', async () => {
      const a = new ObjectId('6ad8b333aaaaaaaaaa000001');
      const b = new ObjectId('6ad8b333bbbbbbbbbb000001'); // same second, same counter, other process bytes
      const ev = { topic: 't', key: 'k', value: '{"orgId":"6ad8b333061f2015b22fc6c0"}', status: 'pending', createdAt: new Date(), updatedAt: new Date() };
      await col('outbox_events').insertMany([{ _id: a, ...ev }, { _id: b, ...ev }]);
      const r = await run();
      expect(reds(r)).to.include('outbox_id_collision');
      expect(redMessage(r, 'outbox_id_collision')).to.contain(`${a.toHexString()} / ${b.toHexString()}`);
    });

    it('is red while the application is connected, even an idle one, and yellow when that is allowed', async () => {
      const app = await new MongoClient(w.mongo.uri, { appName: 'pipeshub-app' }).connect();
      await app.db(DB).command({ ping: 1 });
      try {
        const r = await run();
        expect(reds(r)).to.include('app_running');
        expect(redMessage(r, 'app_running')).to.contain('the application is still connected to MongoDB');
        const allowed = await run({ allowClients: true });
        expect(reds(allowed)).to.not.include('app_running');
        expect(allowed.yellow.map((y) => y.check)).to.include('app_running');
      } finally {
        await app.close();
      }
      // a Node client under a name of its own counts as well; mongosh does not
      const other = await new MongoClient(w.mongo.uri, { appName: 'someone-elses-name' }).connect();
      await other.db(DB).command({ ping: 1 });
      try {
        expect(reds(await run())).to.include('app_running');
      } finally {
        await other.close();
      }
      expect(reds(await run())).to.not.include('app_running');
    });

    it('is red when the schema and this build disagree, and says to use the matching image', async () => {
      await w.stack.admin((c) => c.query("UPDATE kernel.schema_epoch SET min_code_epoch = 99 WHERE branch = 'identity'"));
      try {
        const r = await run();
        expect(reds(r)).to.include('app_version');
        expect(redMessage(r, 'app_version')).to.contain('migrator image of the same release');
      } finally {
        await w.stack.admin((c) => c.query("UPDATE kernel.schema_epoch SET min_code_epoch = 0 WHERE branch = 'identity'"));
      }
    });

    it('is red when the target already holds organisations and no move is running', async () => {
      await w.stack.admin((c) => c.query("INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, created_at, updated_at) VALUES ($1, 'a.example', 'o@a.example', 'business', now(), now())", [String(new ObjectId())]));
      const r = await run();
      expect(reds(r)).to.include('target_not_empty');
      expect(redMessage(r, 'target_not_empty')).to.contain('PostgreSQL already holds 1 organisations');
    });

    it('refuses the online path on a standalone MongoDB', async () => {
      const standalone = await startMongo(process.env.PG_TEST_WORK ?? '/tmp', false);
      const rt = await MigrationRuntime.open(loadConfig({ MONGO_URI: standalone.uri, MONGO_DB_NAME: 'es_standalone' }));
      try {
        const online = await preflight(rt, { online: true });
        expect(reds(online)).to.include('change_streams');
        expect(redMessage(online, 'change_streams')).to.contain('needs a replica set');
        expect(reds(await preflight(rt))).to.not.include('change_streams');
      } finally {
        await rt.close();
        await standalone.stop();
      }
    });
  });
}
