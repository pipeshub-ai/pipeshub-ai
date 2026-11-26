/**
 * The carriers in a whole move on a real MongoDB replica set and PostgreSQL (R45-CARRY, R45MIGR-FU-2): preflight lets carried
 * collections and fields through and still refuses a held one, the bulk copy carries them, the follower applies a write and a
 * delete, the verify is clean, and a reverse copy gives MongoDB back the same documents under the same ids. Runs where
 * `MIGRATE_TEST_MONGOD` names a mongod; carry.itest.ts runs the same steps with MongoDB in memory everywhere else.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { ObjectId } from 'mongodb';
import { PG_BINS, haveStack } from '../../stores/pg-store-harness';
import { bulk } from '../../../src/scripts/docstore-migrate/bulk';
import { follow, followStart } from '../../../src/scripts/docstore-migrate/follow';
import { preflight } from '../../../src/scripts/docstore-migrate/preflight';
import { reverseCopy } from '../../../src/scripts/docstore-migrate/reverse';
import { JobState } from '../../../src/scripts/docstore-migrate/state';
import { verifyAll } from '../../../src/scripts/docstore-migrate/verify';
import { clearCarried } from '../../../src/scripts/docstore-migrate/carry/run';
import { finalize } from '../../../src/scripts/docstore-migrate/finalize';
import { reverseFollow, rollbackOnline } from '../../../src/scripts/docstore-migrate/reverse-follow';
import { runCommand } from './cli-run';
import { ADMIN_A, ORG_A, identityDocs } from './fixtures/identity';
import { haveMongod } from './mongo-harness';
import { World, startWorld } from './world';

const suite = haveStack && haveMongod ? describe : describe.skip;
const DB = 'es_carry';
const log = (): void => undefined;

const org = (): string => String(ORG_A);
const harnessDocs = (): Record<string, unknown[]> => ({
  agentHarnessDataKeys: [
    { _id: 'kid-1', orgId: org(), version: 1, kekId: 'kek-1', wrapped: 'd3JhcA==', createdAt: 1_700_000_000_000, seals: 3 },
    { _id: 'kid-2', orgId: org(), version: 2, kekId: 'kek-1', wrapped: 'd3JhcDI=', createdAt: 1_700_000_100_000 },
  ],
  agentWorkspaceHeads: [
    { _id: new ObjectId(), orgId: org(), workspaceId: 'session:a', revision: 2, runId: 'r1', updatedAt: 1 },
    { _id: new ObjectId(), orgId: org(), workspaceId: 'session:b', revision: 1, runId: 'r2', updatedAt: 2 },
  ],
  agentWorkspaceManifests: [
    {
      _id: new ObjectId(),
      orgId: org(),
      workspaceId: 'session:a',
      revision: 1,
      parentRevision: null,
      contentHash: 'h',
      fileCount: 1,
      files: { v: 1, kid: 'kid-1', kv: 1, iv: 'aQ==', tag: 'dA==', ct: 'Yw==' },
      reason: 'run',
      runId: 'r1',
      createdAt: 1,
    },
  ],
  agentWorkspaceKeys: [
    { _id: new ObjectId(), orgId: org(), sessionId: 's-a', keyId: 'k', wrapped: { v: 1, kid: 'kid-1', kv: 1, iv: 'aQ==', tag: 'dA==', ct: 'Yw==' }, createdAt: 1, destroyedAt: null },
  ],
});

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`docstore-migrate: carriers in a whole move (R45-CARRY, ${bin})`, function () {
    this.timeout(300_000);
    let w: World;

    before(async () => {
      w = await startWorld(bin, 'migrate_carry_m', DB);
    });
    after(async () => {
      await w?.stop();
    });
    beforeEach(async () => {
      const seed = identityDocs();
      // a person's coding engine rides on the user document
      seed.users = (seed.users as Array<Record<string, unknown>>).map((u) =>
        String(u._id) === String(ADMIN_A) ? { ...u, codingEngine: 'codex' } : u,
      );
      await w.reseed({ ...seed, ...harnessDocs() });
      await w.truncate();
      await w.rt.jobs((_t, k) => clearCarried(k));
    });

    const sql = async (q: string, p: unknown[] = []): Promise<any[]> => (await w.stack.admin((c) => c.query(q, p))).rows;

    it('preflight lets the carried collections and fields through and still refuses a held collection', async () => {
      let pre = await preflight(w.rt, { allowClients: true });
      expect(pre.red.filter((f) => f.check.startsWith('held') || f.check === 'carry_refused')).to.deep.equal([]);
      expect(pre.info.carried).to.deep.include({ 'data-keys-and-workspaces:agentHarnessDataKeys': 2, 'coding-engine-choices:users': 1 });
      // R46-CARRY: the durable runs are carried now; a collection an edition writes is still held
      await w.mongo.client.db(DB).collection('agentAuditChainChecks').insertOne({ _id: 'check-1', orgId: org() } as never);
      pre = await preflight(w.rt, { allowClients: true });
      expect(pre.red.filter((f) => f.check.startsWith('held')).map((f) => (f.data as { collection: string }).collection)).to.deep.equal(['agentAuditChainChecks']);
      await w.mongo.client.db(DB).collection('agentAuditChainChecks').deleteMany({});
    });

    it('copies, follows a write and a delete, verifies, and a reverse copy gives MongoDB the same documents under the same ids', async () => {
      const js = await JobState.attach(w.rt);
      await followStart(w.rt, js);
      await bulk(w.rt, js, { log });
      let v = await verifyAll(w.rt, { log });
      expect(v.carried.filter((c) => !c.ok), JSON.stringify(v.carried)).to.deep.equal([]);
      expect(v.ok, JSON.stringify(v.global)).to.equal(true);
      expect(await sql('SELECT subject, doc FROM harness.personal_choices')).to.deep.equal([{ subject: `user:${String(ADMIN_A).toLowerCase()}`, doc: { codingEngine: 'codex' } }]);

      const db = w.mongo.client.db(DB);
      const heads = await db.collection('agentWorkspaceHeads').find({}).sort({ workspaceId: 1 }).toArray();
      await db.collection('agentWorkspaceHeads').deleteOne({ _id: heads[1]!._id });
      await db.collection('agentHarnessDataKeys').updateOne({ _id: 'kid-1' } as never, { $inc: { seals: 2 } });
      await db.collection('users').updateOne({ _id: ADMIN_A } as never, { $unset: { codingEngine: 1 } });
      const r = await follow(w.rt, js, { untilIdleMs: 1500, log });
      expect(r.applied).to.be.greaterThanOrEqual(3);
      expect(await sql("SELECT workspace_id FROM harness.workspace_heads ORDER BY 1")).to.deep.equal([{ workspace_id: 'session:a' }]);
      expect(await sql("SELECT seals::int AS s FROM harness.data_keys WHERE kid = 'kid-1'")).to.deep.equal([{ s: 5 }]);
      expect(await sql("SELECT 1 FROM harness.personal_choices")).to.deep.equal([]);
      v = await verifyAll(w.rt, { log });
      expect(v.carried.filter((c) => !c.ok), JSON.stringify(v.carried)).to.deep.equal([]);

      // the reverse copy writes every carried document back over MongoDB's, by the id it had
      const before = await db.collection('agentWorkspaceHeads').find({}).toArray();
      await db.collection('agentWorkspaceHeads').deleteMany({});
      const rev = await reverseCopy(w.rt, log);
      expect(rev.reversed['data-keys-and-workspaces:agentWorkspaceHeads']).to.deep.equal({ written: 1, unchanged: 0, removed: 0 });
      const after = await db.collection('agentWorkspaceHeads').find({}).toArray();
      expect(after.map((d) => String(d._id))).to.deep.equal(before.map((d) => String(d._id)));
      v = await verifyAll(w.rt, { log, afterReverse: true });
      expect(v.carried.filter((c) => !c.ok), JSON.stringify(v.carried)).to.deep.equal([]);
    });

    it('R46-CARRY (FU-5): after the move the reverse follower writes a carried change back in the bridge window, finalize removes the key map, and an online rollback is then refused', async () => {
      await w.stack.admin((c) => c.query("UPDATE platform.store_epoch SET backend = 'mongo', readonly = true"));
      const moved = await runCommand(w, ['migrate', '--offline']);
      expect(moved.code, JSON.stringify(moved.out).slice(0, 600)).to.equal(0);
      const db = w.mongo.client.db(DB);
      // the application on PostgreSQL moves a workspace head
      await sql("UPDATE harness.workspace_heads SET doc = jsonb_set(doc, '{revision}', '9') WHERE workspace_id = 'session:a'");
      const js = await JobState.attach(w.rt);
      const r = await reverseFollow(w.rt, js, { log, untilIdleMs: 1500, sweepMs: 0 });
      expect(r.carriedWritten).to.be.greaterThanOrEqual(1);
      expect(await db.collection('agentWorkspaceHeads').findOne({ workspaceId: 'session:a' })).to.deep.include({ revision: 9 });
      // finalize waits for the follower's job to end
      expect((await finalize(w.rt, { log })).ok).to.equal(false);
      await js.finish('done');
      const f = await finalize(w.rt, { log });
      expect(f.ok, f.message).to.equal(true);
      expect(f.keys).to.be.greaterThan(0);
      expect(await sql('SELECT 1 FROM platform.docstore_keymap')).to.deep.equal([]);
      const rb = await rollbackOnline(w.rt, { log, maxFreezeMs: 60_000 });
      expect(rb).to.deep.include({ ok: false, stage: 'finalised' });
      expect((await finalize(w.rt, { log })).message).to.equal('the move was already finalised');
    });
  });
}
