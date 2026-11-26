/**
 * OWD-12 on PostgreSQL: the sandbox snapshot index (`harness.sandbox_snapshots`), its port contract, a snapshot
 * recorded in the event's unit that exists only if it commits, and the row stamp.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgSandboxSnapshotIndex } from '../../../src/libs/repositories/agent-harness/sandbox-snapshots/pg';
import { describeSandboxSnapshotIndexContract } from './support/sandbox-snapshots-contract';
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

const SNAPSHOT_ARGS = { takenBefore: 0, forget: 0, forgetSession: 0 };

for (const bin of pgBins()) {
  pgSuite(`WP-23 the snapshot index on PostgreSQL (${bin})`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_region');
    });
    after(async () => {
      await stack?.stop();
    });

    describeSandboxSnapshotIndexContract('postgres', () =>
      scopedStore(new PgSandboxSnapshotIndex(), SNAPSHOT_ARGS),
    );

    describe('PostgreSQL snapshot index', () => {
      beforeEach(() => resetHarnessPg(stack));
      const snapshot = (orgId: string, objectId: string) => ({
        orgId,
        sessionId: 's1',
        runId: null,
        objectId,
        takenAt: 1_000,
        region: 'eu-west',
      });

      it("a snapshot recorded in the event's unit exists only if that unit commits", async () => {
        const orgId = hexOrg();
        const index = new PgSandboxSnapshotIndex();
        await transactAs(
          (tx) => index.record([snapshot(orgId, 'kept')], tx),
          orgId,
        );
        await transactAs(async (tx) => {
          await index.record([snapshot(orgId, 'aborted')], tx);
          throw new Error('abort');
        }, orgId).catch(() => undefined);
        const listed = await scopedStore(index, SNAPSHOT_ARGS).takenBefore(
          orgId,
          10_000,
          10,
        );
        expect(listed.map((row) => row.objectId)).to.deep.equal(['kept']);
      });

      it('the cross-org scan names every org holding a snapshot; each row carries the deployment region', async () => {
        const [a, b] = [hexOrg(), hexOrg()];
        const index = new PgSandboxSnapshotIndex();
        await inOrg(a, () => index.record([snapshot(a, 'x')]));
        await inOrg(b, () => index.record([snapshot(b, 'y')]));
        expect((await index.orgs()).sort()).to.deep.equal([a, b].sort());
        const rows = await stack.admin((c) =>
          c.query(`SELECT DISTINCT region FROM harness.sandbox_snapshots`),
        );
        expect(rows.rows).to.have.length(1);
      });
    });
  });
}
