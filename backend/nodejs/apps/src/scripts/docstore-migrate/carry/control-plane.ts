/**
 * The control plane (R46-CARRY, R45CARRY-FU-3), carried as one unit: each org's revision head and the deployment's, its policy
 * layers and their history, the data-migration ledger, the catalog, installations and their history, grants, the capability
 * ledger and the bindings. Revisions are carried exactly: a revision restarted at one would match a cached ETag of another state.
 *
 *  - `cpOrgHeads` -> `harness.cp_org_heads` (an org's row, `_id` is the org) and `harness.cp_deployment_head` (the `$deployment`
 *    row, no org's). The ordered-steps writer's lease (standalone MongoDB only) is not carried: an idle one is dropped (listed), and
 *    a lease that recorded an intent is an unfinished write whose audit a takeover completes, so the move refuses it;
 *  - the rest -> one table each, typed columns as the PostgreSQL stores write them; JSON texts (`body`, `entry`) stay texts, so
 *    their digests and the stored-form probes read the same bytes. Their MongoDB ids are composed from the row's key, so a row
 *    made on PostgreSQL after the move gets the id the MongoDB store would give it at a rollback;
 *  - `cpBindings` -> `harness.cp_bindings`: the binding in `doc` with its id (the document's `_id`), the refresher's claim in
 *    `refresh_holder`/`refresh_until`.
 *
 * On top of the document-by-document compare, each org's version chains are re-checked on both sides (`check`): every policy
 * layer's and installation's current revision has its history row with the same body, and history revisions run 1..current
 * without a gap. The verdicts must be the same on both sides: a chain whole on MongoDB is whole on PostgreSQL, and one already
 * broken stays visibly broken.
 */
import type { Tx as KernelTx } from '../../../libs/db';
import { rowRegion } from '../../../libs/repositories/agent-harness/pg-documents/pg';
import { DocTable, docTableCarrier, type DocTableSpec } from './doc-table';
import type { Carrier, CarryMongo, Doc, Restored, SourceDoc } from './types';

/** The deployment's row in `cpOrgHeads` (`DEPLOYMENT_HEAD_ID` of the MongoDB head store). */
export const DEPLOYMENT_HEAD_ID = '$deployment';

const str = (d: Doc, p: string): string => String(d[p]);

