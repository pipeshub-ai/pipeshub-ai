/**
 * The control-plane carrier on a migrated PostgreSQL (R46-CARRY, R45CARRY-FU-3), with MongoDB in memory: documents shaped as the
 * MongoDB control-plane stores write them are copied, verified, and read back through the PostgreSQL control-plane stores (heads,
 * policy layers and history, catalog, installations and history, grants, capability and migration ledgers, bindings); revisions
 * come over exactly; the forward follower applies a write and deletes; the version chains are re-checked on both sides; a rollback
 * after the application wrote on PostgreSQL gives every document its id back; reset clears it all.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Timestamp } from 'mongodb';
import { system } from '../../../src/libs/context/orgContext';
import { withJobsUow } from '../../../src/libs/db';
import type { Tx as KernelTx } from '../../../src/libs/db';
import { PgTx } from '../../../src/libs/repositories/pg';
import { PgHeadRevisions } from '../../../src/libs/repositories/control-plane/head-revisions/pg';
import { PgPolicyStore } from '../../../src/libs/repositories/control-plane/policy/pg';
import { PgBindingStore } from '../../../src/libs/repositories/control-plane/bindings/pg';
import { PgInstallationStore } from '../../../src/libs/repositories/control-plane/installations/pg';
import { PgGrantStore } from '../../../src/libs/repositories/control-plane/grant-store/pg';
import { PgCapabilityLedger } from '../../../src/libs/repositories/control-plane/capability-ledger/pg';
import { PgMigrationLedger } from '../../../src/libs/repositories/control-plane/migration-ledger/pg';
import { PgOrgCatalogStore } from '../../../src/libs/repositories/control-plane/catalog/pg';
import { nameKey } from '../../../src/modules/control-plane/domain/catalog-order';
import { CARRIERS } from '../../../src/scripts/docstore-migrate/carry';
import { CONTROL_PLANE } from '../../../src/scripts/docstore-migrate/carry/control-plane';
import {
  clearCarried,
  copyCarried,
  restoreCarried,
  verifyCarried,
  type CarryPg,
} from '../../../src/scripts/docstore-migrate/carry/run';
import { follow } from '../../../src/scripts/docstore-migrate/follow';
import type { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import type { JobState } from '../../../src/scripts/docstore-migrate/state';
import {
  PgStack,
  PG_BINS,
  haveStack,
  inOrg,
  startPgStack,
} from '../../stores/pg-store-harness';
import { FakeCarryMongo, clone } from './fake-carry-mongo';
import {
  ORG_A,
  aBinding,
  aGrant,
  anInstallation,
  bindingId,
  instId,
} from '../../modules/control-plane/support/cp-objects';
import { catalogItem } from '../../modules/control-plane/support/catalog-fixtures';

const suite = haveStack ? describe : describe.skip;
const ORG = ORG_A;
const only = [CONTROL_PLANE];

const pg: CarryPg = (fn, write) =>
  system('carry-cp-itest', () => withJobsUow({ write, service: 'import' }, fn));
const sql = <T extends Record<string, unknown>>(
  text: string,
  args: unknown[] = [],
): Promise<T[]> => pg(async (k) => (await k.query<T>(text, args)).rows, false);

const at = (ms: number): Date => new Date(1_700_000_000_000 + ms);
const orgLayer = (maxUsd: number): string =>
  JSON.stringify({
    schemaVersion: 1,
    scope: { kind: 'org', id: ORG },
    engineProfile: { limits: { maxUsd } },
  });
const item = catalogItem({ name: 'carried-skill' });
const inst = anInstallation({ revision: 2, updatedAt: 2 });
const inst1 = anInstallation({ revision: 1, updatedAt: 1 });
const binding = aBinding({ revision: 3, expiresAt: 1_800_000_000_000 });

/** The documents as the MongoDB stores write them (ids as each store composes them). */
function seed(): Record<string, Record<string, unknown>[]> {
  const { deprecated, ...itemBody } = item;
  const { id, ...bindingDoc } = binding;
  return {
    cpOrgHeads: [
      { _id: ORG, revision: 7, updatedAt: at(7), lease: null },
      // an idle lease of the ordered-steps writer (standalone MongoDB): not carried
      {
        _id: '$deployment',
        revision: 3,
        updatedAt: at(3),
        lease: { token: 't', until: 1, intent: null },
      },
    ],
    cpPolicyLayers: [
      {
        _id: `${ORG}:org:${ORG}`,
        orgId: ORG,
        scope: `org:${ORG}`,
        kind: 'org',
        revision: 2,
        body: orgLayer(2),
        updatedAt: at(2),
        decisionFence: 4,
      },
      // a deleted layer (body null), never fenced
      {
        _id: `${ORG}:agent:a1`,
        orgId: ORG,
        scope: 'agent:a1',
        kind: 'agent',
        revision: 1,
        body: null,
        updatedAt: at(1),
      },
    ],
    cpPolicyHistory: [
      {
        _id: `${ORG}:org:${ORG}:1`,
        orgId: ORG,
        scope: `org:${ORG}`,
        revision: 1,
        body: orgLayer(1),
        at: at(1),
      },
      {
        _id: `${ORG}:org:${ORG}:2`,
        orgId: ORG,
        scope: `org:${ORG}`,
        revision: 2,
        body: orgLayer(2),
        at: at(2),
      },
      {
        _id: `${ORG}:agent:a1:1`,
        orgId: ORG,
        scope: 'agent:a1',
        revision: 1,
        body: null,
        at: at(1),
      },
    ],
    cpMigrationLedger: [
      {
        _id: `capabilities-v1:${ORG}`,
        migrationId: 'capabilities-v1',
        orgId: ORG,
        appliedAt: at(5),
        entry: JSON.stringify({ orgId: ORG, n: 1 }),
      },
    ],
    cpCatalogItems: [
      {
        _id: `${ORG}|${item.ref}`,
        orgId: ORG,
        ref: item.ref,
        kind: item.kind,
        publisher: item.publisher,
        name: item.name,
        version: item.version,
        sortName: nameKey(item),
        createdAt: item.createdAt,
        deprecated,
        body: JSON.stringify(itemBody),
      },
    ],
    cpInstallations: [
      {
        _id: `${ORG}|${inst.id}`,
        orgId: ORG,
        id: inst.id,
        item: inst.item,
        kind: inst.kind,
        owner: inst.owner,
        status: inst.status,
        installedVia: null,
        revision: 2,
        body: JSON.stringify(inst),
      },
    ],
    cpInstallationHistory: [
      {
        _id: `${ORG}|${inst.id}:1`,
        orgId: ORG,
        id: inst.id,
        revision: 1,
        body: JSON.stringify(inst1),
      },
      {
        _id: `${ORG}|${inst.id}:2`,
        orgId: ORG,
        id: inst.id,
        revision: 2,
        body: JSON.stringify(inst),
      },
    ],
    // the MongoDB grant document has no schemaVersion: the store adds it when it reads
    cpGrants: [
      {
        _id: `${ORG}:agent:a7`,
        orgId: ORG,
        subject: 'agent:a7',
        entries: aGrant().entries,
        revision: 1,
      },
    ],
    cpCapabilityLedger: [
      {
        _id: `${ORG}|installation|${inst.id}`,
        orgId: ORG,
        object: 'installation',
        id: inst.id,
        revision: 1,
      },
    ],
    cpBindings: [
      {
        _id: id,
        ...bindingDoc,
        refreshClaim: { holder: 'replica-1', until: 1_700_000_000_500 },
      },
    ],
  };
}

