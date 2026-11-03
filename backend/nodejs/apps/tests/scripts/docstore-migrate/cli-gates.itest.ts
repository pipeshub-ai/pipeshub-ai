/** Two gates of the command line, on real servers: `post_load` fails when the target does not check out, and `drain` wants the fence armed. */
import 'reflect-metadata';
import { expect } from 'chai';
import { MongoClient, ObjectId } from 'mongodb';
import { PG_BINS, haveStack } from '../../stores/pg-store-harness';
import { identityDocs } from './fixtures/identity';
import { haveMongod } from './mongo-harness';
import { runCommand } from './cli-run';
import { World, startWorld } from './world';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_cli_gates';

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: command line gates (${bin})`, function () {
    this.timeout(300_000);
    let w: World;
    before(async () => {
      w = await startWorld(bin, 'migrate_gates', DB);
    });
    after(async () => {
      await w?.stop();
    });
    beforeEach(async () => {
      await w.reseed(identityDocs());
      await w.truncate();
    });

    const run = (args: string[], extra: NodeJS.ProcessEnv = {}) => runCommand(w, args, extra);

    it('the dispatcher: an unknown command exits 2, help exits 0, reset wants --yes, and status reads the job', async () => {
      expect((await run(['no-such-command'])).code).to.equal(2);
      expect((await run(['help'])).code).to.equal(0);
      const noYes = await run(['reset']);
      expect(noYes.code).to.equal(2);
      const status = await run(['status']);
      expect(status.code).to.equal(0);
    });

    it('preflight, bulk, verify and reset on the command line: clean data copies, verifies and can be reset', async () => {
      const pre = await run(['preflight']);
      expect(pre.out.red, JSON.stringify(pre.out.red)).to.deep.equal([]);
      expect(pre.code).to.equal(0);
      const copy = await run(['bulk']);
      expect(copy.code).to.equal(0);
      expect(copy.out.ok).to.equal(true);
      const verify = await run(['verify']);
      expect(verify.code, JSON.stringify(verify.out.collections?.filter((c: any) => c.diffCount))).to.equal(0);
      expect(Number((await w.stack.admin((c) => c.query('SELECT count(*)::int AS n FROM identity.users'))).rows[0].n)).to.be.greaterThan(0);
      const reset = await run(['reset', '--yes']);
      expect(reset.code).to.equal(0);
      expect(Number((await w.stack.admin((c) => c.query('SELECT count(*)::int AS n FROM identity.users'))).rows[0].n)).to.equal(0);
      // a copy stopped after its first batches is reported as stopped, exit 1, and the next run resumes it
      const stopped = await run(['bulk', '--stop-after-batches', '1']);
      expect(stopped.code).to.equal(1);
      expect(stopped.out.stopped).to.contain('stopped after 1 batches');
      expect((await run(['bulk'])).code).to.equal(0);
    });

    it('follow run leaves no signal handlers behind, and a flag that cannot be read stops a drain before it touches anything', async () => {
      expect((await run(['follow', 'start'])).code).to.equal(0);
      const handlers = (): number[] => [process.listenerCount('SIGTERM'), process.listenerCount('SIGINT')];
      const before = handlers();
      expect((await run(['follow', 'run', '--until-idle', '1'])).code).to.equal(0);
      expect(handlers()).to.deep.equal(before);
      // a running turn is not interrupted by a drain whose wait is not a number
      const sessions = w.mongo.client.db(DB).collection('chatSessions');
      await sessions.insertOne({ _id: new ObjectId(), status: 'Inprogress', lastActivityAt: Date.now(), createdAt: new Date() } as never);
      expect((await run(['drain', '--wait', 'abc', '--force'])).code).to.equal(2);
      expect(await sessions.countDocuments({ status: 'Inprogress' })).to.equal(1);
      expect(await w.mongo.client.db(DB).collection('docstore_fence').countDocuments()).to.equal(0);
    });

    it('preflight is red while an application is connected, and --allow-clients makes that a warning', async () => {
      const app = await new MongoClient(w.mongo.uri, { appName: 'pipeshub-app' }).connect();
      await app.db(DB).command({ ping: 1 });
      try {
        const red = await run(['preflight']);
        expect(red.code).to.equal(1);
        expect(red.out.red.map((x: any) => x.check)).to.include('app_running');
        const allowed = await run(['preflight', '--allow-clients']);
        expect(allowed.code, JSON.stringify(allowed.out.red)).to.equal(0);
        expect(allowed.out.yellow.map((x: any) => x.check)).to.include('app_running');
      } finally {
        await app.close();
      }
    });

    it('post_load exits 0 on a target that checks out, and 1 with the problem when a constraint is left unvalidated', async () => {
      const ok = await run(['post_load']);
      expect(ok.code).to.equal(0);
      expect(ok.out).to.include({ ok: true });
      await w.stack.admin((c) => c.query('ALTER TABLE identity.org_logos ADD CONSTRAINT nv_cli_probe CHECK (true) NOT VALID'));
      try {
        const bad = await run(['post_load']);
        expect(bad.code).to.equal(1);
        expect(bad.out.ok).to.equal(false);
        expect(bad.out.problems).to.include('constraint nv_cli_probe on identity.org_logos is not validated');
      } finally {
        await w.stack.admin((c) => c.query('ALTER TABLE identity.org_logos DROP CONSTRAINT nv_cli_probe'));
      }
    });

    it('drain exits 1 and leaves the fence alone while an application client does not carry it; --confirm-fence-armed goes ahead', async () => {
      const app = await new MongoClient(w.mongo.uri, { appName: 'pipeshub-app' }).connect();
      await app.db(DB).command({ ping: 1 });
      try {
        const refused = await run(['drain', '--wait', '0']);
        expect(refused.code).to.equal(1);
        expect(refused.out.message).to.contain('do not carry the Mongo fence');
        expect(await w.mongo.client.db(DB).collection<any>('docstore_fence').findOne({ _id: 'fence' })).to.equal(null);
        const told = await run(['drain', '--wait', '0', '--confirm-fence-armed']);
        expect(told.code).to.equal(0);
        expect(told.out.fence).to.equal('draining');
        expect((await run(['drain', '--cancel'])).out.changed).to.equal(true);
      } finally {
        await app.close();
      }
    });
  });
}