export const CONTROL_PLANE_TABLES: DocTableSpec[] = [
  {
    collection: 'cpPolicyLayers',
    table: 'harness.cp_policy_layers',
    orgPath: 'orgId',
    key: [{ col: 'scope', path: 'scope', kind: 'text' }],
    columns: [
      { col: 'kind', path: 'kind', kind: 'text' },
      { col: 'revision', path: 'revision', kind: 'int' },
      { col: 'body', path: 'body', kind: 'text', absentAs: null },
      // the approver fence's counter (QA21-F2); a layer never fenced has none
      {
        col: 'decision_fence',
        path: 'decisionFence',
        kind: 'int',
        absentAs: 0,
      },
      { col: 'updated_at', path: 'updatedAt', kind: 'date' },
    ],
    newId: (d) => ({ id: `${str(d, 'orgId')}:${str(d, 'scope')}`, oid: false }),
  },
  {
    collection: 'cpPolicyHistory',
    table: 'harness.cp_policy_history',
    orgPath: 'orgId',
    key: [
      { col: 'scope', path: 'scope', kind: 'text' },
      { col: 'revision', path: 'revision', kind: 'int' },
    ],
    columns: [
      { col: 'body', path: 'body', kind: 'text', absentAs: null },
      { col: 'at', path: 'at', kind: 'date' },
    ],
    newId: (d) => ({
      id: `${str(d, 'orgId')}:${str(d, 'scope')}:${str(d, 'revision')}`,
      oid: false,
    }),
  },
  {
    collection: 'cpMigrationLedger',
    table: 'harness.cp_migration_ledger',
    orgPath: 'orgId',
    key: [{ col: 'migration_id', path: 'migrationId', kind: 'text' }],
    columns: [
      { col: 'applied_at', path: 'appliedAt', kind: 'date' },
      { col: 'entry', path: 'entry', kind: 'text' },
    ],
    newId: (d) => ({
      id: `${str(d, 'migrationId')}:${str(d, 'orgId')}`,
      oid: false,
    }),
  },
  {
    collection: 'cpCatalogItems',
    table: 'harness.cp_catalog_items',
    orgPath: 'orgId',
    key: [{ col: 'ref', path: 'ref', kind: 'text' }],
    columns: [
      { col: 'kind', path: 'kind', kind: 'text' },
      { col: 'publisher', path: 'publisher', kind: 'text' },
      { col: 'name', path: 'name', kind: 'text' },
      { col: 'version', path: 'version', kind: 'text' },
      { col: 'sort_name', path: 'sortName', kind: 'text' },
      { col: 'created_at', path: 'createdAt', kind: 'int' },
      { col: 'deprecated', path: 'deprecated', kind: 'bool' },
      { col: 'body', path: 'body', kind: 'text' },
    ],
    newId: (d) => ({ id: `${str(d, 'orgId')}|${str(d, 'ref')}`, oid: false }),
  },
  {
    collection: 'cpInstallations',
    table: 'harness.cp_installations',
    orgPath: 'orgId',
    key: [{ col: 'id', path: 'id', kind: 'text' }],
    columns: [
      { col: 'item', path: 'item', kind: 'text' },
      { col: 'kind', path: 'kind', kind: 'text' },
      { col: 'owner', path: 'owner', kind: 'text' },
      { col: 'status', path: 'status', kind: 'text' },
      {
        col: 'installed_via',
        path: 'installedVia',
        kind: 'text',
        absentAs: null,
      },
      { col: 'revision', path: 'revision', kind: 'int' },
      { col: 'body', path: 'body', kind: 'text' },
    ],
    newId: (d) => ({ id: `${str(d, 'orgId')}|${str(d, 'id')}`, oid: false }),
  },
  {
    collection: 'cpInstallationHistory',
    table: 'harness.cp_installation_history',
    orgPath: 'orgId',
    key: [
      { col: 'id', path: 'id', kind: 'text' },
      { col: 'revision', path: 'revision', kind: 'int' },
    ],
    columns: [{ col: 'body', path: 'body', kind: 'text' }],
    newId: (d) => ({
      id: `${str(d, 'orgId')}|${str(d, 'id')}:${str(d, 'revision')}`,
      oid: false,
    }),
  },
  {
    collection: 'cpGrants',
    table: 'harness.cp_grants',
    orgPath: 'orgId',
    key: [{ col: 'subject', path: 'subject', kind: 'text' }],
    columns: [
      { col: 'entries', path: 'entries', kind: 'json', absentAs: [] },
      { col: 'revision', path: 'revision', kind: 'int' },
    ],
    newId: (d) => ({
      id: `${str(d, 'orgId')}:${str(d, 'subject')}`,
      oid: false,
    }),
  },
  {
    collection: 'cpCapabilityLedger',
    table: 'harness.cp_capability_ledger',
    orgPath: 'orgId',
    key: [
      { col: 'object', path: 'object', kind: 'text' },
      { col: 'id', path: 'id', kind: 'text' },
    ],
    columns: [{ col: 'revision', path: 'revision', kind: 'int' }],
    newId: (d) => ({
      id: `${str(d, 'orgId')}|${str(d, 'object')}|${str(d, 'id')}`,
      oid: false,
    }),
  },
  {
    collection: 'cpBindings',
    table: 'harness.cp_bindings',
    orgPath: 'orgId',
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    idCol: 'id',
    columns: [
      { col: 'installation', path: 'installation', kind: 'text' },
      { col: 'owner', path: 'owner', kind: 'text' },
      { col: 'status', path: 'status', kind: 'text' },
      { col: 'expires_at', path: 'expiresAt', kind: 'int', nullable: true },
      { col: 'revision', path: 'revision', kind: 'int' },
      {
        col: 'refresh_holder',
        path: 'refreshClaim.holder',
        kind: 'text',
        nullable: true,
      },
      {
        col: 'refresh_until',
        path: 'refreshClaim.until',
        kind: 'int',
        nullable: true,
      },
    ],
    // the binding as the store returns it, its id included; the refresher's claim is in its own columns
    doc: {
      omit: ['refreshClaim', 'refreshClaim.holder', 'refreshClaim.until'],
      idAs: 'id',
    },
  },
];

const HEADS = 'cpOrgHeads';

/** A head document as PostgreSQL keeps it: revision and time; refused when its lease holds an unfinished write. */
function headOf(src: SourceDoc, note?: (t: string) => void): Doc {
  const lease = src.doc.lease as { intent?: unknown } | null | undefined;
  if (lease !== null && lease !== undefined) {
    if (lease.intent !== null && lease.intent !== undefined)
      throw new Error(
        `${HEADS} ${src.id}: an unfinished control-plane write (its lease recorded an intent); start the application so a writer completes it, then copy again`,
      );
    note?.(`${HEADS}: an idle ordered-steps lease is not carried`);
  }
  const { revision, updatedAt } = src.doc;
  if (typeof revision !== 'number' || !Number.isSafeInteger(revision))
    throw new Error(`${HEADS} ${src.id}: revision is not an integer`);
  if (!(updatedAt instanceof Date))
    throw new Error(`${HEADS} ${src.id}: updatedAt is not a date`);
  return { revision, updatedAt, lease: null };
}

