/** Two gates of the command line, on real servers: `post_load` fails when the target does not check out, and `drain` wants the fence armed. */
import 'reflect-metadata';
import { expect } from 'chai';
import { MongoClient } from 'mongodb';
import { PG_BINS, haveStack } from '../../stores/pg-store-harness';
import { runCli } from '../../../src/scripts/docstore-migrate/cli';
import { identityDocs } from './fixtures/identity';
import { haveMongod } from './mongo-harness';
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

    /** Runs a command in this process and returns its exit code and the JSON it printed. */
    async function run(args: string[]): Promise<{ code: number; out: any }> {
      let text = '';
      const out = process.stdout.write.bind(process.stdout);
      const err = process.stderr.write.bind(process.stderr);
      (process.stdout as any).write = (s: string): boolean => ((text += s), true);
      (process.stderr as any).write = (): boolean => true;
      try {
        // a command opens its own runtime, and a process holds one: the suite's is closed for the call
        await w.rt.close();
        const code = await runCli(args, w.env);
        return { code, out: text ? JSON.parse(text) : null };
      } finally {
        (process.stdout as any).write = out;
        (process.stderr as any).write = err;
        w.rt = await (await import('../../../src/scripts/docstore-migrate/runtime')).MigrationRuntime.open((await import('../../../src/scripts/docstore-migrate/config')).loadConfig(w.env));
      }
    }

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
