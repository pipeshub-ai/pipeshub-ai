/**
 * The audit chain on PostgreSQL (`harness.audit_records`, `audit_heads`, `audit_pending`, `audit_anchors`; row-level
 * security, PgBouncer in transaction mode): the chain store, queries, anchor sink and lookup contracts, and the
 * invariants the PostgreSQL design owns: concurrent appenders and linkers of one org leave one linear chain with every
 * committed entry once, an aborted unit leaves nothing, an append whose process died is linked by the next read, a
 * malformed append stops only its own org, and another org sees nothing.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  AuditEntry,
  AuditRecord,
  verifyAuditChain,
} from '../../../src/modules/agent-harness/domain/audit';
import {
  PgAuditChainStore,
  pgAuditLinksIdle,
} from '../../../src/libs/repositories/agent-harness/audit/pg';
import { linkAllPendingAuditPg } from '../../../src/libs/repositories/agent-harness/audit-linking/pg';
import { auditLinkDefer } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { PgAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/pg';
import { activeUnit } from '../../../src/libs/db';
import { forEnumeratedOrg, orgContextStorage } from '../../../src/libs/context/orgContext';
import { UnboundOrgError } from '../../../src/libs/repositories/agent-harness/durable-transaction/bound-org';
import type { Tx } from '../../../src/libs/repositories/uow';
import {
  auditEntry,
  describeAuditChainStoreContract,
} from './support/audit-chain-store-contract';
import { describeAuditChainQueriesContract } from './support/audit-chain-queries-contract';
import { describeAuditAnchorSinkContract } from './support/audit-anchor-sink-contract';
import { describeAuditAnchorLookupContract } from './support/audit-anchor-lookup-contract';
import { AUDIT_ORG_1, AUDIT_ORG_2 } from './support/audit-orgs';
import {
  PgStack,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

const CHAIN_ARGS = {
  read: 0,
  readKind: 0,
  readAt: 0,
  sessionsOf: 0,
  head: 0,
  markAnchored: 0,
  settle: 0,
};
const ANCHOR_ARGS = { atOrAfter: 0, list: 0 };

const TS = '2026-10-12T00:00:00.000Z';
const batch = (
  streamId: string,
  firstSeq: number,
  size: number,
  tag: string,
): AuditEntry[] =>
  Array.from({ length: size }, (_, index) =>
    auditEntry(firstSeq + index, { streamId, sessionId: streamId, runId: tag }),
  );
const position = (
  record: Pick<AuditRecord, 'streamId' | 'journalSeq'>,
): string => `${record.streamId}#${String(record.journalSeq)}`;

for (const bin of pgBins()) {
  pgSuite(`audit chain on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_audit');
    });
    after(async () => {
      await pgAuditLinksIdle();
      await stack?.stop();
    });

    const chain = async () => {
      await pgAuditLinksIdle();
      await resetHarnessPg(stack);
      return {
        store: scopedStore(new PgAuditChainStore(), CHAIN_ARGS),
        transact: <T>(work: (tx: Tx) => Promise<T>, orgId: string) =>
          transactAs(work, orgId),
        atomic: true,
      };
    };
    describeAuditChainStoreContract<Tx>('postgres', chain);
    describeAuditChainQueriesContract<Tx>('postgres', chain);

    const anchors = async () => {
      await resetHarnessPg(stack);
      return scopedStore(new PgAuditAnchorSink(), ANCHOR_ARGS);
    };
    describeAuditAnchorSinkContract('postgres', anchors, [
      hexOrg(),
      hexOrg(),
      hexOrg(),
      hexOrg(),
    ]);
    describeAuditAnchorLookupContract('postgres', anchors);

    describe('PostgreSQL audit chain invariants', () => {
      const saved = auditLinkDefer.ms;
      beforeEach(async () => {
        await pgAuditLinksIdle();
        await resetHarnessPg(stack);
      });
      afterEach(() => {
        auditLinkDefer.ms = saved;
      });
      const store = () => scopedStore(new PgAuditChainStore(), CHAIN_ARGS);
      const commit = (orgId: string, entries: AuditEntry[]) =>
        transactAs(
          (tx) => new PgAuditChainStore().append(orgId, entries, TS, tx),
          orgId,
        );
      const pending = async () =>
        (
          await stack.admin((c) =>
            c.query<{ n: number }>(
              `SELECT count(*)::int AS n FROM harness.audit_pending`,
            ),
          )
        ).rows[0]!.n;

      it('concurrent appenders and concurrent linkers of one org leave one linear chain, every entry once, each append contiguous', async () => {
        const orgId = hexOrg();
        auditLinkDefer.ms = 5;
        const committed = Array.from({ length: 24 }, (_, i) =>
          batch(
            `ses-${String(i % 4)}`,
            Math.floor(i / 4) * 2 + 1,
            2,
            `run-${String(i)}`,
          ),
        );
        // Each stream's appends in journal order, the streams racing one another and the links racing the commits.
        await Promise.all([
          ...[0, 1, 2, 3].map(async (stream) => {
            for (const entries of committed.filter((_, i) => i % 4 === stream))
              await commit(orgId, entries);
          }),
          ...Array.from({ length: 4 }, () => linkAllPendingAuditPg(orgId)),
        ]);
        await Promise.all([
          linkAllPendingAuditPg(orgId),
          linkAllPendingAuditPg(orgId),
          store().settle(orgId),
        ]);
        const records = await store().read(orgId, 0, 1_000);
        expect(
          verifyAuditChain(records, { head: await store().head(orgId) })
            .problems,
        ).to.deep.equal([]);
        expect(records.map(position).sort()).to.deep.equal(
          committed.flat().map(position).sort(),
        );
        for (const entries of committed) {
          const seqs = records
            .filter((record) => record.runId === entries[0]?.runId)
            .map((record) => record.seq);
          expect(seqs).to.deep.equal([seqs[0], (seqs[0] as number) + 1]);
        }
        const lastOfStream = new Map<string, number>();
        for (const record of records) {
          expect(record.journalSeq).to.be.greaterThan(
            lastOfStream.get(record.streamId) ?? 0,
          );
          lastOfStream.set(record.streamId, record.journalSeq);
        }
        expect(await pending()).to.equal(0);
      });

      it('an aborted unit leaves no pending append, no record and no head', async () => {
        const orgId = hexOrg();
        const failed = await transactAs(async (tx) => {
          await new PgAuditChainStore().append(orgId, [auditEntry(1)], TS, tx);
          throw new Error('abort');
        }, orgId).catch((error: Error) => error.message);
        expect(failed).to.equal('abort');
        expect(await pending()).to.equal(0);
        expect(await store().head(orgId)).to.equal(null);
        expect(await store().read(orgId, 0, 10)).to.deep.equal([]);
      });

      it('an append whose process died before its link is reported unlinked, then linked by the next read', async () => {
        const orgId = hexOrg();
        auditLinkDefer.ms = 600_000;
        await commit(orgId, batch('ses-a', 1, 1, 'left'));
        const unlinked = await new PgAuditChainStore().unlinked(orgId);
        expect(unlinked).to.have.length(1);
        expect(unlinked[0]).to.include({ orgId, appends: 1, oldestTs: TS });
        expect(
          (await store().read(orgId, 0, 10)).map((record) => record.runId),
        ).to.deep.equal(['left']);
        expect(await new PgAuditChainStore().unlinked()).to.deep.equal([]);
      });

      it('an append changed after its commit stops its own org only; another org links and anchors', async () => {
        const [bad, good] = [hexOrg(), hexOrg()];
        auditLinkDefer.ms = 600_000;
        await commit(bad, batch('ses-a', 1, 1, 'whole'));
        await commit(good, batch('ses-a', 1, 1, 'fine'));
        await stack.admin((c) =>
          c.query(
            `UPDATE harness.audit_pending SET doc = jsonb_set(doc, '{entries,0,kind}', '"tampered"') WHERE org_id = $1`,
            [bad],
          ),
        );
        expect(await store().read(bad, 0, 10)).to.deep.equal([]);
        expect((await new PgAuditChainStore().unlinked(bad))[0]).to.include({
          orgId: bad,
          appends: 1,
        });
        const heads = await new PgAuditChainStore().unanchored(10);
        expect(heads.map((head) => head.orgId)).to.deep.equal([good]);
      });

      it("with no organisation bound, the anchor job's cross-org read runs as the jobs role; its per-org calls are refused until bound", async () => {
        const [a, b] = [hexOrg(), hexOrg()];
        await commit(a, [auditEntry(1)]);
        await commit(b, [auditEntry(1), auditEntry(2)]);
        const raw = new PgAuditChainStore();
        expect(
          await orgContextStorage.exit(() => raw.markAnchored(a, 1).catch((error: unknown) => error)),
        ).to.be.instanceOf(UnboundOrgError);
        await forEnumeratedOrg(a, 'test-anchor', () => raw.markAnchored(a, 1));
        expect(
          (await orgContextStorage.exit(() => raw.unanchored(10))).map((head) => [head.orgId, head.seq]),
        ).to.deep.equal([[b, 2]]);
        expect((await inOrg(a, () => raw.head(a)))?.anchoredSeq).to.equal(1);
        expect((await inOrg(b, () => raw.head(b)))?.anchoredSeq).to.equal(0);
      });

      it("another org's unit sees no record, head or pending append of this one", async () => {
        auditLinkDefer.ms = 600_000;
        await commit(AUDIT_ORG_1, [auditEntry(1)]);
        await commit(AUDIT_ORG_1, [auditEntry(2)]);
        await store().settle(AUDIT_ORG_1);
        await commit(AUDIT_ORG_1, [auditEntry(3)]);
        const seen = await transactAs(async () => {
          const k = activeUnit()!;
          const count = async (table: string) =>
            (
              await k.query<{ n: number }>(
                `/* pipeshub:harness.test_count */ SELECT count(*)::int AS n FROM harness.${table}`,
              )
            ).rows[0]!.n;
          return [
            await count('audit_records'),
            await count('audit_heads'),
            await count('audit_pending'),
          ];
        }, AUDIT_ORG_2);
        expect(seen).to.deep.equal([0, 0, 0]);
        const refused = await inOrg(AUDIT_ORG_2, () =>
          new PgAuditChainStore()
            .read(AUDIT_ORG_1, 0, 10)
            .catch((e: Error) => e),
        );
        expect(refused).to.be.instanceOf(Error);
      });
    });
  });
}
