/**
 * Coverage without a database (R45-MIGR, runs in `npm test`): every collection the application stores has a mapping, is listed as
 * not copied, or is held (preflight refuses a move while it holds documents); every mapping names a repository; every field of a
 * mapped schema is known to its mapping; and, read from the checked-in security surface snapshot (backend/db/surface.snapshot.txt,
 * which the db tests keep equal to the migrations), every table of the document store is reached or listed and every column of a
 * mapped table is mapped or derived. The database itest (coverage.itest.ts) checks the same tables on a migrated PostgreSQL.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import mongoose from 'mongoose';
import { CARRIED, HELD, IGNORED, MAPPINGS, OUTSIDE_GENERIC, fieldCarrier, splitOutside, withEditionHeldFields } from '../../../src/scripts/docstore-migrate/mapping';
import { BUILT_IN_CARRIERS, CARRIERS, EDITION_CARRIERS, asCarrier, carrierProblems } from '../../../src/scripts/docstore-migrate/carry';
import type { Carrier } from '../../../src/scripts/docstore-migrate/carry/types';
import { HARNESS_HELD } from '../../../src/scripts/docstore-migrate/mapping/harness';
import { EDITION_DOCSTORE_COVERAGE } from '../../../src/edition';
import { REPOSITORIES } from '../../../src/libs/repositories/all';
import { MIGRATOR_COLLECTIONS, MONGO_COLLECTIONS, driverCollections, findModelFiles } from '../../../src/scripts/docstore-migrate/mongo-collections';
import { heldFindings } from '../../../src/scripts/docstore-migrate/preflight';
import { ORG_STORE_COVERAGE, UNMODELLED_COLLECTIONS } from '../../../src/modules/agent-harness/domain/principal-erasure';
import { schemaDrift } from './schema-drift';
import { ALLOWED_UNMAPPED_SCHEMA_PATHS, DOC_STORE_SCHEMAS, UNMAPPED_TABLES, columnProblems, reachedTables, staleUnmapped, unreachedTables } from './coverage-lists';

const SNAPSHOT = path.resolve(__dirname, '../../../../../db/surface.snapshot.txt');

/**
 * The surface lines an edition's migration modules add (`EXTRA_MIGRATION_LOCATIONS` in app/edition.py, each with an
 * `allowlist.yaml` whose `surface:` items db.migrate merges into the expected state), so a table only an edition's
 * migrations create counts as created.
 */
