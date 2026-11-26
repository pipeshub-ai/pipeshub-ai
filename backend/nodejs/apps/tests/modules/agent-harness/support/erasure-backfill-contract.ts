/**
 * What every E7B backfill adapter must do (BF-18): `ErasureBackfillStore` and
 * `BackfillSource`, in memory (`erasure-backfill.test.ts`) and on MongoDB
 * (`erasure-backfill.mongo.itest.ts`).
 */
import { expect } from 'chai';
import { Types } from 'mongoose';
import {
  BackfillItem,
  BackfillPlan,
  INITIAL_BACKFILL_CURSOR,
  zeroBackfillCounts,
} from '../../../../src/modules/agent-harness/domain/erasure-backfill';
import {
  BackfillSource,
  ErasureBackfillStore,
} from '../../../../src/modules/agent-harness/ports/erasure-backfill';
import { SeedConversation } from './conversation-seeds';

export type { SeedConversation };

export const hex = (): string => new Types.ObjectId().toHexString();

/** The orgs of the store cases, in the shape every backend takes. */
const O1 = hex();
const O2 = hex();

const plan = (digest: string, operator = 'op-a'): BackfillPlan => ({
  digest,
  orgFilter: null,
  operator,
  createdAt: 1,
  state: 'persisting',
  orgs: [{ orgId: O1, sessions: 3, batches: 2, counts: zeroBackfillCounts() }],
  items: 3,
  cursor: { ...INITIAL_BACKFILL_CURSOR },
  staleReason: null,
});

const item = (digest: string, sessionId: string, batch = 0, orgId = O1): BackfillItem => ({
  digest,
  orgId,
  sessionId,
  batch,
  fingerprint: `sha256:${sessionId}`,
  by: null,
  updatedAt: 7,
  counts: { ...zeroBackfillCounts(), chatSessions: 1 },
  state: 'planned',
});

export function describeErasureBackfillStoreContract(
  name: string,
  create: () => Promise<ErasureBackfillStore<unknown>>,
): void {
  describe(`ErasureBackfillStore contract: ${name} (BF-18)`, () => {
    it('stores a plan once: a second create answers the first', async () => {
      const store = await create();
      const digest = `sha256:${hex()}`;
      expect(await store.plan(digest)).to.equal(null);
      expect(await store.createPlan(plan(digest, 'op-a'))).to.deep.include({ operator: 'op-a', state: 'persisting' });
      expect(await store.createPlan({ ...plan(digest, 'op-b'), state: 'ready' })).to.deep.include({ operator: 'op-a', state: 'persisting' });
      expect((await store.plan(digest))?.orgs).to.deep.equal(plan(digest).orgs);
    });

    it('moves a plan state only from the state named', async () => {
      const store = await create();
      const digest = `sha256:${hex()}`;
      await store.createPlan(plan(digest));
      expect(await store.setPlanState(digest, 'ready', 'completed')).to.equal(false);
      expect(await store.setPlanState(digest, 'persisting', 'ready')).to.equal(true);
      expect(await store.setPlanState(digest, 'ready', 'stale', { staleReason: 'conversation_live' })).to.equal(true);
      expect(await store.plan(digest)).to.deep.include({ state: 'stale', staleReason: 'conversation_live' });
    });

    it('keeps items once, in session-id order, paged, and by batch', async () => {
      const store = await create();
      const digest = `sha256:${hex()}`;
      await store.putItems([item(digest, 'c', 1), item(digest, 'a', 0), item(digest, 'b', 0)]);
      await store.putItems([{ ...item(digest, 'a', 0), fingerprint: 'sha256:changed' }]);
      await store.putItems([item(`sha256:${hex()}`, 'z')]);
      expect((await store.items(digest, null, 10)).map((i) => i.sessionId)).to.deep.equal(['a', 'b', 'c']);
      expect((await store.items(digest, null, 10))[0]?.fingerprint).to.equal('sha256:a');
      expect((await store.items(digest, 'a', 1)).map((i) => i.sessionId)).to.deep.equal(['b']);
      expect((await store.batchItems(digest, O1, 0)).map((i) => i.sessionId)).to.deep.equal(['a', 'b']);
      expect(await store.batchItems(digest, O2, 0)).to.deep.equal([]);
      await store.setItemStates(digest, [{ sessionId: 'b', state: 'enqueued' }]);
      expect((await store.batchItems(digest, O1, 0)).map((i) => i.state)).to.deep.equal(['planned', 'enqueued']);
      expect((await store.items(digest, null, 10))[0]).to.deep.equal(item(digest, 'a', 0));
    });

    it('moves the cursor only from where it is (compare-and-set)', async () => {
      const store = await create();
      const digest = `sha256:${hex()}`;
      await store.createPlan(plan(digest));
      const intended = { org: 0, batch: 0, phase: 'intended' as const };
      expect(await store.moveCursor(digest, { org: 0, batch: 1, phase: 'next' }, intended)).to.equal(false);
      expect(await store.moveCursor(digest, INITIAL_BACKFILL_CURSOR, intended)).to.equal(true);
      expect(await store.moveCursor(digest, INITIAL_BACKFILL_CURSOR, intended)).to.equal(false);
      expect((await store.plan(digest))?.cursor).to.deep.equal(intended);
    });

    it('writes an org marker once; the second is refused and changes nothing', async () => {
      const store = await create();
      const orgId = hex();
      const marker = { orgId, digest: 'sha256:1', operator: 'op', completedAt: 5, erased: 2, withdrawn: 1, skipped: 0 };
      expect(await store.marker(orgId)).to.equal(null);
      expect(await store.putMarker(marker)).to.equal(true);
      expect(await store.putMarker({ ...marker, digest: 'sha256:2' })).to.equal(false);
      expect(await store.marker(orgId)).to.deep.equal(marker);
      expect((await store.markers()).map((m) => m.orgId)).to.include(orgId);
    });
  });
}


