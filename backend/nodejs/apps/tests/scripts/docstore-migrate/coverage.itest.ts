/**
 * Coverage: every Mongo collection the application defines has a mapping (or is listed as not copied), every mapping names a
 * repository with an `importBatch`, and every column of every mapped table is either in the mapping or listed as derived. A
 * table the mappings do not reach must be listed here with the reason.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { MAPPINGS, IGNORED } from '../../../src/scripts/docstore-migrate/mapping';
import { REPOSITORIES } from '../../../src/libs/repositories/all';
import { PG_BINS, PgStack, haveStack, startPgStack } from '../../stores/pg-store-harness';
import { MONGO_COLLECTIONS, findModelFiles } from '../../../src/scripts/docstore-migrate/mongo-collections';
import { schemaDrift } from './schema-drift';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/** Tables no collection maps to: written by functions, derived, or not data to move. */
export const UNMAPPED_TABLES: Record<string, string> = {
  'platform.kv_entries': 'key-value store runtime state, rebuilt by use',
  'platform.queue_deliveries': 'message queue runtime state, rebuilt by use',
  'platform.queue_groups': 'message queue runtime state, rebuilt by use',
  'platform.queue_messages': 'message queue runtime state, rebuilt by use',
  'platform.sched_jobs': 'job queue runtime state, rebuilt by use',
  'platform.sched_repeats': 'job queue runtime state, rebuilt by use',
  'platform.docstore_changes': 'the change log the reverse follower reads; written by triggers',
  'platform.store_epoch': 'the store fence, owned by the migrator',
  'platform.jobs': 'job state',
  'platform.consumer_inbox': 'consumer de-duplication, rebuilt by use',
  'platform.deployment_profile': 'deployment profile, not application data',
};

const suite = haveStack ? describe : describe.skip;

/** Schema paths the migration deliberately does not carry (none today). */
export const ALLOWED_UNMAPPED_SCHEMA_PATHS: Record<string, string> = {};

