/**
 * CPD10-93 (PERF-D's handover): an admission's routing, its agent's
 * effective-set review and its pin ask for one snapshot at the same moment;
 * they share one head check and, on a miss, one load. Nothing outlives the
 * read: a later request reads the head again, and a read begun before a
 * write of the org is never shared with a caller after it.
 */
import { expect } from 'chai';
import { ResolvedSnapshotCache } from '../../../src/modules/control-plane/application/snapshot-cache';
import type { Heads } from '../../../src/modules/control-plane/ports/head-revisions';
import type { Snapshot } from '../../../src/modules/control-plane/domain/resolution';
import { aSnapshot } from './support/resolution-fixtures';

describe('CPD10-93: concurrent snapshot reads are one read', () => {
  let heads: number;
  let loads: number;
  let revision: number;
  let release: () => void;

  const cache = () =>
    new ResolvedSnapshotCache({
      heads: {
        head: async () => revision,
        heads: async (): Promise<Heads> => {
          heads += 1;
          return { org: revision, deployment: 0 };
        },
      },
      load: async (orgId, agentKey, actorId): Promise<Snapshot> => {
        loads += 1;
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return { ...aSnapshot({ agentKey, actorId }), orgId, headRevision: revision, deploymentRevision: 0 };
      },
    });

  beforeEach(() => {
    heads = 0;
    loads = 0;
    revision = 4;
  });

  it('three callers at once: one head read, one load, the same snapshot', async () => {
    const snapshots = cache();
    const asked = [1, 2, 3].map(() => snapshots.snapshot('org-1', 'a7', 'u3'));
    await Promise.resolve();
    await new Promise((resolve) => setImmediate(resolve));
    release();
    const [a, b, c] = await Promise.all(asked);
    expect([heads, loads]).to.deep.equal([1, 1]);
    expect(a).to.equal(b);
    expect(b).to.equal(c);
  });

  it('a later request reads the head again: nothing is shared once the read settled', async () => {
    const snapshots = cache();
    const first = snapshots.snapshot('org-1', 'a7', 'u3');
    await new Promise((resolve) => setImmediate(resolve));
    release();
    await first;
    await snapshots.snapshot('org-1', 'a7', 'u3');
    expect([heads, loads]).to.deep.equal([2, 1]);
  });

  it('a caller after a write of the org starts its own read; another org’s read is still shared', async () => {
    const snapshots = cache();
    const before = snapshots.snapshot('org-1', 'a7', 'u3');
    await new Promise((resolve) => setImmediate(resolve));
    const releaseBefore = release;
    revision = 5;
    snapshots.dropBehind({ orgId: 'org-1', revision: 5 });
    const after = snapshots.snapshot('org-1', 'a7', 'u3');
    await new Promise((resolve) => setImmediate(resolve));
    release();
    releaseBefore();
    expect((await after).headRevision).to.equal(5);
    await before;
    expect(loads).to.equal(2);
  });

  it('PERFE2-9: a miss loads under the heads the cache just read, and the loader does not read them again', async () => {
    let given: Heads | undefined;
    const snapshots = new ResolvedSnapshotCache({
      heads: {
        head: async () => revision,
        heads: async (): Promise<Heads> => {
          heads += 1;
          return { org: revision, deployment: 2 };
        },
      },
      load: async (orgId, agentKey, actorId, read): Promise<Snapshot> => {
        given = read;
        return { ...aSnapshot({ agentKey, actorId }), orgId, headRevision: revision, deploymentRevision: 2 };
      },
    });
    await snapshots.snapshot('org-1', 'a7', 'u3');
    expect(given).to.deep.equal({ org: 4, deployment: 2 });
    expect(heads).to.equal(1);
  });

  it('PERFE2-9: with no head to check, the loader reads its own', async () => {
    let given: Heads | undefined = { org: -1, deployment: -1 };
    const snapshots = new ResolvedSnapshotCache({
      load: async (orgId, agentKey, actorId, read): Promise<Snapshot> => {
        given = read;
        return { ...aSnapshot({ agentKey, actorId }), orgId };
      },
    });
    await snapshots.snapshot('org-1', 'a7', 'u3');
    expect(given).to.equal(undefined);
  });
});
