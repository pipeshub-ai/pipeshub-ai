/**
 * A session's harness rows erased on PostgreSQL (E7: ER-7 exact workspace prefixes, ER-8 another session's payloads
 * kept, the journal reduced to tombstones that keep the envelope), and T5's reference-aware collection (GC-1, GC-3) across
 * orgs as the jobs role. Each step is idempotent: run again, it finds nothing more, and another org is never touched.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { createHash } from 'node:crypto';
import { PgPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/pg';
import { PgRetentionStore } from '../../../src/libs/repositories/agent-harness/retention-store/pg';
import { PgSessionRunLookup } from '../../../src/libs/repositories/agent-harness/session-erasure/pg';
import { PgSessionStoreEraser } from '../../../src/libs/repositories/agent-harness/session-store-eraser/pg';
import type { EnvelopeCipher } from '../../../src/modules/agent-harness/application/envelope-cipher';
import type { RunKeyDigest } from '../../../src/modules/agent-harness/domain/run-key-digest';
import { PgStack, boundStore, hexOrg, inOrg, pgBins, pgSuite, resetHarnessPg, startHarnessPg } from './support/harness-pg';

/** Nothing here can be opened: the eraser must still find what the envelopes point at. */
const sealedShut = {
  open: () => Promise.reject(new Error('shredded')),
  openBlob: () => Promise.reject(new Error('shredded')),
} as unknown as EnvelopeCipher;
const digests: RunKeyDigest = {
  digest: (orgId: string, key: string) => `hk1:${createHash('sha256').update(`${orgId}:${key}`).digest('hex')}`,
} as RunKeyDigest;

