/**
 * What every `AuditChainStore` must do (O-2a chaining, O-2c anchoring
 * bookkeeping): `InMemoryAuditChainStore` (`audit-chain-store.test.ts`) and
 * `MongoAuditChainStore` (`audit-chain-store.mongo.itest.ts`).
 */
import { expect } from 'chai';
import {
  AUDIT_GENESIS_HASH,
  AuditEntry,
  AuditRecord,
  verifyAuditChain,
} from '../../../../src/modules/agent-harness/domain/audit';
import { AuditChainStore } from '../../../../src/modules/agent-harness/ports/audit';
import { AuditChainQueries } from '../../../../src/modules/agent-harness/ports/audit-queries';
import { ChainWorkspaceDownloadAudit } from '../../../../src/modules/agent-harness/adapters/chain-workspace-download-audit';
import { workspacePathDigest } from '../../../../src/modules/agent-harness/domain/workspace-download-audit';
import { AUDIT_ORG_1, AUDIT_ORG_2, AUDIT_ORG_3, AUDIT_ORG_9 } from './audit-orgs';

export interface AuditChainBackend<Tx> {
  /** AUDITUI: every chain store also answers the admin pages' reads by kind (`audit-chain-queries-contract.ts`). */
  store: AuditChainStore<Tx> & AuditChainQueries;
  /** Runs `work` in the store's commit transaction (the journal append's), as `orgId` where the store needs to know. */
  transact<T>(work: (tx: Tx) => Promise<T>, orgId: string): Promise<T>;
  /** A failed `transact` leaves nothing (the in-memory store has no transaction to roll back). */
  atomic: boolean;
}

const TS = '2026-10-09T12:00:00.000Z';

export const auditEntry = (
  journalSeq: number,
  overrides: Partial<AuditEntry> = {},
): AuditEntry => ({
  kind: 'run.admitted',
  sessionId: 'ses-1',
  streamId: 'ses-1',
  runId: 'run-1',
  journalSeq,
  writer: 'node',
  attemptId: null,
  fence: null,
  fields: { principalRef: 'p_user-1', runKind: 'interactive' },
  dataDigest: `sha256:${'d'.repeat(64)}`,
  ...overrides,
});

const entryOf = (record: AuditRecord): AuditEntry => {
  const { orgId, seq, prevHash, hash, ts, ...entry } = record;
  [orgId, seq, prevHash, hash, ts];
  return entry;
};

