/**
 * What every `MemoryStore` must do (P7.3 MS-1..MS-6): the in-memory store
 * (`memory-store.test.ts`) and MongoDB (`memory-store.mongo.itest.ts`).
 */
import { expect } from 'chai';
import { MemoryEntry } from '../../../../src/modules/agent-harness/domain/memory';
import {
  MemoryEraser,
  MemoryStore,
} from '../../../../src/modules/agent-harness/ports/memory-store';

/** Two organisations, in the shape every store takes. */
export const MEMORY_ORG = '0000000000000000000000d1';
export const MEMORY_OTHER_ORG = '0000000000000000000000d2';

let counter = 0;

/** A memory with every field set; `overrides` win. */
export const memoryFixture = (
  overrides: Partial<MemoryEntry> = {},
): MemoryEntry => {
  counter += 1;
  return {
    memoryId: `mem_${String(counter).padStart(6, '0')}`,
    orgId: MEMORY_ORG,
    scope: 'user',
    ownerId: 'alice',
    agentKey: null,
    sessionId: 'ses-1',
    runId: 'run-1',
    origin: 'model',
    kind: 'preference',
    content: 'Prefers answers as short bullet lists.',
    status: 'accepted',
    label: { complete: true, sources: [] },
    createdAt: 1_000 + counter,
    updatedAt: 1_000 + counter,
    reviewedBy: null,
    reviewedAt: null,
    replaces: null,
    supersededBy: null,
    ...overrides,
  };
};