type Event = Record<string, unknown>;
let token = 0;
const event = (
  op: 'insert' | 'update' | 'delete',
  coll: string,
  doc: Record<string, unknown>,
): Event => ({
  _id: { t: (token += 1) },
  operationType: op,
  ns: { db: 'pipeshub', coll },
  documentKey: { _id: doc._id },
  ...(op === 'delete' ? {} : { fullDocument: clone(doc) }),
  clusterTime: new Timestamp({ t: Math.floor(Date.now() / 1000), i: token }),
});

function scriptedRun(events: Event[]): MigrationRuntime {
  const queue = [...events];
  let last: unknown = null;
  const stream = {
    tryNext: async () => {
      const e = queue.shift() ?? null;
      if (e) last = e._id;
      return e;
    },
    get resumeToken() {
      return last;
    },
    close: async () => undefined,
  };
  return {
    cfg: { mongoDb: 'pipeshub', lagAlarmMs: 60_000, batchSize: 100 },
    mongo: {
      watch: () => stream,
      db: (name: string) =>
        name === 'admin'
          ? {
              command: async () => ({
                optimes: {
                  lastCommittedOpTime: {
                    ts: new Timestamp({ t: 2 ** 31 - 1, i: 0 }),
                  },
                },
              }),
            }
          : { collection: () => ({ findOne: async () => null }) },
    },
    db: () => ({ collection: () => ({}) }),
    jobs: <T>(
      fn: (tx: PgTx, k: KernelTx) => Promise<T>,
      opts: { write?: boolean } = {},
    ) => pg((k) => fn(new PgTx(k), k), opts.write !== false),
  } as unknown as MigrationRuntime;
}

