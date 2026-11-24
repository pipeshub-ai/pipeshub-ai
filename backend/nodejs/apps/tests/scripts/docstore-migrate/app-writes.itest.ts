/**
 * After the switch the tool takes the application's MongoDB write roles away, so MongoDB itself refuses a write that skips the fence,
 * and a rollback gives them back. Against a real mongod with authorization on.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { MongoClient, MongoServerError } from 'mongodb';
import { loadConfig } from '../../../src/scripts/docstore-migrate/config';
import { appWritesNote, restoreAppWrites, revokeAppWrites } from '../../../src/scripts/docstore-migrate/fence';
import { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import { haveMongod, startMongo, TestMongo } from './mongo-harness';

const suite = haveMongod ? describe : describe.skip;

suite('docstore-migrate: the application loses MongoDB writes at the switch', function () {
  this.timeout(120_000);
  let mongo: TestMongo;
  const url = (user: string, pw: string, db = 'admin'): string => `mongodb://${user}:${pw}@127.0.0.1:${String(mongo.port)}/?authSource=${db}&directConnection=true`;
  const clients: MongoClient[] = [];
  const as = async (user: string, pw: string): Promise<MongoClient> => {
    const c = await new MongoClient(url(user, pw), { serverSelectionTimeoutMS: 3000 }).connect();
    clients.push(c);
    return c;
  };
  const roles = async (admin: MongoClient, user: string): Promise<Array<{ role: string; db: string }>> =>
    ((await admin.db('admin').command({ usersInfo: user })).users[0].roles as Array<{ role: string; db: string }>)
      .map((r) => ({ role: r.role, db: r.db }))
      .sort((a, b) => `${a.role}${a.db}`.localeCompare(`${b.role}${b.db}`));
  const open = (env: Record<string, string>): Promise<MigrationRuntime> => MigrationRuntime.open(loadConfig({ MONGO_DB_NAME: 'es', ...env }));

  before(async () => {
    mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp', false, ['--auth']);
    // the localhost exception lets the first user in; everything after signs in
    await mongo.client.db('admin').command({ createUser: 'tool', pwd: 'tool-pw', roles: ['root'] });
    const admin = await as('tool', 'tool-pw');
    await admin.db('admin').command({ createUser: 'app', pwd: 'app-pw', roles: [{ role: 'readWrite', db: 'es' }, { role: 'readWrite', db: 'elsewhere' }] });
    await admin.db('admin').command({ createUser: 'ops', pwd: 'ops-pw', roles: ['root'] });
    await admin.db('es').collection('users').insertOne({ n: 1 });
  });
  after(async () => {
    for (const c of clients) await c.close().catch(() => undefined);
    await mongo?.stop();
  });

  it('with a login of its own, the tool takes readWrite on the application database away (MongoDB answers 13) and gives it back at a rollback', async () => {
    const admin = await as('tool', 'tool-pw');
    const before = await roles(admin, 'app');
    const app = await as('app', 'app-pw');
    await app.db('es').collection('users').insertOne({ n: 2 });
    const rt = await open({ MIGRATE_MONGO_URI: url('tool', 'tool-pw'), MONGO_URI: url('app', 'app-pw') });
    try {
      const r = await revokeAppWrites(rt);
      expect(r.error).to.equal(undefined);
      expect(r.revoked).to.deep.equal(['app@admin: readWrite on es']);
      // another administrator still writes everywhere and is named; the tool's own login is not (the application does not use it)
      expect(r.stillWrite).to.deep.equal(['ops@admin (root on admin)']);
      expect(appWritesNote(r)).to.include('ops@admin');
      const refused = await app.db('es').collection('users').insertOne({ n: 3 }).then(() => null, (e: unknown) => e);
      expect(refused).to.be.instanceOf(MongoServerError);
      expect((refused as MongoServerError).code).to.equal(13);
      expect(await app.db('es').collection('users').countDocuments()).to.equal(2);
      // only the application database: a write role elsewhere is not the move's business
      await app.db('elsewhere').collection('x').insertOne({ ok: 1 });
      // a second switch (after a rollback that failed to restore) keeps the first record
      expect((await revokeAppWrites(rt)).revoked).to.deep.equal([]);

      expect(await restoreAppWrites(rt)).to.equal('');
      expect(await roles(admin, 'app')).to.deep.equal(before);
      await app.db('es').collection('users').insertOne({ n: 4 });
      expect(await admin.db('es').collection('docstore_fence').countDocuments({ _id: 'app_writes' as never })).to.equal(0);
      expect(await restoreAppWrites(rt)).to.equal('');
    } finally {
      await rt.close();
    }
  });

  it('when the application shares the tool login (no MIGRATE_MONGO_URI), that login keeps its rights and the report says MongoDB still accepts its writes', async () => {
    const rt = await open({ MONGO_URI: url('tool', 'tool-pw') });
    try {
      const r = await revokeAppWrites(rt);
      expect(r.revoked).to.deep.equal(['app@admin: readWrite on es']);
      expect(r.stillWrite).to.have.members([
        "tool@admin (root on admin; the application signs in as this tool's login)",
        'ops@admin (root on admin)',
      ]);
      expect(await restoreAppWrites(rt)).to.equal('');
    } finally {
      await rt.close();
    }
  });

  it('a login that may not manage users leaves the switch done and says what to run', async () => {
    const rt = await open({ MIGRATE_MONGO_URI: url('app', 'app-pw'), MONGO_URI: url('app', 'app-pw') });
    try {
      const r = await revokeAppWrites(rt);
      expect(r.error).to.match(/not authorized/);
      expect(appWritesNote(r)).to.include('revokeRolesFromUser');
    } finally {
      await rt.close();
    }
  });
});
