/**
 * The carriers on a migrated PostgreSQL (R45-CARRY), with MongoDB in memory: the copy, the verify, the harness's own PostgreSQL
 * stores reading what was carried, the forward follower (a scripted change stream through `follow()`) applying writes and deletes
 * through the key map, the rollback giving every document back its id after the application wrote to PostgreSQL, and the reset.
 * The Slack thread links (R45MIGR-FU-3, FU-4) run the generic path the same way. carry-mongod.itest.ts repeats the move with a real
 * mongod where one is set (R45MIGR-FU-2).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { ObjectId, Timestamp } from 'mongodb';
import { system } from '../../../src/libs/context/orgContext';
import { withJobsUow } from '../../../src/libs/db';
import type { Tx as KernelTx } from '../../../src/libs/db';
import { PgTx } from '../../../src/libs/repositories/pg';
import { PgDataKeyStore } from '../../../src/libs/repositories/agent-harness/data-protection/pg';
import { PgWorkspaceKeyStore, PgWorkspaceRepository } from '../../../src/libs/repositories/agent-harness/workspace/pg';
import { PgCodingEnginePreferences } from '../../../src/libs/repositories/agent-harness/coding-engine-preferences/pg';
import { CARRIERS, carriersOfFields } from '../../../src/scripts/docstore-migrate/carry';
import { applyCarried, clearCarried, copyCarried, restoreCarried, verifyCarried, type CarryPg } from '../../../src/scripts/docstore-migrate/carry/run';
import { follow } from '../../../src/scripts/docstore-migrate/follow';
import { chainAuditRecords } from '../../../src/modules/agent-harness/domain/audit';
import { PgAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/pg';
import { reverseSpecial } from '../../../src/scripts/docstore-migrate/reverse-special';
import type { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import type { JobState } from '../../../src/scripts/docstore-migrate/state';
import { PgStack, PG_BINS, haveStack, inOrg, startPgStack } from '../../stores/pg-store-harness';
import { FakeCarryMongo, clone } from './fake-carry-mongo';
import * as fx from './carry-fixtures';

const suite = haveStack ? describe : describe.skip;

const pg: CarryPg = (fn, write) => system('carry-itest', () => withJobsUow({ write, service: 'import' }, fn));
const sql = <T extends Record<string, unknown>>(text: string, args: unknown[] = []): Promise<T[]> =>
  pg(async (k) => (await k.query<T>(text, args)).rows, false);

type Event = Record<string, unknown>;
let token = 0;
const event = (op: 'insert' | 'update' | 'delete', coll: string, doc: Record<string, unknown> | { _id: unknown }): Event => ({
  _id: { t: (token += 1) },
  operationType: op,
  ns: { db: 'pipeshub', coll },
  documentKey: { _id: doc._id },
  ...(op === 'delete' ? {} : { fullDocument: clone(doc) }),
  clusterTime: new Timestamp({ t: Math.floor(Date.now() / 1000), i: token }),
});

/** A migration run whose MongoDB is a scripted change stream (everything else of MongoDB unused) and whose PostgreSQL is the stack. */
function scriptedRun(events: Event[], slackDocs: Map<string, Record<string, unknown>> = new Map()): MigrationRuntime {
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
  const slack = {
    async replaceOne(filter: Record<string, unknown>, doc: Record<string, unknown>) {
      const hit = [...slackDocs.entries()].find(([, d]) => d.threadId === filter.threadId && d.botId === filter.botId && d.email === filter.email);
      slackDocs.set(hit ? hit[0] : new ObjectId().toHexString(), { ...doc, _id: hit ? new ObjectId(hit[0]) : undefined });
    },
    async deleteMany(q: { _id: { $in: ObjectId[] } }) {
      let n = 0;
      for (const id of q._id.$in) if (slackDocs.delete(id.toHexString())) n += 1;
      return { deletedCount: n };
    },
  };
  return {
    cfg: { mongoDb: 'pipeshub', lagAlarmMs: 60_000, batchSize: 100 },
    mongo: {
      watch: () => stream,
      db: (name: string) =>
        name === 'admin'
          ? { command: async () => ({ optimes: { lastCommittedOpTime: { ts: new Timestamp({ t: 2 ** 31 - 1, i: 0 }) } } }) }
          : { collection: () => ({ findOne: async () => null }) },
    },
    db: () => ({ collection: () => slack }),
    jobs: <T>(fn: (tx: PgTx, k: KernelTx) => Promise<T>, opts: { write?: boolean } = {}) =>
      pg((k) => fn(new PgTx(k), k), opts.write !== false),
  } as unknown as MigrationRuntime;
}