describe('docstore-migrate: collection coverage (no database)', () => {
  it('every field of every mapped Mongoose schema is known to its mapping (a new field fails here, not at an install\'s preflight)', () => {
    expect(schemaDrift(MAPPINGS, ALLOWED_UNMAPPED_SCHEMA_PATHS)).to.deep.equal([]);
  });

  it('the drift check sees a new field (mutation: a path added to a schema is reported, and removed again)', async () => {
    const mongoose = (await import('mongoose')).default;
    const model = Object.values(mongoose.models).find((x) => x.collection.name === 'chatSessions')!;
    model.schema.add({ qaNewField: String });
    try {
      expect(schemaDrift(MAPPINGS)).to.include('chatSessions.qaNewField');
    } finally {
      model.schema.remove('qaNewField');
    }
    expect(schemaDrift(MAPPINGS, ALLOWED_UNMAPPED_SCHEMA_PATHS)).to.deep.equal([]);
  });

  it('every file in the source tree that declares a Mongoose model is found by the model-file search (a new model file cannot hide)', () => {
    const src = path.resolve(__dirname, '../../../src');
    const found = new Set(findModelFiles().map((f) => path.join(src, f)));
    const declaring: string[] = [];
    const walk = (dir: string): void => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) walk(full);
        else if (/\.ts$/.test(e.name) && !e.name.endsWith('.d.ts') && /(mongoose\.model|[^.\w]model)(<[^>(]*>)?\(\s*['"][\w-]+['"]\s*,/.test(fs.readFileSync(full, 'utf8')) && !full.includes('/scripts/docstore-migrate/')) declaring.push(full.replace(/\.ts$/, ''));
      }
    };
    walk(src);
    expect(declaring.length, 'the scan sees the models').to.be.greaterThan(15);
    expect(declaring.filter((f) => !found.has(f)), 'model files the search does not find').to.deep.equal([]);
  });

  it('the model-file search finds *.schema files and files in schema directories anywhere (mutation: a new file in an unlisted place)', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'models-'));
    fs.mkdirSync(path.join(dir, 'modules/newthing/schema'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'modules/newthing/schema/anything.ts'), '');
    fs.writeFileSync(path.join(dir, 'modules/newthing/other.schema.ts'), '');
    fs.writeFileSync(path.join(dir, 'modules/newthing/plain.ts'), '');
    const files = findModelFiles(dir).filter((f) => f.startsWith('modules/'));
    fs.rmSync(dir, { recursive: true, force: true });
    expect(files).to.deep.equal(['modules/newthing/other.schema', 'modules/newthing/schema/anything']);
  });

  it('every Mongoose collection has a mapping or is listed as not copied', () => {
    const mapped = new Set([...MAPPINGS.map((m) => m.collection), ...IGNORED.map((i) => i.collection)]);
    const missing = MONGO_COLLECTIONS.filter((c) => !mapped.has(c));
    expect(missing, `collections without a mapping: ${missing.join(', ')}`).to.deep.equal([]);
    const unknown = [...mapped].filter((c) => !MONGO_COLLECTIONS.includes(c));
    expect(unknown, `mappings for collections the application does not define: ${unknown.join(', ')}`).to.deep.equal([]);
  });

  it('every mapping names a repository, and every repository that owns collections has importBatch', () => {
    for (const m of MAPPINGS) {
      expect(Object.keys(REPOSITORIES), `${m.collection}: repository ${m.repository}`).to.include(m.repository);
    }
  });

  it('collection names are mapped once', () => {
    const names = MAPPINGS.map((m) => m.collection);
    expect(names.filter((n, i) => names.indexOf(n) !== i)).to.deep.equal([]);
  });
});

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
      const cols = async (t: string): Promise<string[]> => {
        const [schema, name] = t.split('.');
        const r = await stack.admin((c) =>
          c.query("SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2", [schema, name]),
        );
        return r.rows.map((x: { column_name: string }) => x.column_name);
      };
      const problems: string[] = [];
      for (const m of MAPPINGS) {
        const have = await cols(m.table);
        if (!have.length) problems.push(`${m.collection}: table ${m.table} does not exist`);
        const named = new Set<string>([m.idCol, ...(m.orgCol ? [m.orgCol] : []), ...(m.versionCol ? [m.versionCol] : []), ...(m.nullsCol ? [m.nullsCol] : []), ...m.derived, ...m.fields.map((f) => f.col)]);
        for (const c of have) if (!named.has(c)) problems.push(`${m.collection}: column ${m.table}.${c} is neither mapped nor derived`);
        for (const c of named) if (have.length && !have.includes(c)) problems.push(`${m.collection}: ${m.table}.${c} does not exist`);
        for (const ch of m.children ?? []) {
          const hc = await cols(ch.table);
          const cn = new Set<string>([ch.parentCol, ch.orderCol, ...ch.derived, ...ch.fields.map((f) => f.col)]);
          for (const c of hc) if (!cn.has(c)) problems.push(`${m.collection}: column ${ch.table}.${c} is neither mapped nor derived`);
          for (const c of cn) if (hc.length && !hc.includes(c)) problems.push(`${m.collection}: ${ch.table}.${c} does not exist`);
        }
      }
      expect(problems).to.deep.equal([]);
    });

    it('every table of the document store is reached by a mapping or listed with a reason', async () => {
      const r = await stack.admin((c) =>
        c.query(
          `SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r','p') AND NOT c.relispartition AND n.nspname IN ('identity','conversation','content','oauth','platform','integrations')`,
        ),
      );
      const reached = new Set<string>(MAPPINGS.flatMap((m) => [m.table, ...(m.children ?? []).map((c) => c.table)]));
      const missing = r.rows.map((x: { t: string }) => x.t).filter((t: string) => !reached.has(t) && !(t in UNMAPPED_TABLES));
      expect(missing, `tables no mapping reaches: ${missing.join(', ')}`).to.deep.equal([]);
    });
  });
}