const job = (): JobState =>
  ({
    progress: {
      follow: { resumeToken: { t: 0 }, appliedUntil: null, events: 0 },
    },
    checkpoint: async () => undefined,
  }) as unknown as JobState;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(
    `docstore-migrate: the control plane carried to PostgreSQL (R46-CARRY, R45CARRY-FU-3, ${bin})`,
    function () {
      this.timeout(180_000);
      let stack: PgStack;
      let mongo: FakeCarryMongo;
      before(async () => {
        process.env.DOC_STORE = 'postgres';
        stack = await startPgStack(bin, 'migrate_carry_cp', {
          importer: true,
          jobs: true,
        });
      });
      after(async () => {
        await stack?.stop();
      });

      it('is a built-in carrier of all ten control-plane collections, writing both head tables', () => {
        expect(CARRIERS).to.include(CONTROL_PLANE);
        expect(CONTROL_PLANE.collections).to.have.members([
          'cpOrgHeads',
          'cpPolicyLayers',
          'cpPolicyHistory',
          'cpMigrationLedger',
          'cpCatalogItems',
          'cpInstallations',
          'cpInstallationHistory',
          'cpGrants',
          'cpCapabilityLedger',
          'cpBindings',
        ]);
        expect(CONTROL_PLANE.tables).to.include.members([
          'harness.cp_org_heads',
          'harness.cp_deployment_head',
        ]);
      });

      it('copies every collection and the verify finds each document in place, with the listed transforms counted', async () => {
        mongo = new FakeCarryMongo(seed());
        const read = await copyCarried(pg, mongo, only);
        expect(read).to.deep.include({
          'control-plane:cpOrgHeads': 2,
          'control-plane:cpPolicyHistory': 3,
          'control-plane:cpBindings': 1,
        });
        const v = await verifyCarried(pg, mongo, only);
        expect(
          v.filter((x) => !x.ok),
          JSON.stringify(v),
        ).to.deep.equal([]);
        expect(
          v.find((x) => x.collection === 'cpOrgHeads')?.transforms,
        ).to.deep.equal({
          'cpOrgHeads: an idle ordered-steps lease is not carried': 1,
        });
        expect(
          v.find((x) => x.collection === 'cpPolicyLayers')?.transforms,
        ).to.deep.equal({
          'cpPolicyLayers: absent decisionFence read as 0': 1,
        });
        expect(v.find((x) => x.collection === '(check)')).to.deep.include({
          ok: true,
          refused: [],
        });
        await copyCarried(pg, mongo, only);
        expect(
          (await verifyCarried(pg, mongo, only)).every((x) => x.ok),
        ).to.equal(true);
        expect(
          await sql(
            'SELECT id, revision::int AS revision FROM harness.cp_deployment_head',
          ),
        ).to.deep.equal([{ id: 'deployment', revision: 3 }]);
        expect(
          await sql(
            'SELECT decision_fence::int AS f FROM harness.cp_policy_layers WHERE scope = $1',
            [`org:${ORG}`],
          ),
        ).to.deep.equal([{ f: 4 }]);
      });

      it('the PostgreSQL control-plane stores read what was carried, revisions exact (an ETag of the copied state matches)', async () => {
        await inOrg(ORG, async () => {
          expect(await new PgHeadRevisions().heads(ORG)).to.deep.equal({
            org: 7,
            deployment: 3,
          });
          const policy = new PgPolicyStore();
          const org = await policy.read(ORG, `org:${ORG}`);
          expect(org.revision).to.equal(2);
          expect(org.layer).to.deep.include({ schemaVersion: 1 });
          expect(
            (await policy.readMany(ORG, ['agent:a1'])).get('agent:a1'),
          ).to.deep.include({ revision: 1, layer: null });
          expect(
            (await policy.history(ORG, `org:${ORG}`, 10)).map(
              (h) => h.revision,
            ),
          ).to.deep.equal([2, 1]);
          expect(
            await new PgOrgCatalogStore().get(ORG, item.ref),
          ).to.deep.equal(item);
          const installs = new PgInstallationStore();
          expect(await installs.get(ORG, inst.id)).to.deep.equal(inst);
          expect(
            (await installs.history(ORG, inst.id, 10)).map((h) => h.revision),
          ).to.deep.equal([2, 1]);
          expect(await new PgGrantStore().get(ORG, 'agent:a7')).to.deep.equal(
            aGrant(),
          );
          expect(await new PgCapabilityLedger().entries(ORG)).to.deep.equal([
            { object: 'installation', id: inst.id, revision: 1 },
          ]);
          expect(await new PgBindingStore().get(ORG, binding.id)).to.deep.equal(
            binding,
          );
        });
        // the ledger's cross-org read runs as the jobs login (R46CARRY-FU-3)
        expect(
          await system('carry-cp-itest', () =>
            new PgMigrationLedger().entries('capabilities-v1'),
          ),
        ).to.deep.equal([{ orgId: ORG, entry: { orgId: ORG, n: 1 } }]);
        expect(
          await sql(
            'SELECT refresh_holder, refresh_until::bigint::text AS u FROM harness.cp_bindings',
          ),
        ).to.deep.equal([{ refresh_holder: 'replica-1', u: '1700000000500' }]);
      });

      it('a head whose lease recorded an intent (an unfinished write) is refused, and the copy stops', async () => {
        const m = new FakeCarryMongo({
          cpOrgHeads: [
            {
              _id: ORG,
              revision: 8,
              updatedAt: at(8),
              lease: { token: 't', until: 9, intent: { x: 1 } },
            },
          ],
        });
        let err: unknown;
        await copyCarried(pg, m, only).catch((e: unknown) => (err = e));
        expect(String(err)).to.contain('an unfinished control-plane write');
        const v = await verifyCarried(pg, m, only);
        expect(
          v.find((x) => x.collection === 'cpOrgHeads')?.refused[0],
        ).to.contain('an unfinished control-plane write');
      });

      it('the follower applies a policy write and deletes through the key map (a removed layer and binding do not survive)', async () => {
        const layer = {
          ...seed().cpPolicyLayers![0]!,
          revision: 3,
          body: orgLayer(3),
          updatedAt: at(30),
        };
        const hist = {
          _id: `${ORG}:org:${ORG}:3`,
          orgId: ORG,
          scope: `org:${ORG}`,
          revision: 3,
          body: orgLayer(3),
          at: at(30),
        };
        const head = { _id: ORG, revision: 8, updatedAt: at(30), lease: null };
        const gone = seed().cpPolicyLayers![1]!;
        const goneHist = seed().cpPolicyHistory![2]!;
        const goneBinding = seed().cpBindings![0]!;
        const r = await follow(
          scriptedRun([
            event('update', 'cpPolicyLayers', layer),
            event('insert', 'cpPolicyHistory', hist),
            event('update', 'cpOrgHeads', head),
            event('delete', 'cpPolicyLayers', gone),
            event('delete', 'cpPolicyHistory', goneHist),
            event('delete', 'cpBindings', goneBinding),
          ]),
          job(),
          { untilIdleMs: 300, log: () => undefined },
        );
        expect(r.applied).to.equal(6);
        mongo.put('cpPolicyLayers', layer);
        mongo.put('cpPolicyHistory', hist);
        mongo.put('cpOrgHeads', head);
        mongo.del('cpPolicyLayers', gone._id);
        mongo.del('cpPolicyHistory', goneHist._id);
        mongo.del('cpBindings', goneBinding._id);
        const v = await verifyCarried(pg, mongo, only);
        expect(
          v.filter((x) => !x.ok),
          JSON.stringify(v),
        ).to.deep.equal([]);
        expect(
          await sql(
            "SELECT 1 FROM harness.cp_policy_layers WHERE scope = 'agent:a1'",
          ),
        ).to.deep.equal([]);
        expect(await sql('SELECT 1 FROM harness.cp_bindings')).to.deep.equal(
          [],
        );
        expect(
          await sql(
            'SELECT 1 FROM platform.docstore_keymap WHERE source_id = $1',
            [gone._id],
          ),
        ).to.deep.equal([]);
        expect(
          await inOrg(ORG, () => new PgHeadRevisions().heads(ORG)),
        ).to.deep.equal({ org: 8, deployment: 3 });
      });

      it('the version chains are re-checked on PostgreSQL: a history row lost there is found, and a chain broken on MongoDB stays broken the same way', async () => {
        await pg(
          (k) =>
            k.query(
              'DELETE FROM harness.cp_installation_history WHERE revision = 1',
            ),
          true,
        );
        let check = (await verifyCarried(pg, mongo, only)).find(
          (x) => x.collection === '(check)',
        );
        expect(check?.ok).to.equal(false);
        expect(check?.refused[0])
          .to.contain(
            `the control-plane version chains of ${ORG} verify differently`,
          )
          .and.contain('history revision 1 missing');
        // broken on both sides alike: the move neither repairs nor hides it
        mongo.del('cpInstallationHistory', `${ORG}|${inst.id}:1`);
        check = (await verifyCarried(pg, mongo, only)).find(
          (x) => x.collection === '(check)',
        );
        expect(check).to.deep.include({ ok: true, refused: [] });
        mongo.put('cpInstallationHistory', seed().cpInstallationHistory![0]!);
        await copyCarried(pg, mongo, only);
        expect(
          (await verifyCarried(pg, mongo, only)).every((x) => x.ok),
        ).to.equal(true);
      });

      it('a rollback after the application wrote on PostgreSQL gives every document its id back (new rows get the ids the MongoDB stores compose)', async () => {
        await pg(
          (k) =>
            k.query(
              "INSERT INTO harness.cp_grants (org_id, subject, entries, revision, region) VALUES ($1, 'agent:b2', $2::jsonb, 1, 'local')",
              [
                ORG,
                JSON.stringify([
                  { installation: instId(1), tools: null, required: false },
                ]),
              ],
            ),
          true,
        );
        await pg(
          (k) => k.query('UPDATE harness.cp_deployment_head SET revision = 4'),
          true,
        );
        const b2 = aBinding({ id: bindingId(2), owner: 'user:u9' });
        await pg(
          (k) =>
            k.query(
              "INSERT INTO harness.cp_bindings (org_id, id, installation, owner, status, expires_at, revision, doc, region) VALUES ($1, $2, $3, $4, 'active', NULL, 1, $5::jsonb, 'local')",
              [ORG, b2.id, b2.installation, b2.owner, JSON.stringify(b2)],
            ),
          true,
        );
        const before = clone(mongo.docs('cpPolicyHistory'));
        const out = await restoreCarried(pg, mongo, only);
        // the grant MongoDB already holds as PostgreSQL gives it is not written again
        expect(out['control-plane:cpGrants']).to.deep.equal({
          written: 1,
          unchanged: 1,
          removed: 0,
        });
        for (const d of before)
          expect(mongo.get('cpPolicyHistory', d._id)?.revision).to.equal(
            d.revision,
          );
        expect(mongo.get('cpGrants', `${ORG}:agent:b2`)).to.deep.include({
          orgId: ORG,
          subject: 'agent:b2',
          revision: 1,
        });
        expect(mongo.get('cpOrgHeads', '$deployment')).to.deep.include({
          revision: 4,
          lease: null,
        });
        const back = mongo.get('cpBindings', b2.id);
        expect(back).to.not.have.property('id');
        expect(back).to.deep.include({ owner: 'user:u9', revision: 1 });
        const v = await verifyCarried(pg, mongo, only);
        expect(
          v.filter((x) => !x.ok),
          JSON.stringify(v),
        ).to.deep.equal([]);
      });

      it('reset removes every control-plane row and its key map entries', async () => {
        await pg((k) => clearCarried(k, only), true);
        for (const t of CONTROL_PLANE.tables)
          expect(
            (
              await sql<{ n: string }>(`SELECT count(*)::text AS n FROM ${t}`)
            )[0]?.n,
            t,
          ).to.equal('0');
        expect(
          (
            await sql<{ n: string }>(
              "SELECT count(*)::text AS n FROM platform.docstore_keymap WHERE collection LIKE 'cp%'",
            )
          )[0]?.n,
        ).to.equal('0');
      });
    },
  );
}