for (const bin of pgBins()) {
  pgSuite(`a session's harness rows erased on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_session_eraser');
    });
    after(async () => {
      await stack?.stop();
    });
    beforeEach(() => resetHarnessPg(stack));

    const sql = async <R = Record<string, unknown>>(text: string, values: unknown[] = []): Promise<R[]> =>
      (await stack.admin((c) => c.query(text, values))).rows as R[];
    // The erasure job binds the org of its job row; the suite binds the org each call names (ADR-SEC-02).
    const eraser = () => boundStore(new PgSessionStoreEraser(sealedShut, digests));

    const workspaces = async (orgId: string, ids: string[]): Promise<void> => {
      for (const id of ids) {
        await sql(`INSERT INTO harness.workspace_heads (org_id, workspace_id, doc, region) VALUES ($1, $2, '{"revision": 1}', 'r')`, [orgId, id]);
        await sql(`INSERT INTO harness.workspace_manifests (org_id, workspace_id, revision, doc, region) VALUES ($1, $2, 1, '{}', 'r')`, [orgId, id]);
        await sql(
          `INSERT INTO harness.workspace_writers (org_id, workspace_id, session_id, run_id, expires_at, updated_at, region) VALUES ($1, $2, 's', 'r', 1, 1, 'r')`,
          [orgId, id],
        );
      }
    };

    // DOS-MONGO-FU-1: SLASH2's `/usage` sums these runs; on PostgreSQL they are read from `harness.runs`' generated columns.
    it("SLASH2 /usage: a session's runs for the person are their own root runs of harness v2, in run order", async () => {
      const [org, other] = [hexOrg(), hexOrg()];
      const doc = (orgId: string, runId: string, fields: Record<string, unknown>) =>
        sql(`INSERT INTO harness.runs (org_id, run_id, doc, region) VALUES ($1, $2, $3, 'r')`, [
          orgId,
          runId,
          JSON.stringify({ orgId, runId, sessionId: 'S', status: 'completed', actorId: 'ana', harnessMajor: 2, ...fields }),
        ]);
      await doc(org, 'r-b', {});
      await doc(org, 'r-a', {});
      await doc(org, 'r-child', { parent: { runId: 'r-a' } });
      await doc(org, 'r-v1', { harnessMajor: 1 });
      await doc(org, 'r-ben', { actorId: 'ben' });
      await doc(org, 'r-elsewhere', { sessionId: 'Q' });
      await doc(other, 'r-theirs', {});
      const lookup = boundStore(new PgSessionRunLookup());
      expect(await lookup.ownRootRunsOf(org, 'S', 'ana')).to.deep.equal(['r-a', 'r-b']);
      expect(await lookup.ownRootRunsOf(org, 'S', 'ben')).to.deep.equal(['r-ben']);
      expect(await lookup.ownRootRunsOf(other, 'Q', 'ana')).to.deep.equal([]);
      expect((await lookup.runsOf(org, 'S')).map((run) => run.runId)).to.deep.equal(['r-a', 'r-b', 'r-ben', 'r-child', 'r-v1']);
    });

    it('ER-7: ws:S and every ws:S:* go with their snapshots; ws:S0 and another org stay; a second run removes nothing', async () => {
      const [org, other] = [hexOrg(), hexOrg()];
      await workspaces(org, ['ws:S', 'ws:S:fork-1', 'ws:S0']);
      await workspaces(other, ['ws:S']);
      for (const o of [org, other])
        await sql(
          `INSERT INTO harness.sandbox_snapshots (org_id, object_id, session_id, taken_at, snapshot_region, region) VALUES ($1, 'obj', 'S', now(), 'r', 'r')`,
          [o],
        );
      expect(await eraser().eraseWorkspaces(org, 'S')).to.deep.equal({ manifests: 2, heads: 2, writers: 2 });
      expect((await sql<{ workspace_id: string }>(`SELECT workspace_id FROM harness.workspace_heads WHERE org_id = $1`, [org])).map((r) => r.workspace_id)).to.deep.equal(['ws:S0']);
      expect(await sql(`SELECT 1 FROM harness.sandbox_snapshots WHERE org_id = $1`, [org])).to.deep.equal([]);
      expect(await sql(`SELECT 1 FROM harness.workspace_heads WHERE org_id = $1`, [other])).to.have.length(1);
      expect(await sql(`SELECT 1 FROM harness.sandbox_snapshots WHERE org_id = $1`, [other])).to.have.length(1);
      expect(await eraser().eraseWorkspaces(org, 'S')).to.deep.equal({ manifests: 0, heads: 0, writers: 0 });
    });

    it("ER-8: the session's own payloads and the unowned ones its events point at go; another session's never do", async () => {
      const org = hexOrg();
      const payloads = new PgPayloadStore();
      const owned = await inOrg(org, () => payloads.put(org, 'h-owned', Buffer.from('a'), { sessionId: 'S', runId: 'run-1' }));
      const theirs = await inOrg(org, () => payloads.put(org, 'h-theirs', Buffer.from('b'), { sessionId: 'T', runId: 'run-9' }));
      const unowned = await inOrg(org, () => payloads.put(org, 'h-unowned', Buffer.from('c')));
      const untouched = await inOrg(org, () => payloads.put(org, 'h-untouched', Buffer.from('d')));
      const event = (seq: number, ref: string) =>
        sql(`INSERT INTO harness.session_events (org_id, stream_id, seq, session_id, run_id, ts, doc, region) VALUES ($1, 'S', $2, 'S', 'run-1', now(), $3, 'r')`, [
          org,
          seq,
          JSON.stringify({ orgId: org, sessionId: 'S', streamId: 'S', seq, type: 'turn.started', payloadRef: { ref, hash: 'x', size: 1 } }),
        ]);
      await event(1, unowned);
      await event(2, theirs);
      expect(await eraser().erasePayloads(org, 'S', ['run-1'])).to.deep.equal({ payloads: 2 });
      const left = (await sql<{ id: string }>(`SELECT id FROM harness.journal_payloads WHERE org_id = $1 ORDER BY id`, [org])).map((r) => `pg:${r.id}`);
      expect(left).to.deep.equal([theirs, untouched].sort());
      expect(left).to.not.include(owned);
      expect(await eraser().erasePayloads(org, 'S', ['run-1'])).to.deep.equal({ payloads: 0 });
    });

    it('ER-8b (R46-CARRY): an unowned payload the move carried, named by its MongoDB name in a carried event, is erased on PostgreSQL too', async () => {
      const org = hexOrg();
      const carried = await inOrg(org, () => new PgPayloadStore().put(org, 'h-carried', Buffer.from('e')));
      await sql(`INSERT INTO harness.session_events (org_id, stream_id, seq, session_id, run_id, ts, doc, region) VALUES ($1, 'S', 1, 'S', 'run-1', now(), $2, 'r')`, [
        org,
        JSON.stringify({ orgId: org, sessionId: 'S', streamId: 'S', seq: 1, type: 'turn.started', payloadRef: { ref: carried.replace(/^pg:/, 'mongo:'), hash: 'x', size: 1 } }),
      ]);
      expect(await eraser().erasePayloads(org, 'S', ['run-1'])).to.deep.equal({ payloads: 1 });
      expect(await sql(`SELECT id FROM harness.journal_payloads WHERE org_id = $1`, [org])).to.deep.equal([]);
    });

    it("R30-LAT-FU-2: a session of many runs stored with callers' keys keeps only their digests, in batches; another session's keys stay", async () => {
      const org = hexOrg();
      const runs = 1_200;
      await sql(
        `INSERT INTO harness.runs (org_id, run_id, doc, region)
           SELECT $1, 'run-' || i, jsonb_build_object('orgId', $1::text, 'runId', 'run-' || i, 'sessionId', 'S', 'status', 'completed',
             'idempotencyKey', 'caller-key-' || i), 'r' FROM generate_series(1, $2::int) AS i`,
        [org, runs],
      );
      await sql(`INSERT INTO harness.runs (org_id, run_id, doc, region) VALUES ($1, 'run-q', $2, 'r')`, [
        org,
        JSON.stringify({ orgId: org, runId: 'run-q', sessionId: 'Q', status: 'completed', idempotencyKey: 'other-key' }),
      ]);
      expect((await eraser().tombstoneJournal(org, 'S', [])).runKeys).to.equal(runs);
      const keys = await sql<{ run_id: string; key: string }>(`SELECT run_id, doc->>'idempotencyKey' AS key FROM harness.runs WHERE org_id = $1`, [org]);
      for (const { run_id: runId, key } of keys) {
        expect(key).to.equal(runId === 'run-q' ? 'other-key' : digests.digest(org, `caller-key-${runId.slice(4)}`));
      }
      expect((await eraser().tombstoneJournal(org, 'S', [])).runKeys).to.equal(0);
    });

    it("the journal becomes tombstones that keep the envelope; the session's receipts, forks, run inputs and caller keys go", async () => {
      const [org, other] = [hexOrg(), hexOrg()];
      for (const o of [org, other]) {
        await sql(`INSERT INTO harness.session_events (org_id, stream_id, seq, session_id, run_id, ts, doc, region) VALUES ($1, 'S', 1, 'S', 'run-1', now(), $2, 'r')`, [
          o,
          JSON.stringify({ orgId: o, sessionId: 'S', streamId: 'S', seq: 1, type: 'turn.started', labels: ['x'], data: { a: 1 }, sealed: { v: 1, kid: 'k', kv: 1, iv: 'i', tag: 't', ct: 'abcdef' } }),
        ]);
        await sql(`INSERT INTO harness.run_events (org_id, session_id, event_seq, run_id, commit_seq, doc, region) VALUES ($1, 'S', 1, 'run-1', 1, '{}', 'r')`, [o]);
        await sql(`INSERT INTO harness.command_receipts (org_id, run_id, command_id, payload_hash, receipt, created_at, region) VALUES ($1, 'run-1', 'c', 'h', '{}', 1, 'r')`, [o]);
        await sql(`INSERT INTO harness.admission_receipts (org_id, scope_key, payload_hash, result, created_at, region) VALUES ($1, 'k', 'h', '{"sessionId": "S"}', 1, 'r')`, [o]);
        await sql(
          `INSERT INTO harness.session_forks (org_id, id, source_session_id, actor_id, fork_session_id, created_at, region) VALUES ($1, 'f1', 'S', 'a', 'F', 1, 'r'), ($1, 'f2', 'Q', 'a', 'S', 1, 'r'), ($1, 'f3', 'Q', 'a', 'R', 1, 'r')`,
          [o],
        );
        await sql(`INSERT INTO harness.runs (org_id, run_id, doc, region) VALUES ($1, 'run-1', $2, 'r'), ($1, 'run-2', $3, 'r')`, [
          o,
          JSON.stringify({ orgId: o, runId: 'run-1', sessionId: 'S', status: 'completed', input: { text: 'secret' }, label: { records: ['r'] }, followups: ['f'], idempotencyKey: 'caller-key' }),
          JSON.stringify({ orgId: o, runId: 'run-2', sessionId: 'S', status: 'completed', input: null, agentDefinition: null, followups: [], idempotencyKey: digests.digest(o, 'k2') }),
        ]);
      }
      expect(await eraser().tombstoneJournal(org, 'S', ['run-1'])).to.deep.equal({
        events: 1,
        v1Events: 1,
        receipts: 1,
        admissionReceipts: 1,
        forkRecords: 2,
        runInputs: 1,
        runKeys: 1,
      });
      const [tomb] = await sql<{ doc: Record<string, unknown> }>(`SELECT doc FROM harness.session_events WHERE org_id = $1`, [org]);
      expect(tomb!.doc).to.deep.include({ erased: true, contentBytes: 6, type: 'turn.started', seq: 1 });
      expect(tomb!.doc).to.not.have.any.keys('sealed', 'data', 'labels');
      const runs = await sql<{ run_id: string; doc: Record<string, unknown> }>(`SELECT run_id, doc FROM harness.runs WHERE org_id = $1 ORDER BY run_id`, [org]);
      expect(runs[0]!.doc).to.deep.include({ input: null, agentDefinition: null, followups: [], idempotencyKey: digests.digest(org, 'caller-key') });
      expect(runs[0]!.doc).to.not.have.property('label');
      expect((await sql(`SELECT id FROM harness.session_forks WHERE org_id = $1`, [org])).length).to.equal(1);
      expect(await eraser().tombstoneJournal(org, 'S', ['run-1'])).to.deep.equal({
        events: 0,
        v1Events: 0,
        receipts: 0,
        admissionReceipts: 0,
        forkRecords: 0,
        runInputs: 0,
        runKeys: 0,
      });
      const [kept] = await sql<{ doc: Record<string, unknown> }>(`SELECT doc FROM harness.session_events WHERE org_id = $1`, [other]);
      expect(kept!.doc).to.have.property('sealed');
      expect(await sql(`SELECT 1 FROM harness.command_receipts WHERE org_id = $1`, [other])).to.have.length(1);
    });

    describe('T5 retention across orgs', () => {
      const old = 1_000_000;
      const payload = async (orgId: string, id: string, referenced: boolean | null, ownerRun: string | null) =>
        sql(
          `INSERT INTO harness.journal_payloads (org_id, id, hash, size, bytes, created_at, owner_session_id, owner_run_id, referenced, region)
            VALUES ($1, $2, 'h', 1, '\\x00', to_timestamp($3::double precision / 1000), $4, $5, $6, 'r')`,
          [orgId, id, old, ownerRun === null ? null : 'S', ownerRun, referenced],
        );
      const run = (orgId: string, runId: string, status: string) =>
        sql(`INSERT INTO harness.runs (org_id, run_id, doc, region) VALUES ($1, $2, $3, 'r')`, [orgId, runId, JSON.stringify({ orgId, runId, sessionId: 'S', status })]);

      it('GC-1 (I8): only unreferenced payloads past the cut-off whose writer ended or never existed are collected', async () => {
        const [a, b] = [hexOrg(), hexOrg()];
        await run(a, 'live', 'running');
        await run(a, 'done', 'completed');
        await payload(a, 'p-done', false, 'done');
        await payload(a, 'p-live', false, 'live');
        await payload(a, 'p-gone', false, 'never-admitted');
        await payload(a, 'p-ref', true, 'done');
        await payload(b, 'p-legacy', null, null);
        await payload(b, 'p-other', false, null);
        const store = new PgRetentionStore();
        expect(await store.collectPayloads(old + 1, 100)).to.equal(3);
        expect((await sql<{ id: string }>(`SELECT id FROM harness.journal_payloads ORDER BY id`)).map((r) => r.id)).to.deep.equal(['p-legacy', 'p-live', 'p-ref']);
        expect(await store.collectPayloads(old, 100)).to.equal(0);
      });

      it("GC-3: receipts expire after the cut-off; a live run keeps its command receipts", async () => {
        const org = hexOrg();
        await run(org, 'live', 'running');
        for (const runId of ['live', 'done'])
          await sql(`INSERT INTO harness.command_receipts (org_id, run_id, command_id, payload_hash, receipt, created_at, region) VALUES ($1, $2, 'c', 'h', '{}', 10, 'r')`, [org, runId]);
        await sql(`INSERT INTO harness.admission_receipts (org_id, scope_key, payload_hash, result, created_at, region) VALUES ($1, 'old', 'h', '{}', 10, 'r'), ($1, 'new', 'h', '{}', 99, 'r')`, [org]);
        const store = new PgRetentionStore();
        expect(await store.expireCommandReceipts(11, 100)).to.equal(1);
        expect(await store.expireAdmissionReceipts(11, 100)).to.equal(1);
        expect((await sql<{ run_id: string }>(`SELECT run_id FROM harness.command_receipts`)).map((r) => r.run_id)).to.deep.equal(['live']);
        expect((await sql<{ scope_key: string }>(`SELECT scope_key FROM harness.admission_receipts`)).map((r) => r.scope_key)).to.deep.equal(['new']);
      });

      it('the orgs that ever used the harness are the ones holding a data key; now() is the database clock', async () => {
        const [a, b] = [hexOrg(), hexOrg()].sort();
        for (const org of [b, a, a])
          await sql(
            `INSERT INTO harness.data_keys (org_id, kid, version, kek_id, wrapped, created_at_ms, region) VALUES ($1, gen_random_uuid()::text, (SELECT count(*) + 1 FROM harness.data_keys WHERE org_id = $1), 'kek', 'w', 1, 'r')`,
            [org],
          );
        const store = new PgRetentionStore();
        expect(await store.harnessOrgs()).to.deep.equal([a, b]);
        expect(Math.abs((await store.now()) - Date.now())).to.be.below(60_000);
      });
    });
  });
}
