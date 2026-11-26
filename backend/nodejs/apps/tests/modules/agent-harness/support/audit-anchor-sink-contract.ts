/**
 * What every `AuditAnchorSink` must do (O-2c): append-only anchors, written
 * once. `InMemoryAuditAnchorSink` and `ObjectLockAuditAnchorSink`
 * (`audit-anchor-sink.test.ts`), `MongoAuditAnchorSink`
 * (`audit-anchor-sink.mongo.itest.ts`).
 */
import { expect } from 'chai';
import { AuditAnchor } from '../../../../src/modules/agent-harness/domain/audit';
import { AuditAnchorConflictError } from '../../../../src/modules/agent-harness/domain/errors';
import { AuditAnchorSink } from '../../../../src/modules/agent-harness/ports/audit';
import { AUDIT_ORG_1 } from './audit-orgs';

const anchor = (seq: number, overrides: Partial<AuditAnchor> = {}): AuditAnchor => ({
  orgId: AUDIT_ORG_1,
  seq,
  hash: `sha256:${String(seq).padStart(64, '0')}`,
  anchoredAt: '2026-10-09T12:00:00.000Z',
  ...overrides,
});

const conflict = async (work: Promise<unknown>): Promise<AuditAnchorConflictError> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(AuditAnchorConflictError);
    return error as AuditAnchorConflictError;
  }
  return expect.fail('expected an AuditAnchorConflictError');
};

export function describeAuditAnchorSinkContract(
  name: string,
  create: () => Promise<AuditAnchorSink>,
  /** Org ids that prefix or encode one another where the sink can hold them; PostgreSQL holds only 24-hex ids. */
  isolatedOrgs: readonly string[] = ['org-1', 'org-10', 'org/1', 'org%2F1'],
): void {
  describe(`AuditAnchorSink contract: ${name}`, () => {
    it('AS-1: an org without anchors lists none; written anchors list back in seq order', async () => {
      const sink = await create();
      expect(await sink.list(AUDIT_ORG_1)).to.deep.equal([]);
      await sink.write(anchor(10));
      await sink.write(anchor(2));
      expect(await sink.list(AUDIT_ORG_1)).to.deep.equal([anchor(2), anchor(10)]);
    });

    it('AS-2: writing the same anchor again is idempotent', async () => {
      const sink = await create();
      await sink.write(anchor(1));
      await sink.write(anchor(1));
      expect(await sink.list(AUDIT_ORG_1)).to.deep.equal([anchor(1)]);
    });

    it('AS-3: another hash at an anchored seq is refused and the first anchor stays', async () => {
      const sink = await create();
      await sink.write(anchor(1));
      const refused = await conflict(sink.write(anchor(1, { hash: 'sha256:rewritten' })));
      expect(refused).to.include({ orgId: AUDIT_ORG_1, seq: 1 });
      expect(await sink.list(AUDIT_ORG_1)).to.deep.equal([anchor(1)]);
    });

    it("AS-4: orgs are isolated, including ids that prefix or encode one another", async () => {
      const sink = await create();
      const orgs = isolatedOrgs;
      for (const [index, orgId] of orgs.entries()) {
        await sink.write(anchor(1, { orgId, hash: `sha256:${String(index)}` }));
      }
      for (const [index, orgId] of orgs.entries()) {
        expect(await sink.list(orgId)).to.deep.equal([anchor(1, { orgId, hash: `sha256:${String(index)}` })]);
      }
      await sink.write(anchor(1, { orgId: isolatedOrgs[1], hash: 'sha256:1' }));
    });

    it('AS-5: concurrent writes of one anchor all succeed and store it once', async () => {
      const sink = await create();
      await Promise.all(Array.from({ length: 5 }, () => sink.write(anchor(3))));
      expect(await sink.list(AUDIT_ORG_1)).to.deep.equal([anchor(3)]);
    });

    it('AS-6: concurrent writes of different hashes at one seq: exactly one wins, the rest are refused as conflicts', async () => {
      const sink = await create();
      const outcomes = await Promise.allSettled(
        Array.from({ length: 5 }, (_, index) => sink.write(anchor(3, { hash: `sha256:${String(index)}` }))),
      );
      expect(outcomes.filter((outcome) => outcome.status === 'fulfilled')).to.have.length(1);
      for (const outcome of outcomes) {
        if (outcome.status === 'rejected') expect(outcome.reason).to.be.instanceOf(AuditAnchorConflictError);
      }
      const winner = outcomes.findIndex((outcome) => outcome.status === 'fulfilled');
      expect(await sink.list(AUDIT_ORG_1)).to.deep.equal([anchor(3, { hash: `sha256:${String(winner)}` })]);
    });
  });
}
