/**
 * Coverage on a migrated PostgreSQL: every mapping's repository has an `importBatch`, every column of every mapped table is either in
 * the mapping or listed as derived, and every table of the document store is reached or listed (coverage-lists.ts). The same lists
 * are checked without a database, against the surface snapshot, by coverage.test.ts in `npm test`.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping';
import { REPOSITORIES } from '../../../src/libs/repositories/all';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { DOC_STORE_SCHEMAS, columnProblems, namedColumns, unreachedTables } from './coverage-lists';

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: table coverage (${bin})`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    before(async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_cov', { importer: true });
    });
    after(async () => {
      await stack?.stop();
    });

    it('repositories expose importBatch', () => {
      process.env.DOC_STORE = 'postgres';
      for (const m of MAPPINGS) {
        const repo = REPOSITORIES[m.repository]!() as { importBatch?: unknown };
        expect(repo.importBatch, `${m.collection}: ${m.repository}.importBatch`).to.be.a('function');
      }
    });

    it('every column of a mapped table is mapped or listed as derived, and every mapped column exists', async () => {
      const have = new Map<string, string[]>();
      for (const t of namedColumns().keys()) {
        const [schema, name] = t.split('.');
        const r = await stack.admin((c) =>
          c.query('SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2', [schema, name]),
        );
        have.set(t, r.rows.map((x: { column_name: string }) => x.column_name));
      }
      expect(columnProblems((t) => have.get(t) ?? [])).to.deep.equal([]);
    });

    it('every table of the document store is reached by a mapping, named by a held or ignored collection, or listed with a reason', async () => {
      const r = await stack.admin((c) =>
        c.query(
          `SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r','p') AND NOT c.relispartition AND n.nspname = ANY($1::text[])`,
          [DOC_STORE_SCHEMAS],
        ),
      );
      const tables = r.rows.map((x: { t: string }) => x.t);
      expect(tables.filter((t: string) => t.startsWith('harness.')).length, 'the harness tables are migrated').to.be.at.least(53);
      expect(unreachedTables(tables)).to.deep.equal([]);
    });
  });
}
