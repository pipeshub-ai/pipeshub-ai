/**
 * The sandbox snapshot index's port contract, run against the in-memory adapter (wp23-snapshot-ttl.test.ts) and the
 * Mongo and PostgreSQL ones (sandbox-snapshots.mongo.itest.ts, sandbox-snapshots.pg.itest.ts).
 */
import { randomBytes } from 'node:crypto';
import { expect } from 'chai';
import type {
  SandboxSnapshotIndex,
  StoredSandboxSnapshot,
} from '../../../../src/modules/agent-harness/ports/sandbox-snapshots';

/** A new org id in the shape every store takes (PostgreSQL checks it). */
const fresh = (): string => randomBytes(12).toString('hex');

export function describeSandboxSnapshotIndexContract(
  name: string,
  make: () => Promise<SandboxSnapshotIndex> | SandboxSnapshotIndex,
): void {
  describe(`SandboxSnapshotIndex contract (${name})`, () => {
    const row = (
      orgId: string,
      objectId: string,
      takenAt: number,
      sessionId = 's1',
    ): StoredSandboxSnapshot => ({
      orgId,
      sessionId,
      runId: 'r1',
      objectId,
      takenAt,
      region: 'eu-west',
    });

    it('SNT-5: a snapshot named again keeps its latest time; an org holding one is listed', async () => {
      const index = await make();
      const org = fresh();
      await index.record([row(org, 'a', 1_000), row(org, 'b', 5_000)]);
      await index.record([row(org, 'a', 9_000)]);
      await index.record([row(org, 'b', 2_000)]);
      expect(await index.orgs()).to.include(org);
      const before = await index.takenBefore(org, 10_000, 10);
      expect(before.map((r) => [r.objectId, r.takenAt, r.region])).to.deep.equal([
        ['b', 5_000, 'eu-west'],
        ['a', 9_000, 'eu-west'],
      ]);
    });

    it("SNT-7: the org's snapshots before a time, oldest first and bounded; another org's never", async () => {
      const index = await make();
      const org = fresh();
      const other = fresh();
      await index.record([
        row(org, 'x', 3_000),
        row(org, 'y', 1_000),
        row(org, 'z', 7_000),
      ]);
      // One event names one org's snapshots: the other org's is its own call.
      await index.record([row(other, 'w', 1_000)]);
      expect((await index.takenBefore(org, 6_000, 10)).map((r) => r.objectId)).to.deep.equal(['y', 'x']);
      expect((await index.takenBefore(org, 6_000, 1)).map((r) => r.objectId)).to.deep.equal(['y']);
    });

    it('SNT-8: forgetting drops only rows still older than the cutoff', async () => {
      const index = await make();
      const org = fresh();
      await index.record([row(org, 'old', 1_000), row(org, 'renamed', 1_000)]);
      await index.record([row(org, 'renamed', 8_000)]);
      expect(await index.forget(org, ['old', 'renamed'], 5_000)).to.equal(1);
      expect((await index.takenBefore(org, 10_000, 10)).map((r) => r.objectId)).to.deep.equal(['renamed']);
      expect(await index.forget(org, [], 5_000)).to.equal(0);
    });

    it("SNT-9: an erased session drops its rows only", async () => {
      const index = await make();
      const org = fresh();
      await index.record([row(org, 'a', 1_000, 's1'), row(org, 'b', 1_000, 's2')]);
      expect(await index.forgetSession(org, 's1')).to.equal(1);
      expect((await index.takenBefore(org, 10_000, 10)).map((r) => r.objectId)).to.deep.equal(['b']);
    });
  });
}