export function describeMemoryStoreContract(
  name: string,
  create: () => Promise<MemoryStore & MemoryEraser>,
): void {
  describe(`MemoryStore contract: ${name}`, () => {
    let store: MemoryStore & MemoryEraser;

    beforeEach(async () => {
      store = await create();
    });

    it('MS-1: proposing the same id twice keeps one document', async () => {
      const entry = memoryFixture({ status: 'pending', label: null });
      const first = await store.propose(entry);
      const second = await store.propose({ ...entry, content: 'other text' });
      expect(first.created).to.equal(true);
      expect(second.created).to.equal(false);
      expect(second.memory).to.deep.equal(entry);
      expect(await store.get(entry.orgId, entry.memoryId)).to.deep.equal(entry);
    });

    it('MEM-PAGE-1: a list pages on from the last memory it answered, ties by id, never skipping or repeating one', async () => {
      const tied = [memoryFixture({ createdAt: 70 }), memoryFixture({ createdAt: 70 }), memoryFixture({ createdAt: 70 })];
      const older = memoryFixture({ createdAt: 60 });
      const newest = memoryFixture({ createdAt: 80 });
      for (const entry of [...tied, older, newest]) await store.propose(entry);
      const seen: string[] = [];
      let after: { createdAt: number; memoryId: string } | undefined;
      // Bounded: a store that ignored `after` would answer the first page for ever.
      for (let pages = 0; pages < 10; pages += 1) {
        const page = await store.list(MEMORY_ORG, { ownerId: 'alice', limit: 2, ...(after !== undefined ? { after } : {}) });
        if (page.length === 0) break;
        seen.push(...page.map((entry) => entry.memoryId));
        const last = page[page.length - 1]!;
        after = { createdAt: last.createdAt, memoryId: last.memoryId };
      }
      expect(seen).to.deep.equal([newest, ...tied, older].map((entry) => entry.memoryId));
    });

    it('MS-2: lists by owner, agent, status and run, newest first, limited, never another org', async () => {
      const a = memoryFixture({ createdAt: 10 });
      const b = memoryFixture({ createdAt: 30, status: 'proposed' });
      const c = memoryFixture({ createdAt: 20, ownerId: 'bob' });
      const d = memoryFixture({ createdAt: 40, scope: 'agent', agentKey: 'agent-1', runId: 'run-2' });
      const other = memoryFixture({ createdAt: 50, orgId: MEMORY_OTHER_ORG });
      for (const entry of [a, b, c, d, other]) await store.propose(entry);
      const ids = (entries: MemoryEntry[]) => entries.map((entry) => entry.memoryId);
      expect(ids(await store.list(MEMORY_ORG, { ownerId: 'alice', limit: 10 }))).to.deep.equal([
        d.memoryId,
        b.memoryId,
        a.memoryId,
      ]);
      expect(ids(await store.list(MEMORY_ORG, { agentKey: 'agent-1', limit: 10 }))).to.deep.equal([d.memoryId]);
      expect(
        ids(await store.list(MEMORY_ORG, { ownerId: 'alice', statuses: ['accepted'], scopes: ['user'], limit: 10 })),
      ).to.deep.equal([a.memoryId]);
      expect(ids(await store.list(MEMORY_ORG, { runId: 'run-2', limit: 10 }))).to.deep.equal([d.memoryId]);
      expect(await store.list(MEMORY_ORG, { limit: 2 })).to.have.length(2);
      expect(ids(await store.list(MEMORY_OTHER_ORG, { limit: 10 }))).to.deep.equal([other.memoryId]);
    });

    it('MS-2b: equal creation times come back in id order', async () => {
      const second = memoryFixture({ createdAt: 5, memoryId: 'mem_b' });
      const first = memoryFixture({ createdAt: 5, memoryId: 'mem_a' });
      await store.propose(second);
      await store.propose(first);
      expect((await store.list(MEMORY_ORG, { limit: 10 })).map((entry) => entry.memoryId)).to.deep.equal([
        'mem_a',
        'mem_b',
      ]);
    });

    it('MS-3: an update applies once; a stale expected status writes nothing', async () => {
      const entry = memoryFixture({ status: 'pending', label: null });
      await store.propose(entry);
      const label = { complete: true, sources: [{ kind: 'indexed' as const, recordId: 'r1' }] };
      const updated = await store.update(
        entry.orgId,
        entry.memoryId,
        { status: 'accepted', label, updatedAt: 99 },
        'pending',
      );
      expect(updated).to.deep.include({ status: 'accepted', updatedAt: 99 });
      expect(updated?.label).to.deep.equal(label);
      expect(
        await store.update(entry.orgId, entry.memoryId, { status: 'proposed', updatedAt: 100 }, 'pending'),
      ).to.equal(null);
      expect((await store.get(entry.orgId, entry.memoryId))?.status).to.equal('accepted');
      expect(await store.update(entry.orgId, 'mem_missing', { updatedAt: 1 })).to.equal(null);
    });

    it('MS-4: byIds answers the org\'s named memories only', async () => {
      const a = memoryFixture();
      const b = memoryFixture({ orgId: MEMORY_OTHER_ORG });
      await store.propose(a);
      await store.propose(b);
      const found = await store.byIds(MEMORY_ORG, [a.memoryId, b.memoryId, 'mem_missing', a.memoryId]);
      expect(found.map((entry) => entry.memoryId)).to.deep.equal([a.memoryId]);
      expect(await store.byIds(MEMORY_ORG, [])).to.deep.equal([]);
    });

    it('MS-5: delete removes it once', async () => {
      const entry = memoryFixture();
      await store.propose(entry);
      expect(await store.delete(entry.orgId, entry.memoryId)).to.equal(true);
      expect(await store.get(entry.orgId, entry.memoryId)).to.equal(null);
      expect(await store.delete(entry.orgId, entry.memoryId)).to.equal(false);
    });

    it('MS-6: session, user and org erasure remove exactly theirs', async () => {
      const fromSession = memoryFixture({ sessionId: 'ses-erased', scope: 'agent', agentKey: 'a', ownerId: 'carol' });
      const sessionScoped = memoryFixture({ sessionId: 'ses-erased', scope: 'session' });
      const kept = memoryFixture({ sessionId: 'ses-kept' });
      const bobs = memoryFixture({ ownerId: 'bob', sessionId: 'ses-kept' });
      const otherOrg = memoryFixture({ orgId: MEMORY_OTHER_ORG, sessionId: 'ses-erased', ownerId: 'bob' });
      for (const entry of [fromSession, sessionScoped, kept, bobs, otherOrg]) await store.propose(entry);
      expect(await store.eraseSession(MEMORY_ORG, 'ses-erased')).to.equal(2);
      expect(await store.get(MEMORY_ORG, fromSession.memoryId)).to.equal(null);
      expect(await store.get(MEMORY_ORG, kept.memoryId)).to.not.equal(null);
      expect(await store.eraseUser(MEMORY_ORG, 'bob')).to.equal(1);
      expect(await store.get(MEMORY_ORG, bobs.memoryId)).to.equal(null);
      expect(await store.get(MEMORY_OTHER_ORG, otherOrg.memoryId)).to.not.equal(null);
      expect(await store.eraseOrg(MEMORY_ORG)).to.equal(1);
      expect(await store.list(MEMORY_ORG, { limit: 10 })).to.deep.equal([]);
      expect(await store.list(MEMORY_OTHER_ORG, { limit: 10 })).to.have.length(1);
    });

    it('FIX-R2 C2: a memory superseded by a correction goes when the correction goes, transitively, and nothing else', async () => {
      const oldest = memoryFixture({ sessionId: 'ses-a' });
      const old = memoryFixture({ sessionId: 'ses-a', replaces: oldest.memoryId });
      const current = memoryFixture({ sessionId: 'ses-b', replaces: old.memoryId });
      const unrelated = memoryFixture({ sessionId: 'ses-a' });
      for (const entry of [oldest, old, current, unrelated]) await store.propose(entry);
      await store.update(MEMORY_ORG, oldest.memoryId, { supersededBy: old.memoryId, updatedAt: 2 }, 'accepted');
      await store.update(MEMORY_ORG, old.memoryId, { supersededBy: current.memoryId, updatedAt: 2 }, 'accepted');
      expect((await store.get(MEMORY_ORG, old.memoryId))?.supersededBy).to.equal(current.memoryId);
      expect(await store.delete(MEMORY_ORG, current.memoryId)).to.equal(true);
      expect(await store.get(MEMORY_ORG, old.memoryId)).to.equal(null);
      expect(await store.get(MEMORY_ORG, oldest.memoryId)).to.equal(null);
      expect(await store.get(MEMORY_ORG, unrelated.memoryId)).to.not.equal(null);
    });

    it('FIX-R2 C2: erasing the session a correction came from erases what it superseded; erasing the old one\'s keeps the correction', async () => {
      const old = memoryFixture({ sessionId: 'ses-taught' });
      const correction = memoryFixture({ sessionId: 'ses-corrected', replaces: old.memoryId });
      const other = memoryFixture({ sessionId: 'ses-taught' });
      const elsewhere = memoryFixture({ orgId: MEMORY_OTHER_ORG, sessionId: 'ses-corrected' });
      for (const entry of [old, correction, other, elsewhere]) await store.propose(entry);
      await store.update(MEMORY_ORG, old.memoryId, { supersededBy: correction.memoryId, updatedAt: 2 }, 'accepted');
      expect(await store.eraseSession(MEMORY_ORG, 'ses-corrected')).to.equal(2);
      expect(await store.get(MEMORY_ORG, old.memoryId)).to.equal(null);
      expect(await store.get(MEMORY_ORG, other.memoryId)).to.not.equal(null);
      expect(await store.get(MEMORY_OTHER_ORG, elsewhere.memoryId)).to.not.equal(null);

      const again = memoryFixture({ sessionId: 'ses-taught-2' });
      const fix = memoryFixture({ sessionId: 'ses-fixed-2', replaces: again.memoryId });
      for (const entry of [again, fix]) await store.propose(entry);
      await store.update(MEMORY_ORG, again.memoryId, { supersededBy: fix.memoryId, updatedAt: 2 }, 'accepted');
      expect(await store.eraseSession(MEMORY_ORG, 'ses-taught-2')).to.equal(1);
      expect(await store.get(MEMORY_ORG, fix.memoryId)).to.not.equal(null);
    });

    it('FX4-6: pendingRuns lists the runs with proposals pending from before a time, oldest first, bounded, across orgs', async () => {
      const pending = (overrides: Partial<MemoryEntry>) =>
        memoryFixture({ status: 'pending', label: null, ...overrides });
      for (const entry of [
        pending({ runId: 'run-b', createdAt: 20 }),
        pending({ runId: 'run-a', createdAt: 10 }),
        pending({ runId: 'run-a', createdAt: 30 }),
        pending({ orgId: MEMORY_OTHER_ORG, runId: 'run-a', createdAt: 15 }),
        pending({ runId: 'run-late', createdAt: 100 }),
        pending({ runId: null, createdAt: 5 }),
        memoryFixture({ runId: 'run-settled', status: 'proposed', createdAt: 1 }),
      ]) {
        await store.propose(entry);
      }
      expect(await store.pendingRuns(50, 10)).to.deep.equal([
        { orgId: MEMORY_ORG, runId: 'run-a' },
        { orgId: MEMORY_OTHER_ORG, runId: 'run-a' },
        { orgId: MEMORY_ORG, runId: 'run-b' },
      ]);
      expect(await store.pendingRuns(50, 2)).to.deep.equal([
        { orgId: MEMORY_ORG, runId: 'run-a' },
        { orgId: MEMORY_OTHER_ORG, runId: 'run-a' },
      ]);
      expect(await store.pendingRuns(10, 10)).to.deep.equal([]);
    });

    it('AM-1 (H2): agent erasure removes every memory of that agent, any status, and nothing else', async () => {
      const accepted = memoryFixture({ scope: 'agent', agentKey: 'agent-x', ownerId: 'carol' });
      const proposed = memoryFixture({ scope: 'agent', agentKey: 'agent-x', status: 'proposed', label: null });
      const otherAgent = memoryFixture({ scope: 'agent', agentKey: 'agent-y' });
      const userInRun = memoryFixture({ scope: 'user', agentKey: 'agent-x' });
      const sessionInRun = memoryFixture({ scope: 'session', agentKey: 'agent-x' });
      const otherOrg = memoryFixture({ orgId: MEMORY_OTHER_ORG, scope: 'agent', agentKey: 'agent-x' });
      for (const entry of [accepted, proposed, otherAgent, userInRun, sessionInRun, otherOrg]) await store.propose(entry);
      expect(await store.eraseAgent(MEMORY_ORG, 'agent-x')).to.equal(2);
      expect(await store.get(MEMORY_ORG, accepted.memoryId)).to.equal(null);
      expect(await store.get(MEMORY_ORG, proposed.memoryId)).to.equal(null);
      for (const kept of [otherAgent, userInRun, sessionInRun]) {
        expect(await store.get(MEMORY_ORG, kept.memoryId), kept.scope).to.not.equal(null);
      }
      expect(await store.get(MEMORY_OTHER_ORG, otherOrg.memoryId)).to.not.equal(null);
      expect(await store.eraseAgent(MEMORY_ORG, 'agent-x')).to.equal(0);
    });
  });
}
