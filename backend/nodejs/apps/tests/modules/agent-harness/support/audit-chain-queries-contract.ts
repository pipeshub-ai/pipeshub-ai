/**
 * What every `AuditChainQueries` must do (AUDITUI's admin pages): reads by
 * kind of the org's own chain, newest first, a cursor that pages without a gap
 * or a repeat, every filter ANDed, `readAt` and `sessionsOf`.
 * `InMemoryAuditChainStore` (`audit-chain-store.test.ts`) and
 * `MongoAuditChainStore` (`audit-chain-store.mongo.itest.ts`).
 */
import { expect } from 'chai';
import { ChainWorkspaceDownloadAudit } from '../../../../src/modules/agent-harness/adapters/chain-workspace-download-audit';
import { workspacePathDigest } from '../../../../src/modules/agent-harness/domain/workspace-download-audit';
import { AuditChainBackend, auditEntry } from './audit-chain-store-contract';
import { AUDIT_ORG_1, AUDIT_ORG_2, AUDIT_ORG_3 } from './audit-orgs';

const TS = '2026-10-09T12:00:00.000Z';
const DOWNLOADED = 'workspace.downloaded';

export function describeAuditChainQueriesContract<Tx>(
  name: string,
  create: () => Promise<AuditChainBackend<Tx>>,
): void {
  describe(`AuditChainQueries contract: ${name}`, () => {
    const append = (backend: AuditChainBackend<Tx>, orgId: string, entries: ReturnType<typeof auditEntry>[]) =>
      backend.transact((tx) => backend.store.append(orgId, entries, TS, tx), orgId);

    /** Records of org-1: seq 1-3 downloads, 4 a run entry; org-2: one download. */
    const seeded = async (backend: AuditChainBackend<Tx>) => {
      let clock = Date.parse(TS);
      const audit = (orgId: string) =>
        new ChainWorkspaceDownloadAudit<Tx>(
          backend.store,
          (work) => backend.transact(work, orgId),
          () => new Date((clock += 60_000)),
        );
      const download = (orgId: string, sessionId: string, actorId: string, path?: string) =>
        audit(orgId).record({
          type: DOWNLOADED,
          orgId,
          actorId,
          sessionId,
          revision: 1,
          target: path === undefined ? 'archive' : 'file',
          ...(path !== undefined ? { path } : {}),
          files: 1,
          bytes: 1,
        });
      await download(AUDIT_ORG_1, 'ses-a', 'user-1', 'a.txt');
      await download(AUDIT_ORG_1, 'ses-b', 'user-2', 'b.txt');
      await download(AUDIT_ORG_1, 'ses-a', 'user-1');
      await download(AUDIT_ORG_2, 'ses-a', 'user-1', 'a.txt');
      // A read links what is pending, so the run entry (an earlier ts) is linked after the downloads.
      await backend.store.read(AUDIT_ORG_1, 0, 1);
      await append(backend, AUDIT_ORG_1, [auditEntry(1)]);
    };

    it('AQ-1: reads by kind are the org’s own, newest first, only of the kinds asked', async () => {
      const backend = await create();
      await seeded(backend);
      const all = await backend.store.readKind(AUDIT_ORG_1, { kinds: [DOWNLOADED], beforeSeq: null, limit: 10 });
      expect(all.map((r) => r.seq)).to.deep.equal([3, 2, 1]);
      expect(all.every((r) => r.orgId === AUDIT_ORG_1 && r.kind === DOWNLOADED)).to.equal(true);
      const both = await backend.store.readKind(AUDIT_ORG_1, { kinds: [DOWNLOADED, 'run.admitted'], beforeSeq: null, limit: 10 });
      expect(both.map((r) => [r.seq, r.kind])).to.deep.equal([
        [4, 'run.admitted'],
        [3, DOWNLOADED],
        [2, DOWNLOADED],
        [1, DOWNLOADED],
      ]);
      expect(await backend.store.readKind(AUDIT_ORG_1, { kinds: ['nothing.of.this.kind'], beforeSeq: null, limit: 10 })).to.deep.equal([]);
    });

    it('AQ-2: the cursor pages a long chain newest first with no gap and no repeat, to an empty page', async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, Array.from({ length: 11 }, (_, n) => auditEntry(n + 1)));
      const kinds = ['run.admitted'];
      const seen: number[] = [];
      let cursor: number | null = null;
      for (let page = 0; page < 10; page += 1) {
        const records = await backend.store.readKind(AUDIT_ORG_1, { kinds, beforeSeq: cursor, limit: 4 });
        if (records.length === 0) break;
        expect(records.length).to.be.at.most(4);
        seen.push(...records.map((r) => r.seq));
        cursor = records.at(-1)!.seq;
      }
      expect(seen).to.deep.equal([11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]);
      expect(await backend.store.readKind(AUDIT_ORG_1, { kinds, beforeSeq: 1, limit: 4 })).to.deep.equal([]);
    });

    it('AQ-3: every filter is ANDed: sessions, entry fields (any of a list), and an inclusive time window', async () => {
      const backend = await create();
      await seeded(backend);
      const kinds = [DOWNLOADED];
      const seqs = async (query: Partial<Parameters<typeof backend.store.readKind>[1]>) =>
        (await backend.store.readKind(AUDIT_ORG_1, { kinds, beforeSeq: null, limit: 10, ...query })).map((r) => r.seq);
      expect(await seqs({ beforeSeq: 3, limit: 1 })).to.deep.equal([2]);
      expect(await seqs({ sessionIds: ['ses-a'] })).to.deep.equal([3, 1]);
      expect(await seqs({ fields: { by: ['p_user-2'] } })).to.deep.equal([2]);
      expect(await seqs({ fields: { pathDigest: [workspacePathDigest('a.txt'), workspacePathDigest('zzz')] } })).to.deep.equal([1]);
      expect(await seqs({ sessionIds: ['ses-a'], fields: { by: ['p_user-2'] } })).to.deep.equal([]);
      const [first, second, third] = await backend.store.readAt(AUDIT_ORG_1, [1, 2, 3]);
      expect(await seqs({ since: second!.ts, until: third!.ts })).to.deep.equal([3, 2]);
      expect(await seqs({ since: first!.ts, until: first!.ts })).to.deep.equal([1]);
    });

    it('AQ-4: readAt answers the stored records at the seqs that exist, in seq order; sessionsOf the distinct sessions, most recent first', async () => {
      const backend = await create();
      await seeded(backend);
      expect((await backend.store.readAt(AUDIT_ORG_1, [4, 2, 9, 2])).map((r) => [r.seq, r.kind])).to.deep.equal([
        [2, DOWNLOADED],
        [4, 'run.admitted'],
      ]);
      expect(await backend.store.readAt(AUDIT_ORG_1, [])).to.deep.equal([]);
      const kinds = [DOWNLOADED];
      expect(await backend.store.sessionsOf(AUDIT_ORG_1, { kinds, limit: 10 })).to.deep.equal(['ses-a', 'ses-b']);
      const [, second] = await backend.store.readAt(AUDIT_ORG_1, [1, 2]);
      expect(await backend.store.sessionsOf(AUDIT_ORG_1, { kinds, limit: 1, until: second!.ts })).to.deep.equal(['ses-b']);
    });

    it("AQ-5: no read crosses orgs: another org's records are never on a page, at a seq, or among the sessions", async () => {
      const backend = await create();
      await seeded(backend);
      const kinds = [DOWNLOADED];
      const other = await backend.store.readKind(AUDIT_ORG_2, { kinds, beforeSeq: null, limit: 10 });
      expect(other.map((r) => [r.orgId, r.seq])).to.deep.equal([[AUDIT_ORG_2, 1]]);
      expect((await backend.store.readAt(AUDIT_ORG_2, [1, 2, 3, 4])).map((r) => r.orgId)).to.deep.equal([AUDIT_ORG_2]);
      expect(await backend.store.sessionsOf(AUDIT_ORG_2, { kinds, limit: 10 })).to.deep.equal(['ses-a']);
      expect(await backend.store.readKind(AUDIT_ORG_3, { kinds, beforeSeq: null, limit: 10 })).to.deep.equal([]);
      expect(await backend.store.readAt(AUDIT_ORG_3, [1])).to.deep.equal([]);
      expect(await backend.store.sessionsOf(AUDIT_ORG_3, { kinds, limit: 10 })).to.deep.equal([]);
    });

    it('AQ-6 (AUS-15): runIds keep only those runs’ records; anyOf is met by a run or a field naming the person; both AND the rest', async () => {
      const backend = await create();
      const kinds = ['tool.intent', 'interaction.resolved', 'config.changed'];
      await append(backend, AUDIT_ORG_1, [
        auditEntry(1, { kind: 'tool.intent', runId: 'run-a', fields: { toolId: 't' } }),
        auditEntry(2, { kind: 'interaction.resolved', runId: 'run-b', fields: { by: 'p_admin' } }),
        auditEntry(3, { kind: 'config.changed', runId: null, sessionId: null, fields: { actor: 'user:admin' } }),
        auditEntry(4, { kind: 'tool.intent', runId: 'run-c', fields: { toolId: 't' } }),
      ]);
      await append(backend, AUDIT_ORG_2, [auditEntry(1, { kind: 'tool.intent', runId: 'run-a', fields: { by: 'p_admin' } })]);
      const seqs = async (query: Partial<Parameters<typeof backend.store.readKind>[1]>, orgId = AUDIT_ORG_1) =>
        (await backend.store.readKind(orgId, { kinds, beforeSeq: null, limit: 10, ...query })).map((r) => r.seq);
      expect(await seqs({ runIds: ['run-a', 'run-c'] })).to.deep.equal([4, 1]);
      expect(await seqs({ runIds: [] })).to.deep.equal([]);
      expect(await seqs({ anyOf: { runIds: ['run-a'], fields: { by: ['p_admin'], actor: ['user:admin'] } } })).to.deep.equal([3, 2, 1]);
      expect(await seqs({ anyOf: { runIds: [], fields: { by: ['p_nobody'] } } })).to.deep.equal([]);
      expect(await seqs({ anyOf: {} })).to.deep.equal([]);
      expect(await seqs({ runIds: ['run-b', 'run-c'], anyOf: { runIds: ['run-c'], fields: { by: ['p_admin'] } } })).to.deep.equal([4, 2]);
      expect(await seqs({ kinds: ['tool.intent'], anyOf: { fields: { by: ['p_admin'] } } }), 'kinds still AND').to.deep.equal([]);
      expect(await seqs({ runIds: ['run-a'], anyOf: { fields: { by: ['p_admin'] } } }, AUDIT_ORG_2)).to.deep.equal([1]);
    });
  });
}