function editionSurfaceLines(): string[] {
  const backend = path.resolve(__dirname, '../../../../..');
  const edition = fs.readFileSync(path.join(backend, 'python/app/edition.py'), 'utf8');
  const listed = /^EXTRA_MIGRATION_LOCATIONS[^=]*=\s*\[([^\]]*)\]/m.exec(edition)?.[1] ?? '';
  const lines: string[] = [];
  for (const [, location] of listed.matchAll(/["']([^"']+)["']/g)) {
    const allowlist = path.join(backend, location!.replace(/^backend\//, ''), 'allowlist.yaml');
    if (!fs.existsSync(allowlist)) continue;
    let section = '';
    for (const raw of fs.readFileSync(allowlist, 'utf8').split('\n')) {
      const item = raw.trim();
      if (item.endsWith(':') && !raw.startsWith(' ') && !raw.startsWith('-')) section = item.slice(0, -1);
      else if (section === 'surface' && item.startsWith('- ')) lines.push(item.slice(2).trim().replace(/^(["'])(.*)\1$/, '$2'));
    }
  }
  return lines;
}

function snapshot(): { tables: string[]; columns: Map<string, string[]> } {
  const tables: string[] = [];
  const columns = new Map<string, string[]>();
  for (const line of [...fs.readFileSync(SNAPSHOT, 'utf8').split('\n'), ...editionSurfaceLines()]) {
    const rel = /^rel (\w+\.\w+) kind=([rp]) /.exec(line);
    if (rel) tables.push(rel[1] as string);
    const col = /^column (\w+)\.(\w+)\.(\w+) /.exec(line);
    if (col) {
      const t = `${col[1]}.${col[2]}`;
      columns.set(t, [...(columns.get(t) ?? []), col[3] as string]);
    }
  }
  return { tables, columns };
}

const SRC = path.resolve(__dirname, '../../../src');
let srcText: string | undefined;
/** Whether the source names this model or collection: a model another test file registered in this worker is not the application's. */
function inSource(collection: string): boolean {
  if (srcText === undefined) {
    const parts: string[] = [];
    const walk = (dir: string): void => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) walk(full);
        else if (/\.ts$/.test(e.name)) parts.push(fs.readFileSync(full, 'utf8'));
      }
    };
    walk(SRC);
    srcText = parts.join('\n');
  }
  const names = [collection, ...Object.values(mongoose.models).filter((m) => m.collection.name === collection).map((m) => m.modelName)];
  return names.some((n) => srcText!.includes(`'${n}'`) || srcText!.includes(`"${n}"`));
}

/** Every collection the application stores: its models, the collections it names to the driver, and the erasure inventory. */
const appCollections = (): string[] =>
  [...new Set([...MONGO_COLLECTIONS.filter(inSource), ...driverCollections(), ...Object.keys(ORG_STORE_COVERAGE), ...UNMODELLED_COLLECTIONS])]
    .filter((c) => !MIGRATOR_COLLECTIONS.includes(c))
    .sort();

describe('docstore-migrate: coverage without a database (R45-MIGR)', () => {
  it('every field of every mapped Mongoose schema is known to its mapping (a new field fails here, not at an install\'s preflight)', () => {
    expect(schemaDrift(MAPPINGS, ALLOWED_UNMAPPED_SCHEMA_PATHS)).to.deep.equal([]);
  });

  it('the drift check sees a new field (mutation: a path added to a schema is reported, and removed again)', () => {
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
        else if (
          /\.ts$/.test(e.name) &&
          !e.name.endsWith('.d.ts') &&
          /(mongoose\.model|harnessModel|[^.\w]model)(<[^>(]*>)?\(\s*['"][\w-]+['"]\s*,/.test(fs.readFileSync(full, 'utf8')) &&
          !full.includes('/scripts/docstore-migrate/')
        )
          declaring.push(full.replace(/\.ts$/, ''));
      }
    };
    walk(src);
    expect(declaring.length, 'the scan sees the models').to.be.greaterThan(40);
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

  it('the driver scan finds a collection named to the driver, by literal or by a constant of the file (mutation)', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'driver-'));
    fs.writeFileSync(path.join(dir, 'a.ts'), "const X_COLL = 'qaConst';\nexport const f = (db: any) => [db.collection('qaLiteral'), db.collection<{ a: 1 }>(X_COLL)];\n");
    const names = driverCollections(dir);
    fs.rmSync(dir, { recursive: true, force: true });
    expect(names).to.deep.equal(['qaConst', 'qaLiteral']);
    expect(driverCollections()).to.include.members(['agentAuditRecords', 'agentHarnessProbes', 'agentDispatchOutbox']);
  });

  it('every collection the application stores is mapped, listed as not copied, carried or held, in exactly one list (R44SMALL-FU-3: none is dropped silently)', () => {
    const lists = [
      ...MAPPINGS.map((m) => m.collection),
      ...IGNORED.map((i) => i.collection),
      ...CARRIED.map((h) => h.collection),
      ...HELD.map((h) => h.collection),
    ];
    const app = appCollections();
    const missing = app.filter((c) => !lists.includes(c));
    expect(missing, `collections without a mapping, a not-copied reason or a hold: ${missing.join(', ')}`).to.deep.equal([]);
    expect(lists.filter((n, i) => lists.indexOf(n) !== i), 'collections in more than one list').to.deep.equal([]);
    const unknown = lists.filter((c) => !app.includes(c));
    expect(unknown, `entries for collections the application does not store: ${unknown.join(', ')}`).to.deep.equal([]);
    expect(app.length, 'the scan sees the harness collections too').to.be.greaterThan(80);
  });

  it('every reason says why (not empty), and a held collection of this build names the tables its data lives in', () => {
    for (const x of [...IGNORED, ...HELD, ...CARRIED]) expect(x.reason.length, x.collection).to.be.greaterThan(20);
    for (const c of CARRIERS) expect(c.reason.length, c.name).to.be.greaterThan(20);
    for (const h of HARNESS_HELD) expect(h.tables.length, h.collection).to.be.greaterThan(0);
    for (const m of MAPPINGS) for (const h of m.held ?? []) expect(h.reason.length, `${m.collection}.${h.path}`).to.be.greaterThan(20);
  });

  it("the edition's lists (src/edition.ts) are in the lists the move reads: its held and not-copied collections, and its held fields on their mapping", () => {
    for (const h of EDITION_DOCSTORE_COVERAGE.held) expect(OUTSIDE_GENERIC.map((x) => x.collection), h.collection).to.include(h.collection);
    // an edition's held collection is held unless one of its carriers carries it
    for (const h of EDITION_DOCSTORE_COVERAGE.held)
      expect([...HELD, ...CARRIED].map((x) => x.collection), h.collection).to.include(h.collection);
    for (const i of EDITION_DOCSTORE_COVERAGE.ignored) expect(IGNORED.map((x) => x.collection), i.collection).to.include(i.collection);
    for (const f of EDITION_DOCSTORE_COVERAGE.heldFields)
      expect(MAPPINGS.find((m) => m.collection === f.collection)?.held?.map((h) => h.path), `${f.collection}.${f.path}`).to.include(f.path);
  });

  it("an edition's held field joins its collection's mapping and keeps the build's own; one of a collection no mapping copies is refused (mutation)", () => {
    const users = MAPPINGS.find((m) => m.collection === 'users')!;
    const [joined] = withEditionHeldFields([users], [{ collection: 'users', path: 'qaEditionField', reason: 'an edition field for the test' }]);
    expect(joined!.held!.map((h) => h.path)).to.include.members([...(users.held ?? []).map((h) => h.path), 'qaEditionField']);
    expect(withEditionHeldFields([users], [])[0]).to.equal(users);
    expect(() => withEditionHeldFields([users], [{ collection: 'qaNoMapping', path: 'x', reason: 'r' }])).to.throw('qaNoMapping.x');
  });

  it('every mapping names a repository', () => {
    for (const m of MAPPINGS) expect(Object.keys(REPOSITORIES), `${m.collection}: repository ${m.repository}`).to.include(m.repository);
  });

  it('every table of the document store (surface snapshot) is reached by a mapping, named by a held or ignored collection, or listed with a reason', () => {
    const { tables } = snapshot();
    expect(tables.length, 'the snapshot lists the tables').to.be.greaterThan(100);
    expect(unreachedTables(tables)).to.deep.equal([]);
    const known = new Set(tables);
    const stale = [...reachedTables().keys(), ...Object.keys(UNMAPPED_TABLES)].filter((t) => !known.has(t));
    expect(stale, 'tables named that the migrations do not create').to.deep.equal([]);
    expect(DOC_STORE_SCHEMAS).to.include('harness');
  });

  it('no table is listed as not reached that a mapping, a carrier or a held collection reaches (a stale reason)', () => {
    expect(staleUnmapped()).to.deep.equal([]);
  });

  it('a new table is seen (mutation: a harness table nothing names is reported)', () => {
    expect(unreachedTables(['harness.qa_new_table', 'graph.not_document_store'])).to.deep.equal(['harness.qa_new_table']);
  });

  it('every column of a mapped table (surface snapshot) is mapped or derived, and every mapped column exists', () => {
    const { columns } = snapshot();
    expect(columnProblems((t) => columns.get(t) ?? [])).to.deep.equal([]);
  });

  it('preflight is red for a held collection or a held field that holds data, and silent while they are empty', () => {
    expect(heldFindings(new Map([['agentRuns', 0]]), new Map([['users.codingEngine', 0]]), [])).to.deep.equal([]);
    // with no carrier at all, every collection and field outside the generic path is held
    const red = heldFindings(new Map([['agentRuns', 3], ['cpGrants', 1]]), new Map([['chatSessions.codingEngine', 2]]), []);
    expect(red.map((f) => f.check)).to.deep.equal(['held_collection', 'held_collection', 'held_field']);
    expect(red[0]!.message).to.contain('agentRuns holds 3 documents').and.contain('stay on MongoDB');
    expect(red[0]!.data).to.deep.include({ tables: ['harness.runs'] });
    expect(red[2]!.message).to.contain('2 chatSessions documents hold codingEngine');
  });

  it('R45-CARRY: a carried collection or field is not held (preflight lets the move go on), and a held one still is', () => {
    const counts = new Map([['agentHarnessDataKeys', 2], ['agentWorkspaceHeads', 1], ['agentRuns', 3], ['agentAuditChainChecks', 4]]);
    const fields = new Map([['chatSessions.codingEngine', 2], ['users.codingEngine', 1], ['org.defaultCodingEngine', 1]]);
    const red = heldFindings(counts, fields);
    expect(red.map((f) => (f.data as { collection: string }).collection)).to.deep.equal(['agentAuditChainChecks']);
    expect(CARRIED.map((c) => c.collection)).to.include.members([
      'agentHarnessDataKeys',
      'agentWorkspaceHeads',
      'agentWorkspaceManifests',
      'agentWorkspaceKeys',
      'agentAuditRecords',
      'agentAuditHeads',
      'agentAuditPending',
      'agentAuditAnchors',
    ]);
    // this build's held fields; an edition's own (src/edition.ts heldFields) stay held unless one of its carriers carries them
    const editionField = (c: string, p: string): boolean => EDITION_DOCSTORE_COVERAGE.heldFields.some((f) => f.collection === c && f.path === p);
    for (const m of MAPPINGS)
      for (const h of m.held ?? [])
        if (!editionField(m.collection, h.path)) expect(fieldCarrier(m.collection, h.path), `${m.collection}.${h.path}`).to.be.a('string');
  });

  it('R46-CARRY: every group of this build is carried whole; only the collection an edition writes stays held', () => {
    const editionHeld = new Set(EDITION_DOCSTORE_COVERAGE.held.map((h) => h.collection));
    expect(HELD.map((h) => h.collection).filter((c) => !editionHeld.has(c)).sort()).to.deep.equal(
      ['agentAuditChainChecks'],
    );
    const by = (name: string): string[] => CARRIED.filter((c) => c.carrier === name).map((c) => c.collection).sort();
    expect(by('durable-runs')).to.deep.equal(
      ['agentRuns', 'agentEvents', 'agentEventCounters', 'agentNextRuns', 'sessionEvents', 'sessionStreamCounters', 'agentJournalPayloads', 'agentSessionForks', 'agentSandboxReaps', 'agentSandboxSnapshots', 'agentCommandReceipts', 'agentAdmissionReceipts', 'agentAdmissionQuotas'].sort(),
    );
    expect(by('control-plane')).to.deep.equal(
      ['cpOrgHeads', 'cpPolicyLayers', 'cpPolicyHistory', 'cpMigrationLedger', 'cpCatalogItems', 'cpInstallations', 'cpInstallationHistory', 'cpGrants', 'cpCapabilityLedger', 'cpBindings'].sort(),
    );
    expect([...by('ledgers'), ...by('peoples-data'), ...by('erasure')].sort()).to.deep.equal(
      [
        'agentHarnessBudgetAccounts', 'agentHarnessBudgetReservations',
        'agentEngineSignIns', 'agentMemories', 'agentSchedules', 'agentScheduleFirings', 'agentScheduleCounters', 'agentHarnessInboxPreferences', 'agentHarnessRunEndNotices',
        'agentSessionErasures', 'agentErasures', 'agentPrincipalErasures', 'agentErasureBackfillPlans', 'agentErasureBackfillItems', 'agentErasureBackfillMarkers',
      ].sort(),
    );
  });

  it('R45-CARRY: every carrier carries only what the mappings hold, nothing twice, and names the tables it writes', () => {
    const heldCollections = new Set(OUTSIDE_GENERIC.map((h) => h.collection));
    const heldFields = new Set(MAPPINGS.flatMap((m) => (m.held ?? []).map((h) => `${m.collection}.${h.path}`)));
    expect(carrierProblems(CARRIERS, heldCollections, heldFields)).to.deep.equal([]);
    const twice = [...BUILT_IN_CARRIERS, { ...BUILT_IN_CARRIERS[1]!, name: 'again' } as Carrier];
    expect(carrierProblems(twice, heldCollections, heldFields).join(' ')).to.contain('carried by both data-keys-and-workspaces and again');
    const mapped = { ...BUILT_IN_CARRIERS[1]!, name: 'bad', collections: ['users'], fields: { users: ['email'] } } as Carrier;
    expect(carrierProblems([mapped], heldCollections, heldFields)).to.deep.equal([
      'bad carries users, which is not a held collection',
      'bad carries users.email, which is not a held field',
    ]);
  });

  it("R45-CARRY: an edition's carriers come from the one edition seam (EDITION_DOCSTORE_COVERAGE.carriers), checked for their shape", () => {
    expect(EDITION_CARRIERS).to.have.length(EDITION_DOCSTORE_COVERAGE.carriers.length);
    expect(CARRIERS.map((c) => c.name)).to.deep.equal([...BUILT_IN_CARRIERS, ...EDITION_CARRIERS].map((c) => c.name));
    expect(asCarrier(BUILT_IN_CARRIERS[1], 0)).to.equal(BUILT_IN_CARRIERS[1]);
    expect(() => asCarrier({ name: 'half', collections: [], fields: {}, tables: [], reason: 'r', write: () => undefined }, 2)).to.throw(
      'edition carrier 2 (half) is not a docstore-migrate Carrier: remove, expected, actual, restore, clear',
    );
    expect(() => asCarrier(null, 0)).to.throw('not an object');
  });

  it("R45-CARRY: an edition's own held collection stays held until a carrier carries it, and its carrier may carry it", () => {
    const own = { collection: 'qaEditionLedger', reason: 'an edition ledger for the test', tables: [] as string[] };
    const outside = [...OUTSIDE_GENERIC, own];
    const edition = { ...BUILT_IN_CARRIERS[1]!, name: 'qa-edition', collections: ['qaEditionLedger'], tables: ['harness.qa'] } as Carrier;
    const held = new Set(outside.map((h) => h.collection));
    const fields = new Set(MAPPINGS.flatMap((m) => (m.held ?? []).map((h) => `${m.collection}.${h.path}`)));
    expect(carrierProblems([...BUILT_IN_CARRIERS, edition], held, fields)).to.deep.equal([]);
    expect(carrierProblems([edition], new Set(OUTSIDE_GENERIC.map((h) => h.collection)), new Set())).to.deep.equal([
      'qa-edition carries qaEditionLedger, which is not a held collection',
    ]);
  });

  it("R45-CARRY: an edition's carrier un-holds its collections the way a built-in one does (the extension point)", () => {
    const before = splitOutside(BUILT_IN_CARRIERS);
    const collection = before.held[0]!.collection;
    const edition = { ...BUILT_IN_CARRIERS[1]!, name: 'edition-carrier', collections: [collection], tables: [] } as Carrier;
    const after = splitOutside([...BUILT_IN_CARRIERS, edition]);
    expect(after.held.map((h) => h.collection)).not.to.include(collection);
    expect(after.carried.find((c) => c.collection === collection)?.carrier).to.equal('edition-carrier');
    expect(heldFindings(new Map([[collection, 1]]), new Map(), [...BUILT_IN_CARRIERS, edition])).to.deep.equal([]);
    expect(heldFindings(new Map([[collection, 1]]), new Map()).map((f) => f.check)).to.deep.equal(['held_collection']);
  });
});