const job = (): JobState =>
  ({ progress: { follow: { resumeToken: { t: 0 }, appliedUntil: null, events: 0 } }, checkpoint: async () => undefined }) as unknown as JobState;

const runFollow = (rt: MigrationRuntime): ReturnType<typeof follow> => follow(rt, job(), { untilIdleMs: 300, log: () => undefined });

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: carriers on PostgreSQL (R45-CARRY, ${bin})`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    let mongo: FakeCarryMongo;
    before(async () => {
      process.env.DOC_STORE = 'postgres';
      stack = await startPgStack(bin, 'migrate_carry', { importer: true });
    });
    after(async () => {
      await stack?.stop();
    });

    it('copies the data keys, the workspaces and the coding-engine fields, and the verify finds every one in place', async () => {
      mongo = new FakeCarryMongo(fx.seed());
      const read = await copyCarried(pg, mongo, CARRIERS);
      expect(read).to.deep.include({
        'data-keys-and-workspaces:agentHarnessDataKeys': 3,
        'data-keys-and-workspaces:agentWorkspaceHeads': 2,
        'data-keys-and-workspaces:agentWorkspaceManifests': 2,
        'data-keys-and-workspaces:agentWorkspaceKeys': 2,
        'coding-engine-choices:users': 2,
      });
      const v = await verifyCarried(pg, mongo, CARRIERS);
      expect(v.filter((x) => !x.ok), JSON.stringify(v)).to.deep.equal([]);
      const keys = v.find((x) => x.collection === 'agentHarnessDataKeys');
      expect(keys).to.deep.include({ expected: 3, actual: 3 });
      expect(keys?.transforms).to.deep.equal({ 'agentHarnessDataKeys: absent seals read as 0': 1 });
      // a second copy changes nothing (a killed run is simply run again)
      await copyCarried(pg, mongo, CARRIERS);
      expect((await verifyCarried(pg, mongo, CARRIERS)).every((x) => x.ok)).to.equal(true);
      // a key map entry per workspace document (6) and per audit document (3 records, a head, an append, an anchor)
      expect((await sql<{ n: string }>('SELECT count(*)::text AS n FROM platform.docstore_keymap'))[0]?.n).to.equal('12');
      expect(v.find((x) => x.carrier === 'audit-chain' && x.collection === '(check)')).to.deep.include({ ok: true, refused: [] });
    });

    it("the harness's PostgreSQL stores read what was carried as the MongoDB stores would", async () => {
      const ws = `session:${fx.SESSION.toHexString()}`;
      await inOrg(fx.ORG, async () => {
        const keys = await new PgDataKeyStore().list(fx.ORG);
        expect(keys.map((k) => [k.kid, k.version, k.kekId, k.wrapped, k.createdAt])).to.deep.equal([
          ['kid-A1', 1, 'kek-1', 'd3JhcHBlZC1rZXktMQ==', 1_700_000_000_000],
          ['Kid-B2', 2, 'kek-1', 'd3JhcHBlZC1rZXktMg==', 1_700_000_100_000],
        ]);
        // the seal count goes on from the copied one (none counted: zero)
        expect(await new PgDataKeyStore().reserveSeals(fx.ORG, 'kid-A1', 1)).to.equal(8);
        expect(await new PgDataKeyStore().reserveSeals(fx.ORG, 'Kid-B2', 2)).to.equal(2);
        const repo = new PgWorkspaceRepository();
        expect(await repo.head(fx.ORG, ws)).to.deep.include({ revision: 3, forkOf: null });
        const fork = await repo.head(fx.ORG, `${ws}:fork-1`);
        expect(fork?.forkOf).to.deep.equal({ workspaceId: ws, revision: 1 });
        expect(fork?.lastMerge).to.deep.include({ key: 'm1', status: 'conflict', revision: null });
        const m2 = await repo.manifest(fx.ORG, ws, 2);
        expect(m2).to.deep.include({ contentHash: 'h2', fileCount: 3, parentRevision: 1, reason: 'upload' });
        expect(m2?.files).to.deep.equal({ v: 1, kid: 'kid-A1', kv: 1, iv: 'aXY=', tag: 'dGFn', ct: 'Y3Qy' });
        const keyStore = new PgWorkspaceKeyStore();
        expect(await keyStore.get(fx.ORG, fx.SESSION.toHexString())).to.deep.include({ keyId: 'sk-1', destroyedAt: null });
        expect(await keyStore.get(fx.ORG, fx.SESSION2.toHexString())).to.deep.include({ keyId: '', wrapped: null });
      });
      // the choices are the rows PgCodingEnginePreferences writes: (org, subject) with the fields in doc
      const rows = await sql<{ org_id: string; subject: string; doc: unknown }>('SELECT org_id, subject, doc FROM harness.personal_choices ORDER BY subject');
      expect(rows).to.deep.equal([
        { org_id: fx.ORG, subject: 'org', doc: { codingEngineAutoRoute: false, defaultCodingEngine: 'claude-code' } },
        {
          org_id: fx.ORG,
          subject: `session:${fx.SESSION.toHexString()}`,
          doc: { codingEngine: 'opencode', codingEnginePin: 'opencode@1.2', repository: { baseRef: 'main', toolsetInstanceId: 'ti-1', url: 'https://git.example/r.git' } },
        },
        { org_id: fx.ORG, subject: `user:${fx.USER.toHexString()}`, doc: { codingEngine: 'codex' } },
      ]);
      // the reserved seals are PostgreSQL's now: the next verify sees the difference, by field name only
      const v = await verifyCarried(pg, mongo, CARRIERS);
      const keys = v.find((x) => x.collection === 'agentHarnessDataKeys');
      expect(keys?.differing.map((d) => d.fields)).to.deep.equal([['seals'], ['seals']]);
      expect(JSON.stringify(keys)).not.to.contain('d3JhcHBl');
      mongo.put('agentHarnessDataKeys', { ...fx.dataKeys()[0]!, seals: 8 });
      mongo.put('agentHarnessDataKeys', { ...fx.dataKeys()[1]!, seals: 2 });
    });

    it('the follower applies writes and deletes of carried documents through the key map (a removal during the follow does not survive)', async () => {
      const [head, fork] = fx.workspaceHeads();
      const [m1] = fx.workspaceManifests();
      const [, tomb] = fx.workspaceKeys();
      const [, , other] = fx.dataKeys();
      const moved = { ...head!, revision: 4, runId: 'run-9', updatedAt: 1_700_000_900_000 };
      const born = { _id: new ObjectId(), orgId: fx.ORG2, workspaceId: 'session:x', revision: 1, runId: 'r', updatedAt: 1 };
      const r = await runFollow(
        scriptedRun([
          event('update', 'agentWorkspaceHeads', moved),
          event('insert', 'agentWorkspaceHeads', born),
          event('delete', 'agentWorkspaceHeads', { _id: fork!._id }),
          event('delete', 'agentWorkspaceManifests', { _id: m1!._id }),
          event('delete', 'agentWorkspaceKeys', { _id: tomb!._id }),
          event('delete', 'agentHarnessDataKeys', { _id: other!._id }),
        ]),
      );
      expect(r.applied).to.equal(6);
      for (const [c, d] of [
        ['agentWorkspaceHeads', moved],
        ['agentWorkspaceHeads', born],
      ] as const)
        mongo.put(c, d);
      mongo.del('agentWorkspaceHeads', fork!._id);
      mongo.del('agentWorkspaceManifests', m1!._id);
      mongo.del('agentWorkspaceKeys', tomb!._id);
      mongo.del('agentHarnessDataKeys', other!._id);
      const v = await verifyCarried(pg, mongo, CARRIERS);
      expect(v.filter((x) => !x.ok), JSON.stringify(v)).to.deep.equal([]);
      expect(await sql('SELECT workspace_id FROM harness.workspace_heads WHERE workspace_id LIKE $1', ['%fork-1'])).to.deep.equal([]);
      expect(await sql('SELECT 1 FROM harness.workspace_keys WHERE session_id = $1', [fx.SESSION2.toHexString()])).to.deep.equal([]);
      expect(await sql('SELECT 1 FROM platform.docstore_keymap WHERE source_id = $1', [String(fork!._id)])).to.deep.equal([]);
      expect(await sql('SELECT 1 FROM harness.data_keys WHERE kid = $1', ['kid-C1'])).to.deep.equal([]);
    });

    it('the audit chain is re-checked on PostgreSQL: a record changed in the copy breaks it there and the verify says so; a link made during the follow keeps it whole', async () => {
      await pg((k) => k.query("UPDATE harness.audit_records SET doc = jsonb_set(doc, '{fields,n}', '99') WHERE seq = 2"), true);
      let v = await verifyCarried(pg, mongo, CARRIERS);
      expect(v.find((x) => x.collection === 'agentAuditRecords')?.differing.map((d) => d.fields)).to.deep.equal([['fields']]);
      const check = v.find((x) => x.carrier === 'audit-chain' && x.collection === '(check)');
      expect(check?.ok).to.equal(false);
      expect(check?.refused[0]).to.contain(`the audit chain of ${fx.ORG} verifies differently`).and.contain('tampered');
      await copyCarried(pg, mongo, CARRIERS.filter((c) => c.name === 'audit-chain'));
      expect((await verifyCarried(pg, mongo, CARRIERS)).every((x) => x.ok)).to.equal(true);

      // the linker on MongoDB: the pending append becomes record 4, the head moves, the append is removed, all in one transaction
      const records = mongo.docs('agentAuditRecords').sort((a, b) => Number(a.seq) - Number(b.seq));
      const last = records[2]!;
      const [pending] = mongo.docs('agentAuditPending');
      const [linked] = chainAuditRecords(fx.ORG, { seq: Number(last.seq), hash: String(last.hash) }, pending!.entries as never, String(pending!.ts));
      const rec = { _id: new ObjectId(), ...linked! };
      const [head] = mongo.docs('agentAuditHeads');
      const moved = { ...head!, seq: linked!.seq, hash: linked!.hash };
      await runFollow(
        scriptedRun([
          event('insert', 'agentAuditRecords', rec),
          event('update', 'agentAuditHeads', moved),
          event('delete', 'agentAuditPending', { _id: pending!._id }),
        ]),
      );
      mongo.put('agentAuditRecords', rec);
      mongo.put('agentAuditHeads', moved);
      mongo.del('agentAuditPending', pending!._id);
      v = await verifyCarried(pg, mongo, CARRIERS);
      expect(v.filter((x) => !x.ok), JSON.stringify(v)).to.deep.equal([]);
      expect(await sql('SELECT seq::int AS seq FROM harness.audit_heads')).to.deep.equal([{ seq: 4 }]);
      expect(await sql('SELECT 1 FROM harness.audit_pending')).to.deep.equal([]);
    });

    it('a chain that was already broken on MongoDB stays broken the same way on PostgreSQL: the move neither repairs nor hides it', async () => {
      const r1 = mongo.docs('agentAuditRecords').find((d) => d.seq === 1)!;
      mongo.put('agentAuditRecords', { ...r1, kind: 'rewritten' });
      await copyCarried(pg, mongo, CARRIERS.filter((c) => c.name === 'audit-chain'));
      const check = (await verifyCarried(pg, mongo, CARRIERS)).find((x) => x.collection === '(check)');
      expect(check).to.deep.include({ ok: true, refused: [] });
      mongo.put('agentAuditRecords', r1);
      await copyCarried(pg, mongo, CARRIERS.filter((c) => c.name === 'audit-chain'));
    });

    it("a carried field follows its document: a changed choice is rewritten, a cleared one and a deleted person's are removed", async () => {
      const [u1, u2] = fx.users();
      const changed = { ...u1!, codingEngine: 'claude-code' };
      await applyCarried(pg, 'users', [changed, { ...u2!, codingEngine: 'codex' }], [], carriersOfFields('users'));
      expect(await sql<{ subject: string; doc: unknown }>("SELECT subject, doc FROM harness.personal_choices WHERE subject LIKE 'user:%' ORDER BY subject")).to.deep.equal([
        { subject: `user:${fx.USER.toHexString()}`, doc: { codingEngine: 'claude-code' } },
        { subject: `user:${fx.USER2.toHexString()}`, doc: { codingEngine: 'codex' } },
      ]);
      await applyCarried(pg, 'users', [{ ...u1!, codingEngine: undefined }], [String(fx.USER2)], carriersOfFields('users'));
      expect(await sql("SELECT 1 FROM harness.personal_choices WHERE subject LIKE 'user:%'")).to.deep.equal([]);
      mongo.put('users', { ...u1!, codingEngine: undefined });
      mongo.del('users', fx.USER2);
      mongo.put('users', { _id: fx.USER2, orgId: new ObjectId(fx.ORG), email: 'b@x.test' });
      const v = await verifyCarried(pg, mongo, CARRIERS);
      expect(v.filter((x) => !x.ok), JSON.stringify(v)).to.deep.equal([]);
    });

    it('a rollback after the application wrote to PostgreSQL gives every document its id back and removes what PostgreSQL removed', async () => {
      // the application on PostgreSQL: a new data key, a shredded session key, an erased person's choice, a new workspace
      await inOrg(fx.ORG, async () => {
        expect(await new PgDataKeyStore().insert({ orgId: fx.ORG, kid: 'kid-D3', version: 3, kekId: 'kek-1', wrapped: 'bmV3', createdAt: 1_800_000_000_000 })).to.equal(true);
        await new PgWorkspaceKeyStore().destroy(fx.ORG, fx.SESSION.toHexString(), 1_800_000_000_500);
        await new PgCodingEnginePreferences().eraseSession(fx.ORG, fx.SESSION.toHexString());
        const r4 = mongo.docs('agentAuditRecords').find((d) => d.seq === 4)!;
        await new PgAuditAnchorSink().write({ orgId: fx.ORG, seq: 4, hash: String(r4.hash), anchoredAt: '2026-11-02T00:00:00.000Z' });
      });
      await pg(
        (k) =>
          k.query(
            "INSERT INTO harness.workspace_heads (org_id, workspace_id, doc, region) VALUES ($1, 'session:new', $2::jsonb, 'local')",
            [fx.ORG, JSON.stringify({ orgId: fx.ORG, workspaceId: 'session:new', revision: 1, runId: 'r2', updatedAt: 5 })],
          ),
        true,
      );
      const before = clone(mongo.docs('agentWorkspaceHeads'));
      const out = await restoreCarried(pg, mongo, CARRIERS);
      // the two heads MongoDB already holds as PostgreSQL gives them are not written again (R46-CARRY)
      expect(out['data-keys-and-workspaces:agentWorkspaceHeads']).to.deep.equal({ written: 1, unchanged: 2, removed: 0 });
      // the documents MongoDB had keep their ObjectIds; the one made on PostgreSQL gets a new one, recorded in the key map
      for (const d of before) expect(mongo.get('agentWorkspaceHeads', d._id)?.workspaceId).to.equal(d.workspaceId);
      const fresh = mongo.docs('agentWorkspaceHeads').find((d) => d.workspaceId === 'session:new');
      expect(fresh?._id).to.be.instanceOf(ObjectId);
      expect(await sql('SELECT row_key FROM platform.docstore_keymap WHERE source_id = $1', [String(fresh?._id)])).to.deep.equal([{ row_key: '["session:new"]' }]);
      expect(mongo.get('agentHarnessDataKeys', 'kid-D3')).to.deep.include({ orgId: fx.ORG, version: 3, kekId: 'kek-1', wrapped: 'bmV3', seals: 0 });
      expect(mongo.get('agentWorkspaceKeys', fx.workspaceKeys()[0]!._id)).to.deep.include({ wrapped: null, destroyedAt: 1_800_000_000_000 + 500 });
      const chat = mongo.get('chatSessions', fx.SESSION);
      expect(chat).to.not.have.property('codingEngine');
      expect(chat).to.not.have.property('repository');
      expect(chat?.title).to.equal('t');
      expect(mongo.get('org', new ObjectId(fx.ORG))).to.deep.include({ defaultCodingEngine: 'claude-code', codingEngineAutoRoute: false });
      // an anchor written on PostgreSQL comes back under the id the MongoDB sink gives one; the copied one keeps its own
      expect(mongo.get('agentAuditAnchors', `${fx.ORG}:4`)).to.deep.include({ seq: 4, anchoredAt: '2026-11-02T00:00:00.000Z' });
      expect(mongo.get('agentAuditAnchors', `${fx.ORG}:2`)).to.deep.include({ seq: 2 });
      // and the verify, the other way round, finds MongoDB equal to PostgreSQL
      const v = await verifyCarried(pg, mongo, CARRIERS);
      expect(v.filter((x) => !x.ok), JSON.stringify(v)).to.deep.equal([]);
    });

    it('a document removed in MongoDB with no row left in PostgreSQL is removed by the rollback too', async () => {
      await pg((k) => k.query("DELETE FROM harness.workspace_heads WHERE workspace_id = 'session:new'"), true);
      await restoreCarried(pg, mongo, CARRIERS);
      expect(mongo.docs('agentWorkspaceHeads').map((d) => d.workspaceId)).to.not.include('session:new');
    });

    it('reset removes every carried row and the whole key map', async () => {
      await pg((k) => clearCarried(k), true);
      for (const t of ['harness.data_keys', 'harness.workspace_heads', 'harness.workspace_manifests', 'harness.workspace_keys', 'harness.personal_choices', 'harness.audit_records', 'harness.audit_heads', 'harness.audit_pending', 'harness.audit_anchors', 'platform.docstore_keymap'])
        expect((await sql<{ n: string }>(`SELECT count(*)::text AS n FROM ${t}`))[0]?.n, t).to.equal('0');
    });

    it('R45MIGR-FU-4/FU-3: a Slack thread link removed during the follow is removed from PostgreSQL, and a rollback writes orgId back', async () => {
      const conv = new ObjectId();
      await stack.admin((c) =>
        c.query(
          'INSERT INTO conversation.chat_sessions (org_id, id, initiator, user_id, created_at, updated_at) VALUES ($1, $2, $3, $3, now(), now())',
          [fx.ORG, conv.toHexString(), fx.USER.toHexString()],
        ),
      );
      const link = (thread: string): Record<string, unknown> => ({
        _id: new ObjectId(),
        threadId: thread,
        botId: 'B1',
        email: 'a@x.test',
        conversationId: conv.toHexString(),
        createdAt: new Date('2026-01-01T00:00:00Z'),
        updatedAt: new Date('2026-01-01T00:00:00Z'),
        __v: 0,
      });
      const a = link('T-a');
      const b: Record<string, unknown> = { ...link('T-b'), orgId: fx.ORG };
      await runFollow(scriptedRun([event('insert', 'slack_conversations', a), event('insert', 'slack_conversations', b)]));
      expect((await sql<{ thread_id: string }>('SELECT thread_id FROM integrations.slack_threads ORDER BY 1')).map((r) => r.thread_id)).to.deep.equal(['T-a', 'T-b']);
      expect(await sql('SELECT source_id, row_key FROM platform.docstore_keymap WHERE collection = $1 ORDER BY row_key', ['slack_conversations'])).to.deep.equal([
        { source_id: String(a._id), row_key: '["T-a","B1","a@x.test"]' },
        { source_id: String(b._id), row_key: '["T-b","B1","a@x.test"]' },
      ]);
      // before the key map the event's ObjectId named no row and the link stayed
      await runFollow(scriptedRun([event('delete', 'slack_conversations', { _id: a._id })]));
      expect((await sql<{ thread_id: string }>('SELECT thread_id FROM integrations.slack_threads')).map((r) => r.thread_id)).to.deep.equal(['T-b']);
      expect(await sql('SELECT 1 FROM platform.docstore_keymap WHERE source_id = $1', [String(a._id)])).to.deep.equal([]);
      // the rollback: every row written back with its orgId; a carried link whose row is gone after the move is removed
      const slackDocs = new Map<string, Record<string, unknown>>([
        [String(b._id), { ...b }],
        [String(a._id), { ...a }],
      ]);
      await pg((k) => k.query("DELETE FROM integrations.slack_threads WHERE thread_id = 'T-b'"), true);
      await pg((k) => k.query("INSERT INTO integrations.slack_threads (org_id, thread_id, bot_id, email, conversation_id, created_at, updated_at) VALUES ($1, 'T-c', 'B1', 'a@x.test', $2, now(), now())", [fx.ORG, conv.toHexString()]), true);
      const counts = await reverseSpecial(scriptedRun([], slackDocs), 'slack_conversations');
      expect(counts).to.deep.equal({ written: 1, removed: 1 });
      expect([...slackDocs.values()].map((d) => [d.threadId, d.orgId])).to.deep.equal([
        [a.threadId, undefined],
        ['T-c', fx.ORG],
      ]);
    });
  });
}
