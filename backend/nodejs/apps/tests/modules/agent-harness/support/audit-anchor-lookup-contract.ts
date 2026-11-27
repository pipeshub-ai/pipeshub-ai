/**
 * What every `AuditAnchorLookup` must do (AUDITUI): the first anchor at or
 * after a seq, as listing the org's anchors and taking the first would answer,
 * without listing them. `InMemoryAuditAnchorSink` (`audit-anchor-sink.test.ts`)
 * and `MongoAuditAnchorSink` (`audit-anchor-sink.mongo.itest.ts`).
 */
import { expect } from 'chai';
import { AuditAnchor } from '../../../../src/modules/agent-harness/domain/audit';
import { AuditAnchorSink } from '../../../../src/modules/agent-harness/ports/audit';
import { AuditAnchorLookup } from '../../../../src/modules/agent-harness/ports/audit-queries';
import { AUDIT_ORG_1, AUDIT_ORG_2, AUDIT_ORG_3, AUDIT_ORG_10 } from './audit-orgs';

const anchor = (seq: number, orgId = AUDIT_ORG_1): AuditAnchor => ({
  orgId,
  seq,
  hash: `sha256:${String(seq).padStart(64, '0')}`,
  anchoredAt: '2026-10-09T12:00:00.000Z',
});

export function describeAuditAnchorLookupContract(
  name: string,
  create: () => Promise<AuditAnchorSink & AuditAnchorLookup>,
): void {
  describe(`AuditAnchorLookup contract: ${name}`, () => {
    it('AL-1: an org without anchors has none at or after any seq', async () => {
      const lookup = await create();
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 0)).to.equal(null);
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 7)).to.equal(null);
    });

    it('AL-2: an anchor at the seq is the answer; between anchors, the next one; past the last, none', async () => {
      const lookup = await create();
      for (const seq of [64, 8, 32]) await lookup.write(anchor(seq));
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 8)).to.deep.equal(anchor(8));
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 9)).to.deep.equal(anchor(32));
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 0)).to.deep.equal(anchor(8));
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 64)).to.deep.equal(anchor(64));
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 65)).to.equal(null);
    });

    it('AL-3: every seq of a long chain is covered as listing the anchors would answer it', async () => {
      const lookup = await create();
      const seqs = [3, 4, 10, 17, 40, 41, 99, 128, 200];
      await Promise.all(seqs.map((seq) => lookup.write(anchor(seq))));
      const listed = await lookup.list(AUDIT_ORG_1);
      for (let seq = 0; seq <= 205; seq += 1) {
        const expected = listed.find((a) => a.seq >= seq) ?? null;
        expect(await lookup.atOrAfter(AUDIT_ORG_1, seq), `seq ${String(seq)}`).to.deep.equal(expected);
      }
    });

    it("AL-4: orgs are isolated: another org's anchor, even an earlier one, is never the answer", async () => {
      const lookup = await create();
      await lookup.write(anchor(5, AUDIT_ORG_2));
      await lookup.write(anchor(50, AUDIT_ORG_1));
      await lookup.write(anchor(500, AUDIT_ORG_10));
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 1)).to.deep.equal(anchor(50));
      expect(await lookup.atOrAfter(AUDIT_ORG_2, 6)).to.equal(null);
      expect(await lookup.atOrAfter(AUDIT_ORG_1, 51)).to.equal(null);
      expect(await lookup.atOrAfter(AUDIT_ORG_3, 0)).to.equal(null);
    });
  });
}