/** `cpOrgHeads`: an org's rows in `cp_org_heads` (org = `_id`), the deployment's in `cp_deployment_head`. */
const orgHeads = {
  async write(k: KernelTx, docs: SourceDoc[]): Promise<void> {
    for (const src of docs) {
      const h = headOf(src);
      if (src.id === DEPLOYMENT_HEAD_ID)
        await k.query(
          `/* pipeshub:migrate.carry_cp_deployment_head */ INSERT INTO harness.cp_deployment_head (id, revision, updated_at)
             VALUES ('deployment', $1, $2) ON CONFLICT (id) DO UPDATE SET revision = EXCLUDED.revision, updated_at = EXCLUDED.updated_at`,
          [h.revision, h.updatedAt],
        );
      else
        await k.query(
          `/* pipeshub:migrate.carry_cp_org_head */ INSERT INTO harness.cp_org_heads (org_id, revision, updated_at, region)
             VALUES ($1, $2, $3, $4) ON CONFLICT (org_id) DO UPDATE SET revision = EXCLUDED.revision, updated_at = EXCLUDED.updated_at`,
          [src.id, h.revision, h.updatedAt, rowRegion()],
        );
    }
  },
  async remove(k: KernelTx, ids: string[]): Promise<void> {
    if (ids.includes(DEPLOYMENT_HEAD_ID))
      await k.query(
        `/* pipeshub:migrate.carry_cp_deployment_head_remove */ DELETE FROM harness.cp_deployment_head`,
      );
    const orgs = ids.filter((i) => i !== DEPLOYMENT_HEAD_ID);
    if (orgs.length)
      await k.query(
        `/* pipeshub:migrate.carry_cp_org_head_remove */ DELETE FROM harness.cp_org_heads WHERE org_id = ANY($1::text[])`,
        [orgs],
      );
  },
  expected(
    src: SourceDoc,
    note: (t: string) => void,
  ): Array<[string, unknown]> {
    return [[src.id, { _id: src.id, ...headOf(src, note) }]];
  },
  async actual(k: KernelTx): Promise<Map<string, unknown>> {
    const { rows } = await k.query<{
      id: string;
      revision: string;
      updated_at: Date;
    }>(
      `/* pipeshub:migrate.carry_cp_heads */ SELECT org_id AS id, revision::text, updated_at FROM harness.cp_org_heads
       UNION ALL SELECT $1, revision::text, updated_at FROM harness.cp_deployment_head`,
      [DEPLOYMENT_HEAD_ID],
    );
    return new Map(
      rows.map((r) => [
        r.id,
        {
          _id: r.id,
          revision: Number(r.revision),
          updatedAt: new Date(r.updated_at),
          lease: null,
        },
      ]),
    );
  },
  async restore(k: KernelTx, mongo: CarryMongo): Promise<Restored> {
    const docs: SourceDoc[] = [...(await orgHeads.actual(k)).values()].map(
      (v) => {
        const { _id, ...doc } = v as Doc;
        return { id: String(_id), oid: false, doc };
      },
    );
    await mongo.replace(HEADS, docs);
    const keep = new Set(docs.map((d) => d.id));
    const gone: Array<{ id: string; oid: boolean }> = [];
    for await (const d of mongo.scan(HEADS, { pick: [] }))
      if (!keep.has(d.id)) gone.push({ id: d.id, oid: d.oid });
    return {
      written: docs.length,
      removed: gone.length ? await mongo.remove(HEADS, gone) : 0,
    };
  },
  async clear(k: KernelTx): Promise<void> {
    await k.query(
      `/* pipeshub:migrate.carry_cp_heads_clear */ DELETE FROM harness.cp_org_heads`,
    );
    await k.query(
      `/* pipeshub:migrate.carry_cp_deployment_head_clear */ DELETE FROM harness.cp_deployment_head`,
    );
  },
};

interface Versioned {
  /** org -> object (scope or installation id) -> current revision and body */
  current: Map<string, Map<string, { revision: number; body: unknown }>>;
  /** org -> object -> revision -> body */
  history: Map<string, Map<string, Map<number, unknown>>>;
}

const emptyVersioned = (): Versioned => ({
  current: new Map(),
  history: new Map(),
});

const sub = <K, V>(m: Map<K, V>, k: K, make: () => V): V => {
  let v = m.get(k);
  if (v === undefined) {
    v = make();
    m.set(k, v);
  }
  return v;
};

