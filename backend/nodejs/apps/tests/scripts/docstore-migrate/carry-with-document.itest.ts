/**
 * A carrier whose fields are written with their documents (`withDocument`): on a real MongoDB and PostgreSQL, the copy and the
 * follower write the fields in the unit that writes the document, after its rows, and a failure there undoes both.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { ObjectId } from 'mongodb';
import {
  PG_BINS,
  PgStack,
  haveStack,
  startPgStack,
} from '../../stores/pg-store-harness';
import { loadConfig } from '../../../src/scripts/docstore-migrate/config';
import { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import { JobState } from '../../../src/scripts/docstore-migrate/state';
import { bulk } from '../../../src/scripts/docstore-migrate/bulk';
import { replaceDocs } from '../../../src/scripts/docstore-migrate/apply';
import { repair } from '../../../src/scripts/docstore-migrate/repair';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { CARRIERS } from '../../../src/scripts/docstore-migrate/carry';
import {
  resyncCarried,
  verifyCarried,
} from '../../../src/scripts/docstore-migrate/carry/run';
import {
  carryMongo,
  carryPg,
} from '../../../src/scripts/docstore-migrate/carry/steps';
import type { Carrier } from '../../../src/scripts/docstore-migrate/carry/types';
import { identityDocs } from './fixtures/identity';
import { TestMongo, haveMongod, startMongo } from './mongo-harness';

const suite = haveStack && haveMongod ? describe : describe.skip;
const REFUSED_LOGO = new ObjectId();
const DB = 'es_with_doc';
const log = (): void => undefined;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(
    `docstore-migrate: fields written with their documents (${bin})`,
    function () {
      this.timeout(180_000);
      let stack: PgStack;
      let mongo: TestMongo;
      let rt: MigrationRuntime;
      /** What the carrier saw in the unit: the document rows already there when it was called. */
      const seen: number[] = [];
      const carrier: Carrier = {
        name: 'qa-with-document',
        reason: 'test: fails on a document that asks it to',
        collections: [],
        fields: { users: ['qaFault'] },
        reads: { users: { pick: ['qaFault'] } },
        tables: ['identity.users'],
        withDocument: true,
        async write(k, _c, docs) {
          const { rows } = await k.query<{ n: number }>(
            'SELECT count(*)::int AS n FROM identity.users WHERE id = ANY ($1::text[])',
            [docs.map((d) => d.id)],
          );
          seen.push(rows[0]!.n);
          const faulty = docs.filter((d) => d.doc.qaFault === true);
          if (faulty.length) {
            // a field written in the unit before the failure: undone with the documents
            await k.query(
              "UPDATE identity.users SET full_name = 'written by the carrier' WHERE id = ANY ($1::text[])",
              [faulty.map((d) => d.id)],
            );
            throw new Error('injected: the fields cannot be written');
          }
        },
        remove: async () => undefined,
        expected: () => [],
        actual: async () => new Map(),
        restore: async () => ({ written: 0, removed: 0 }),
        clear: async () => undefined,
      };
      /** A carrier of a field of org-logos (a collection whose importer skips a logo without an org): what it holds, by logo id. */
      const logoFields = new Map<string, unknown>();
      const logoCarrier: Carrier = {
        name: 'qa-logo-fields',
        reason: 'test: keeps one field of each logo',
        collections: [],
        fields: { 'org-logos': ['qaField'] },
        reads: { 'org-logos': { pick: ['qaField'] } },
        tables: ['identity.org_logos'],
        withDocument: true,
        async write(_k, _c, docs) {
          for (const d of docs) logoFields.set(d.id, d.doc.qaField);
        },
        async remove(_k, _c, ids) {
          for (const id of ids) logoFields.delete(id);
        },
        expected: () => [],
        actual: async () => new Map(),
        restore: async () => ({ written: 0, removed: 0 }),
        clear: async () => logoFields.clear(),
      };
      const users = async (): Promise<
        Array<{ id: string; full_name: string | null }>
      > =>
        (
          await stack.admin((c) =>
            c.query('SELECT id, full_name FROM identity.users ORDER BY id'),
          )
        ).rows;

      before(async () => {
        stack = await startPgStack(bin, 'migrate_with_doc', { importer: true });
        mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp');
        const docs = identityDocs();
        (docs.users![0] as Record<string, unknown>).qaFault = true;
        // a logo with no org: the importer refuses it whole, so its carried field must never be in PostgreSQL
        docs['org-logos']!.push({
          _id: REFUSED_LOGO,
          logo: null,
          qaField: 'never',
          __v: 0,
        });
        for (const [name, list] of Object.entries(docs))
          if (list.length)
            await mongo.client
              .db(DB)
              .collection(name)
              .insertMany(list as never[], { ordered: false });
        rt = await MigrationRuntime.open(
          loadConfig({
            ...process.env,
            MONGO_URI: mongo.uri,
            MONGO_DB_NAME: DB,
            PG_IMPORT_URL: stack.cluster
              .url('migrate_with_doc', 'pipeshub_import')
              .replace('pipeshub_import@', 'pipeshub_import:x@'),
            PG_NODE_URL: stack.cluster.url(
              'migrate_with_doc',
              'pipeshub_node_login',
              stack.bouncer.port,
            ),
            PG_MIGRATE_URL: stack.cluster.url(
              'migrate_with_doc',
              'pipeshub_migrate',
            ),
            MIGRATE_SCHEMA_COMMAND: 'none',
            MIGRATE_BATCH_SIZE: '100', // every user in one batch, so one unit
            MIGRATE_PARALLEL: '1',
          }),
        );
        (CARRIERS as Carrier[]).push(carrier, logoCarrier);
      });
      after(async () => {
        for (const c of [carrier, logoCarrier]) {
          const i = CARRIERS.indexOf(c);
          if (i >= 0) (CARRIERS as Carrier[]).splice(i, 1);
        }
        await rt?.close();
        await mongo?.stop();
        await stack?.stop();
      });

      it('the copy: a failure after the users are written and before their fields undoes the users too; once corrected both are written', async () => {
        const js = await JobState.attach(rt);
        let err: Error | undefined;
        await bulk(rt, js, { log }).catch((e: Error) => (err = e));
        expect(err?.message).to.contain(
          'injected: the fields cannot be written',
        );
        expect(
          seen,
          'called in the unit, after the users were written',
        ).to.have.length(1);
        expect(seen[0]).to.be.greaterThan(0);
        expect(
          await users(),
          'the unit was undone: no user without its fields',
        ).to.deep.equal([]);

        await mongo.client
          .db(DB)
          .collection('users')
          .updateMany({}, { $unset: { qaFault: '' } });
        await bulk(rt, await JobState.attach(rt), { log });
        expect((await users()).length).to.equal(identityDocs().users!.length);
        expect(seen.at(-1)).to.equal(identityDocs().users!.length);
      });

      it('the follower: a failure in the fields keeps the document as it was', async () => {
        const m = MAPPINGS.find((x) => x.collection === 'users')!;
        const [first] = await mongo.client
          .db(DB)
          .collection('users')
          .find({})
          .sort({ _id: 1 })
          .limit(1)
          .toArray();
        const before = await users();
        let err: Error | undefined;
        await replaceDocs(rt, m, [
          { ...first!, fullName: 'changed while following', qaFault: true },
        ]).catch((e: Error) => (err = e));
        expect(err?.message).to.contain('injected');
        expect(
          await users(),
          'the new name was undone with the fields',
        ).to.deep.equal(before);
        await replaceDocs(rt, m, [
          { ...first!, fullName: 'changed while following' },
        ]);
        expect(
          (await users()).find((u) => u.id === String(first!._id))?.full_name,
        ).to.equal('changed while following');
      });

      it('the follower and a repair: a document the importer now skips, or a repair removes, loses its carried fields in the same unit', async () => {
        const logos = MAPPINGS.find((x) => x.collection === 'org-logos')!;
        const [logo] = await mongo.client
          .db(DB)
          .collection('org-logos')
          .find({ orgId: { $exists: true } })
          .sort({ _id: 1 })
          .limit(1)
          .toArray();
        const id = String(logo!._id).toLowerCase();
        await replaceDocs(rt, logos, [{ ...logo!, qaField: 'kept' }]);
        expect(logoFields.get(id)).to.equal('kept');
        // the follower: the logo loses its org, the importer skips it and removes its row, and its field goes with it
        const orphan = { ...logo! };
        delete orphan.orgId;
        await replaceDocs(rt, logos, [{ ...orphan, qaField: 'kept' }]);
        expect(
          logoFields.has(id),
          'a skipped document keeps no carried field',
        ).to.equal(false);
        // a repair writes it again, then removes it as extra (MongoDB no longer holds it)
        await replaceDocs(rt, logos, [{ ...logo!, qaField: 'again' }]);
        expect(logoFields.get(id)).to.equal('again');
        await repair(
          rt,
          new Map([['org-logos', [{ id, kind: 'extra_in_pg' } as never]]]),
          log,
        );
        expect(
          logoFields.has(id),
          'a document a repair removes keeps no carried field',
        ).to.equal(false);
      });

      it('a document the copy refused has no carried field: not after the copy, nor after a resync, and the verify does not expect one', async () => {
        const id = REFUSED_LOGO.toHexString();
        expect(
          (
            await stack.admin((c) =>
              c.query(
                "SELECT 1 FROM platform.docstore_refused WHERE collection = 'org-logos' AND source_id = $1",
                [id],
              ),
            )
          ).rows,
        ).to.have.length(1);
        expect(logoFields.has(id), 'after the copy').to.equal(false);
        await resyncCarried(carryPg(rt), carryMongo(rt), [logoCarrier]);
        expect(logoFields.has(id), 'after a resync').to.equal(false);
        const logos = await mongo.client.db(DB).collection('org-logos').find({}).toArray();
        expect(
          [...logoFields.keys()].sort(),
          'the resync cleared the fields and wrote every other logo\'s again',
        ).to.deep.equal(
          logos
            .map((l) => String(l._id).toLowerCase())
            .filter((l) => l !== id)
            .sort(),
        );
        // the verify expects every logo the copy took, and none it refused (the fake's actual is what it holds)
        const v = await verifyCarried(carryPg(rt), carryMongo(rt), [
          {
            ...logoCarrier,
            expected: (_c, d) => [[d.id, d.doc.qaField]],
            actual: async () => new Map(logoFields),
          },
        ]);
        expect(v.map((x) => [x.collection, x.ok, x.missing])).to.deep.equal([
          ['org-logos', true, []],
        ]);
      });
    },
  );
}
