/**
 * R48MIGFIX-FU-2: every document the copy refuses is known to verify and rollback, past the job state's list of the first
 * `MAX_SKIPPED_LISTED`. A real MongoDB and PostgreSQL: Slack links whose person is not a copied user (more than the cap) are refused;
 * verify reports none of them missing, rollback removes none of them from MongoDB, and the follower's whole-collection sync keeps the
 * list. Before, the 21st past the cap read as missing and a rollback deleted it.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { loadConfig } from '../../../src/scripts/docstore-migrate/config';
import { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import { JobState } from '../../../src/scripts/docstore-migrate/state';
import { bulk, MAX_SKIPPED_LISTED } from '../../../src/scripts/docstore-migrate/bulk';
import { verifyAll } from '../../../src/scripts/docstore-migrate/verify';
import { reverseCopy } from '../../../src/scripts/docstore-migrate/reverse';
import { SLACK_LINKS, syncSlackLinks } from '../../../src/scripts/docstore-migrate/slack-links';
import { ADMIN_A, MEMBER_A, ORG_A, identityDocs } from './fixtures/identity';
import { at, oid } from './roundtrip';
import { TestMongo, haveMongod, startMongo } from './mongo-harness';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_refusals';
const log = (): void => undefined;
const REFUSED = MAX_SKIPPED_LISTED + 21;

const link = (n: number, userId: string): Record<string, unknown> => ({
  _id: oid(),
  orgId: String(ORG_A),
  workspaceId: 'E1',
  slackUserId: `U${String(n).padStart(5, '0')}`,
  teamId: 'T1',
  userId,
  email: `p${n}@example.test`,
  slackName: `p${n}`,
  linkedAt: at('2026-11-01T10:00:00.000Z'),
  createdAt: at('2026-11-01T10:00:00.000Z'),
  updatedAt: at('2026-11-01T10:00:00.000Z'),
  __v: 0,
});

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: every refusal is known past the job state's list (R48MIGFIX-FU-2, ${bin})`, function () {
    this.timeout(240_000);
    let stack: PgStack;
    let mongo: TestMongo;
    let rt: MigrationRuntime;

    before(async () => {
      stack = await startPgStack(bin, 'migrate_refusals', { importer: true });
      mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp');
      const docs = identityDocs();
      const db = mongo.client.db(DB);
      for (const name of ['org', 'users'])
        await db.collection(name).insertMany(docs[name] as never[], { ordered: false });
      const links = [link(0, String(ADMIN_A)), link(1, String(MEMBER_A))];
      // linked to a person who is not a copied user of the organisation: the importer refuses each
      for (let n = 2; n < REFUSED + 2; n++) links.push(link(n, String(oid())));
      await db.collection(SLACK_LINKS).insertMany(links as never[]);
      rt = await MigrationRuntime.open(
        loadConfig({
          ...process.env,
          MONGO_URI: mongo.uri,
          MONGO_DB_NAME: DB,
          PG_IMPORT_URL: stack.cluster.url('migrate_refusals', 'pipeshub_import').replace('pipeshub_import@', 'pipeshub_import:x@'),
          PG_NODE_URL: stack.cluster.url('migrate_refusals', 'pipeshub_node_login', stack.bouncer.port),
          PG_MIGRATE_URL: stack.cluster.url('migrate_refusals', 'pipeshub_migrate'),
          MIGRATE_SCHEMA_COMMAND: 'none',
          MIGRATE_BATCH_SIZE: '50',
          MIGRATE_PARALLEL: '1',
        }),
      );
    });
    after(async () => {
      await rt?.close();
      await mongo?.stop();
      await stack?.stop();
    });

    const refusedRows = async (): Promise<number> =>
      (await stack.admin((c) => c.query('SELECT count(*)::int AS n FROM platform.docstore_refused WHERE collection = $1', [SLACK_LINKS]))).rows[0].n;

    it('the copy refuses them all, lists the first in the job state and keeps every one', async () => {
      const js = await JobState.attach(rt);
      await bulk(rt, js, { only: ['org', 'users', SLACK_LINKS], log });
      const p = js.progress.collections[SLACK_LINKS]!;
      expect(p.skipped).to.equal(REFUSED);
      expect(Object.keys(p.skippedIds ?? {})).to.have.length(MAX_SKIPPED_LISTED);
      expect(await refusedRows()).to.equal(REFUSED);
    });

    it('verify reports none of them missing', async () => {
      const v = await verifyAll(rt, { only: [SLACK_LINKS], log });
      const c = v.collections.find((x) => x.collection === SLACK_LINKS)!;
      expect(c.diffCount, JSON.stringify(c.diffs.slice(0, 2))).to.equal(0);
      expect(c.skippedListed).to.have.length(REFUSED);
    });

    it("the follower's sync keeps the list, and a rollback leaves every refused link in MongoDB", async () => {
      const js = await JobState.attach(rt);
      await syncSlackLinks(rt, js);
      await js.checkpoint();
      expect(await refusedRows()).to.equal(REFUSED);
      const r = await reverseCopy(rt, log);
      expect(r.reversed[SLACK_LINKS]).to.deep.equal({ written: 2, removed: 0 });
      expect(await mongo.client.db(DB).collection(SLACK_LINKS).countDocuments()).to.equal(REFUSED + 2);
    });
  });
}