export function describeAuditChainStoreContract<Tx>(
  name: string,
  create: () => Promise<AuditChainBackend<Tx>>,
): void {
  describe(`AuditChainStore contract: ${name}`, () => {
    const append = (
      backend: AuditChainBackend<Tx>,
      orgId: string,
      entries: AuditEntry[],
    ) => backend.transact((tx) => backend.store.append(orgId, entries, TS, tx), orgId);

    it('AC-1: an empty batch appends nothing and starts no chain', async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, []);
      expect(await backend.store.head(AUDIT_ORG_1)).to.equal(null);
      expect(await backend.store.read(AUDIT_ORG_1, 0, 10)).to.deep.equal([]);
    });

    it('AC-2: a first append links from genesis, numbers from 1, and holds what was appended', async () => {
      const backend = await create();
      const entries = [auditEntry(1), auditEntry(2, { kind: 'run.claimed' })];
      await append(backend, AUDIT_ORG_1, entries);
      const records = await backend.store.read(AUDIT_ORG_1, 0, 10);
      expect(records.map((record) => [record.orgId, record.seq, record.ts])).to.deep.equal([
        [AUDIT_ORG_1, 1, TS],
        [AUDIT_ORG_1, 2, TS],
      ]);
      expect(records[0]?.prevHash).to.equal(AUDIT_GENESIS_HASH);
      expect(records[1]?.prevHash).to.equal(records[0]?.hash);
      expect(records.map((record) => entryOf(record))).to.deep.equal(entries);
      expect(await backend.store.head(AUDIT_ORG_1)).to.deep.equal({
        orgId: AUDIT_ORG_1,
        seq: 2,
        hash: records[1]?.hash,
        anchoredSeq: 0,
      });
    });

    it('AC-3: a later append continues from the head; the stored chain verifies', async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, [auditEntry(1)]);
      const first = await backend.store.read(AUDIT_ORG_1, 0, 10);
      await append(backend, AUDIT_ORG_1, [auditEntry(2), auditEntry(3)]);
      const stored = await backend.store.read(AUDIT_ORG_1, 0, 10);
      expect(stored.map((record) => record.seq)).to.deep.equal([1, 2, 3]);
      expect(stored[1]?.prevHash).to.equal(first[0]?.hash);
      expect(verifyAuditChain(stored, { head: await backend.store.head(AUDIT_ORG_1) })).to.deep.include({
        ok: true,
        records: 3,
        lastSeq: 3,
      });
    });

    it('AC-4: read pages by seq: after a seq, at most the limit, in order', async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, [1, 2, 3, 4, 5].map((n) => auditEntry(n)));
      expect((await backend.store.read(AUDIT_ORG_1, 2, 2)).map((record) => record.seq)).to.deep.equal([3, 4]);
      expect((await backend.store.read(AUDIT_ORG_1, 4, 10)).map((record) => record.seq)).to.deep.equal([5]);
      expect(await backend.store.read(AUDIT_ORG_1, 5, 10)).to.deep.equal([]);
    });

    it("AC-5: every org has its own chain from genesis; one org's reads never show another's", async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, [auditEntry(1), auditEntry(2)]);
      await append(backend, AUDIT_ORG_2, [auditEntry(1)]);
      const other = await backend.store.read(AUDIT_ORG_2, 0, 10);
      expect(other).to.have.length(1);
      expect(other[0]).to.include({ seq: 1, prevHash: AUDIT_GENESIS_HASH, orgId: AUDIT_ORG_2 });
      expect((await backend.store.read(AUDIT_ORG_1, 0, 10)).every((record) => record.orgId === AUDIT_ORG_1)).to.equal(true);
      expect((await backend.store.head(AUDIT_ORG_2))?.seq).to.equal(1);
      expect(await backend.store.head(AUDIT_ORG_3)).to.equal(null);
    });

    it('AC-6: unanchored lists grown heads, least recently anchored first, at most the limit', async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, [auditEntry(1)]);
      await append(backend, AUDIT_ORG_2, [auditEntry(1), auditEntry(2)]);
      await append(backend, AUDIT_ORG_3, [auditEntry(1)]);
      await backend.store.markAnchored(AUDIT_ORG_1, 1);
      await append(backend, AUDIT_ORG_1, [auditEntry(2)]);
      const heads = await backend.store.unanchored(10);
      expect(heads.map((head) => [head.orgId, head.anchoredSeq])).to.have.deep.members([
        [AUDIT_ORG_1, 1],
        [AUDIT_ORG_2, 0],
        [AUDIT_ORG_3, 0],
      ]);
      expect(heads.at(-1)?.orgId).to.equal(AUDIT_ORG_1);
      expect(await backend.store.unanchored(2)).to.have.length(2);
    });

    it('AC-7: markAnchored takes a head out of unanchored and never moves backwards', async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, [auditEntry(1), auditEntry(2)]);
      await backend.store.markAnchored(AUDIT_ORG_1, 2);
      expect(await backend.store.unanchored(10)).to.deep.equal([]);
      await backend.store.markAnchored(AUDIT_ORG_1, 1);
      expect((await backend.store.head(AUDIT_ORG_1))?.anchoredSeq).to.equal(2);
      await append(backend, AUDIT_ORG_1, [auditEntry(3)]);
      expect(await backend.store.unanchored(10)).to.deep.equal([await backend.store.head(AUDIT_ORG_1)]);
    });

    it('AC-8: marking an org without a chain anchored starts nothing', async () => {
      const backend = await create();
      await backend.store.markAnchored(AUDIT_ORG_9, 4);
      expect(await backend.store.head(AUDIT_ORG_9)).to.equal(null);
      expect(await backend.store.unanchored(10)).to.deep.equal([]);
    });

    it('AC-9: concurrent appends of one org stay one linear chain, each batch contiguous', async () => {
      const backend = await create();
      await Promise.all(
        Array.from({ length: 6 }, (_, index) =>
          append(backend, AUDIT_ORG_1, [
            auditEntry(1, { runId: `run-${String(index)}` }),
            auditEntry(2, { runId: `run-${String(index)}` }),
          ]),
        ),
      );
      const stored = await backend.store.read(AUDIT_ORG_1, 0, 100);
      expect(verifyAuditChain(stored, { head: await backend.store.head(AUDIT_ORG_1) })).to.deep.include({
        ok: true,
        records: 12,
        lastSeq: 12,
      });
      for (let index = 0; index < 12; index += 2) {
        expect(stored[index]?.runId).to.equal(stored[index + 1]?.runId);
      }
    });

    it('AC-10 (PERFC-22): an append is in the chain for whoever reads once its transaction committed', async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, [auditEntry(1)]);
      expect((await backend.store.head(AUDIT_ORG_1))?.seq).to.equal(1);
      await append(backend, AUDIT_ORG_1, [auditEntry(2)]);
      expect((await backend.store.read(AUDIT_ORG_1, 0, 10)).map((record) => record.journalSeq)).to.deep.equal([1, 2]);
      await append(backend, AUDIT_ORG_2, [auditEntry(1)]);
      await append(backend, AUDIT_ORG_1, [auditEntry(3)]);
      const heads = await backend.store.unanchored(10);
      expect(heads.map((head) => [head.orgId, head.seq])).to.have.deep.members([
        [AUDIT_ORG_1, 3],
        [AUDIT_ORG_2, 1],
      ]);
    });

    it('AC-11 (PERFC-23): an append whose transaction fails is never in the chain', async () => {
      const backend = await create();
      await append(backend, AUDIT_ORG_1, [auditEntry(1)]);
      const failed = await backend
        .transact(async (tx) => {
          await backend.store.append(AUDIT_ORG_1, [auditEntry(2)], TS, tx);
          throw new Error('the journal append failed');
        }, AUDIT_ORG_1)
        .then(
          () => null,
          (error: Error) => error.message,
        );
      expect(failed).to.equal('the journal append failed');
      if (backend.atomic) {
        expect((await backend.store.read(AUDIT_ORG_1, 0, 10)).map((record) => record.journalSeq)).to.deep.equal([1]);
        expect((await backend.store.head(AUDIT_ORG_1))?.seq).to.equal(1);
      }
    });

    it('AC-12 (PERFC-21): the streams of one org interleave; each stays in journal order', async () => {
      const backend = await create();
      const streams = ['ses-a', 'ses-b', 'ses-c'];
      await Promise.all(
        streams.map(async (streamId) => {
          for (let seq = 1; seq <= 4; seq += 1) {
            await append(backend, AUDIT_ORG_1, [auditEntry(seq, { streamId, sessionId: streamId })]);
          }
        }),
      );
      const stored = await backend.store.read(AUDIT_ORG_1, 0, 100);
      expect(verifyAuditChain(stored, { head: await backend.store.head(AUDIT_ORG_1) }).ok).to.equal(true);
      for (const streamId of streams) {
        expect(stored.filter((record) => record.streamId === streamId).map((record) => record.journalSeq)).to.deep.equal([
          1, 2, 3, 4,
        ]);
      }
    });

    it('AC-13 (GAPS2-2): a workspace download is one record of its session on the chain, its path only as a digest', async () => {
      const backend = await create();
      let clock = Date.parse(TS);
      const audit = new ChainWorkspaceDownloadAudit<Tx>(
        backend.store,
        (work) => backend.transact(work, AUDIT_ORG_1),
        () => new Date((clock += 1)),
      );
      await append(backend, AUDIT_ORG_1, [auditEntry(1)]);
      const download = {
        type: 'workspace.downloaded' as const,
        orgId: AUDIT_ORG_1,
        actorId: 'user-1',
        sessionId: 'ses-1',
        revision: 4,
        target: 'file' as const,
        path: 'reports/gaps2-marker-salary.xlsx',
        files: 1,
        bytes: 2048,
      };
      await audit.record(download);
      const { path: _path, ...whole } = download;
      _path;
      await audit.record({ ...whole, target: 'archive', files: 3, bytes: 4096 });
      const stored = await backend.store.read(AUDIT_ORG_1, 0, 10);
      expect(verifyAuditChain(stored, { head: await backend.store.head(AUDIT_ORG_1) }).ok).to.equal(true);
      const downloads = stored.filter((record) => record.kind === 'workspace.downloaded');
      expect(downloads.map((record) => [record.sessionId, record.streamId, record.runId, record.writer])).to.deep.equal([
        ['ses-1', 'wsdl:ses-1', null, 'node'],
        ['ses-1', 'wsdl:ses-1', null, 'node'],
      ]);
      expect(downloads.map((record) => record.fields)).to.deep.equal([
        {
          by: 'p_user-1',
          target: 'file',
          revision: 4,
          files: 1,
          bytes: 2048,
          pathDigest: workspacePathDigest('reports/gaps2-marker-salary.xlsx'),
        },
        { by: 'p_user-1', target: 'archive', revision: 4, files: 3, bytes: 4096 },
      ]);
      expect(JSON.stringify(stored)).not.to.include('gaps2-marker');
    });
  });
}
