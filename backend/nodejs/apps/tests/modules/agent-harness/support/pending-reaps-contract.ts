/**
 * CLEANUP-G6: the store of reaps the broker refused, as every adapter keeps it.
 */
import { expect } from 'chai';
import { PendingReaps } from '../../../../src/modules/agent-harness/ports/sandbox-reaper';

export function describePendingReapsContract(
  name: string,
  make: () => Promise<PendingReaps>,
): void {
  describe(`PendingReaps contract: ${name}`, () => {
    let store: PendingReaps;
    beforeEach(async () => {
      store = await make();
    });

    it('CLEANUP-G6: a refused reap is due at its time, once per label set, until done', async () => {
      const now = await store.now();
      const labels = { orgId: 'org-1', runId: 'run-1' };
      await store.defer(labels, now + 1_000, 'Error');
      await store.defer({ runId: 'run-1', orgId: 'org-1' }, now + 5_000, 'Error');
      await store.defer({ orgId: 'org-2', runId: 'run-1' }, now + 2_000, 'Error');
      expect(await store.due(now, 10)).to.deep.equal([]);
      const [first, second] = await store.due(now + 2_000, 10);
      expect(first).to.deep.include({ labels, failures: 1 });
      expect(second?.labels).to.deep.equal({ orgId: 'org-2', runId: 'run-1' });
      await store.later(first!.id, now + 10_000, 'TypeError');
      expect((await store.due(now + 2_000, 10)).map((d) => d.labels['orgId'])).to.deep.equal(['org-2']);
      expect(await store.due(now + 10_000, 1)).to.have.length(1);
      expect((await store.due(now + 10_000, 10)).find((d) => d.id === first!.id)?.failures).to.equal(2);
      await store.done(first!.id);
      await store.done(second!.id);
      expect(await store.due(now + 60_000, 10)).to.deep.equal([]);
    });
  });
}