/** Each org's problems with one chain kind, as sorted text (ids and revisions only, never a body). */
function chainProblems(
  kind: string,
  v: Versioned,
  out: Map<string, string[]>,
): void {
  const orgs = new Set([...v.current.keys(), ...v.history.keys()]);
  for (const org of orgs) {
    const problems = sub(out, org, () => []);
    const cur =
      v.current.get(org) ??
      new Map<string, { revision: number; body: unknown }>();
    const hist = v.history.get(org) ?? new Map<string, Map<number, unknown>>();
    for (const [obj, c] of cur) {
      const h = hist.get(obj);
      if (!h?.has(c.revision))
        problems.push(
          `${kind} ${obj}: revision ${String(c.revision)} has no history`,
        );
      else if (JSON.stringify(h.get(c.revision)) !== JSON.stringify(c.body))
        problems.push(
          `${kind} ${obj}: revision ${String(c.revision)} differs from its history`,
        );
    }
    for (const [obj, h] of hist) {
      const top = Math.max(...h.keys());
      const c = cur.get(obj);
      if (c && top > c.revision)
        problems.push(`${kind} ${obj}: history ahead of the current revision`);
      for (let r = 1; r <= top; r += 1)
        if (!h.has(r))
          problems.push(
            `${kind} ${obj}: history revision ${String(r)} missing`,
          );
    }
  }
}

function verdicts(policy: Versioned, installs: Versioned): Map<string, string> {
  const out = new Map<string, string[]>();
  chainProblems('policy', policy, out);
  chainProblems('installation', installs, out);
  return new Map(
    [...out].map(([org, p]) => [org, JSON.stringify([...p].sort())]),
  );
}

function addDoc(
  v: Versioned,
  coll: 'current' | 'history',
  objPath: string,
  d: Doc,
): void {
  const org = String(d.orgId);
  const obj = String(d[objPath]);
  const revision = Number(d.revision);
  if (coll === 'current')
    sub(v.current, org, () => new Map()).set(obj, {
      revision,
      body: d.body ?? null,
    });
  else
    sub(
      sub(v.history, org, () => new Map<string, Map<number, unknown>>()),
      obj,
      () => new Map<number, unknown>(),
    ).set(revision, d.body ?? null);
}

const spec = (collection: string): DocTableSpec =>
  CONTROL_PLANE_TABLES.find((s) => s.collection === collection) as DocTableSpec;

const CHAINS: Array<
  [string, 'policy' | 'installs', 'current' | 'history', string]
> = [
  ['cpPolicyLayers', 'policy', 'current', 'scope'],
  ['cpPolicyHistory', 'policy', 'history', 'scope'],
  ['cpInstallations', 'installs', 'current', 'id'],
  ['cpInstallationHistory', 'installs', 'history', 'id'],
];

/** The version chains re-checked on both sides: an org whose verdict differs is a problem (ids and revisions, no body). */
export async function controlPlaneChainProblems(
  k: KernelTx,
  mongo: CarryMongo,
): Promise<string[]> {
  const before = { policy: emptyVersioned(), installs: emptyVersioned() };
  const after = { policy: emptyVersioned(), installs: emptyVersioned() };
  for (const [coll, which, part, obj] of CHAINS) {
    for await (const d of mongo.scan(coll))
      addDoc(before[which], part, obj, d.doc);
    for (const v of (await new DocTable(spec(coll)).actual(k)).values())
      addDoc(after[which], part, obj, v as Doc);
  }
  const m = verdicts(before.policy, before.installs);
  const p = verdicts(after.policy, after.installs);
  const problems: string[] = [];
  for (const org of new Set([...m.keys(), ...p.keys()]))
    if (m.get(org) !== p.get(org))
      problems.push(
        `the control-plane version chains of ${org} verify differently: MongoDB ${m.get(org) ?? 'none'}, PostgreSQL ${p.get(org) ?? 'none'}`,
      );
  return problems;
}

const tables = docTableCarrier('control-plane', '', CONTROL_PLANE_TABLES);

export const CONTROL_PLANE: Carrier = {
  ...tables,
  name: 'control-plane',
  reason:
    "the control plane: each org's revision head and the deployment's, policy layers and history, the migration ledger, catalog, installations and their history, grants, the capability ledger and bindings, revisions carried exactly",
  collections: [HEADS, ...tables.collections],
  tables: [
    'harness.cp_org_heads',
    'harness.cp_deployment_head',
    ...tables.tables,
  ],
  write: (k, c, docs) =>
    c === HEADS ? orgHeads.write(k, docs) : tables.write(k, c, docs),
  remove: (k, c, ids) =>
    c === HEADS ? orgHeads.remove(k, ids) : tables.remove(k, c, ids),
  expected: (c, doc, note) =>
    c === HEADS
      ? orgHeads.expected(doc, note ?? (() => undefined))
      : tables.expected(c, doc, note),
  actual: (k, c) => (c === HEADS ? orgHeads.actual(k) : tables.actual(k, c)),
  restore: (k, c, mongo) =>
    c === HEADS ? orgHeads.restore(k, mongo) : tables.restore(k, c, mongo),
  async clear(k) {
    await tables.clear(k);
    await orgHeads.clear(k);
  },
  check: controlPlaneChainProblems,
};