export function describeBackfillSourceContract(
  name: string,
  create: () => Promise<{
    source: BackfillSource;
    seed: (conversations: SeedConversation[]) => Promise<void>;
    /** False where no frozen legacy copy can exist (PostgreSQL): none is seeded. */
    legacy?: boolean;
  }>,
): void {
  describe(`BackfillSource contract: ${name} (BF-18)`, () => {
    it('lists deleted conversations only, in session-id order, paged and by org, with their counts', async () => {
      const { source, seed, legacy = true } = await create();
      const org = hex();
      const other = hex();
      const user = hex();
      const ids = [hex(), hex(), hex(), hex()].sort();
      await seed([
        { orgId: org, sessionId: ids[0]!, deleted: true, deletedBy: user, messages: 2, citations: 3, legacyCopies: 1, runs: 1 },
        { orgId: org, sessionId: ids[1]!, deleted: false },
        { orgId: org, sessionId: ids[2]!, deleted: true, marked: true },
        { orgId: other, sessionId: ids[3]!, deleted: true },
      ]);
      const page = (await source.deletedAfter(null, 10, org)).filter((o) => ids.includes(o.sessionId));
      expect(page.map((o) => o.sessionId)).to.deep.equal([ids[0], ids[2]]);
      expect(page[0]).to.deep.include({
        orgId: org,
        deleted: true,
        erasureRequested: false,
        by: `p_${user}`,
        counts: { chatSessions: 1, chatSessionMessages: 2, citations: 6, legacyCopies: legacy ? 1 : 0, agentRuns: 1 },
      });
      expect(page[0]?.updatedAt).to.be.a('number');
      expect(page[1]).to.deep.include({ erasureRequested: true, by: null });
      const after = await source.deletedAfter(ids[0]!, 1, org);
      expect(after.map((o) => o.sessionId)).to.deep.equal([ids[2]]);
      const all = (await source.deletedAfter(null, 100, null)).map((o) => o.sessionId);
      expect(all).to.include.members([ids[0], ids[2], ids[3]]);
      expect(all).to.not.include(ids[1]);
      expect([...all].sort()).to.deep.equal(all);
    });

    it("observes exactly the named sessions of the org now, live ones included, absent and other orgs' left out", async () => {
      const { source, seed } = await create();
      const org = hex();
      const live = hex();
      const gone = hex();
      const foreign = hex();
      await seed([
        { orgId: org, sessionId: live, deleted: false },
        { orgId: hex(), sessionId: foreign, deleted: true },
      ]);
      const seen = await source.observe(org, [live, gone, foreign]);
      expect(seen.map((o) => o.sessionId)).to.deep.equal([live]);
      expect(seen[0]).to.deep.include({ deleted: false, erasureRequested: false });
    });
  });
}
