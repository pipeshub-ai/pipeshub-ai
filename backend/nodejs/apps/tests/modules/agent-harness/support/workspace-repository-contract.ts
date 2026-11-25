/**
 * NW-9: what every `WorkspaceRepository` and `WorkspaceKeyStore` must do,
 * run on the in-memory adapters (unit) and on Mongo (replica-set itest).
 */
import { expect } from 'chai';
import { WorkspaceError } from '../../../../src/modules/agent-harness/domain/workspace';
import {
  MergeRecord,
  StoredManifest,
  WorkspaceKeyStore,
  WorkspaceRepository,
  WorkspaceWriterStore,
  WriterLeaseRecord,
} from '../../../../src/modules/agent-harness/ports/workspace-store';
import { without } from './without';

/** Two organisations, in the shape every store takes. */
export const WS_ORG = '0000000000000000000000c1';
export const WS_OTHER_ORG = '0000000000000000000000c2';

export interface WorkspaceStoresUnderTest {
  repository: WorkspaceRepository;
  keys: WorkspaceKeyStore;
  writers: WorkspaceWriterStore;
}

const record = (key: string, revision: number | null): MergeRecord => ({
  key,
  into: 'ws:s1',
  base: 1,
  theirs: 2,
  ours: 1,
  revision,
  status: 'partial',
});

const lease = (
  runId: string,
  expiresAt: number,
  workspaceId = 'ws:s1',
): WriterLeaseRecord => ({
  orgId: WS_ORG,
  sessionId: 's1',
  workspaceId,
  runId,
  expiresAt,
});

const SEALED = { v: 1, kid: 'k', kv: 1, iv: 'aXY=', tag: 'dGFn', ct: 'Y3Q=' };

export const storedManifest = (
  workspaceId: string,
  revision: number,
  orgId = WS_ORG,
): StoredManifest => ({
  orgId,
  workspaceId,
  revision,
  parentRevision: revision > 1 ? revision - 1 : null,
  contentHash: `sha256:${workspaceId}:${String(revision)}`,
  fileCount: 1,
  files: SEALED,
  reason: 'autosave',
  runId: 'run-1',
  createdAt: 1_000 + revision,
});

export async function workspaceCode(work: Promise<unknown>): Promise<string> {
  try {
    await work;
  } catch (error) {
    if (error instanceof WorkspaceError) return error.code;
    throw error;
  }
  throw new Error('expected a WorkspaceError');
}

export function describeWorkspaceStoresContract(
  label: string,
  make: () => Promise<WorkspaceStoresUnderTest>,
): void {
  describe(`NW-9 workspace stores contract (${label})`, () => {
    let stores: WorkspaceStoresUnderTest;

    beforeEach(async () => {
      stores = await make();
    });

    it('appends revisions under a head CAS', async () => {
      const { repository } = stores;
      expect(await repository.head(WS_ORG, 'ws:s1')).to.equal(null);
      await repository.append({
        manifest: storedManifest('ws:s1', 1),
        expectedRevision: 0,
      });
      await repository.append({
        manifest: storedManifest('ws:s1', 2),
        expectedRevision: 1,
      });
      const head = await repository.head(WS_ORG, 'ws:s1');
      expect(head).to.deep.include({
        revision: 2,
        forkOf: null,
        mergeBase: null,
      });
      expect(await repository.manifest(WS_ORG, 'ws:s1', 1)).to.deep.equal(
        storedManifest('ws:s1', 1),
      );
    });

    it('U3 WV-9: lists revisions newest first, bounded and paged, without their file lists', async () => {
      const { repository } = stores;
      for (let revision = 1; revision <= 5; revision += 1) {
        await repository.append({
          manifest: storedManifest('ws:s1', revision),
          expectedRevision: revision - 1,
        });
      }
      await repository.append({
        manifest: storedManifest('ws:s1', 1, WS_OTHER_ORG),
        expectedRevision: 0,
      });
      const first = await repository.revisions(WS_ORG, 'ws:s1', { limit: 3 });
      expect(first.map((m) => m.revision)).to.deep.equal([5, 4, 3]);
      const summary = without(storedManifest('ws:s1', 5), 'files');
      expect(first[0]).to.deep.equal(summary);
      expect(first[0]).not.to.have.property('files');
      const next = await repository.revisions(WS_ORG, 'ws:s1', {
        limit: 3,
        before: 3,
      });
      expect(next.map((m) => m.revision)).to.deep.equal([2, 1]);
      expect(
        await repository.revisions(WS_ORG, 'ws:other', { limit: 3 }),
      ).to.deep.equal([]);
    });

    it('a stale expected revision writes nothing', async () => {
      const { repository } = stores;
      await repository.append({
        manifest: storedManifest('ws:s1', 1),
        expectedRevision: 0,
      });
      expect(
        await workspaceCode(
          repository.append({
            manifest: storedManifest('ws:s1', 1),
            expectedRevision: 0,
          }),
        ),
      ).to.equal('WORKSPACE_CONFLICT');
      expect(
        await workspaceCode(
          repository.append({
            manifest: storedManifest('ws:s1', 3),
            expectedRevision: 2,
          }),
        ),
      ).to.equal('WORKSPACE_CONFLICT');
      expect(await repository.manifest(WS_ORG, 'ws:s1', 3)).to.equal(null);
      expect((await repository.head(WS_ORG, 'ws:s1'))?.revision).to.equal(1);
    });

    it('records a fork’s origin and moves its merge base with the merge', async () => {
      const { repository } = stores;
      await repository.append({
        manifest: storedManifest('ws:s1', 1),
        expectedRevision: 0,
      });
      await repository.append({
        manifest: storedManifest('ws:s1:c', 1),
        expectedRevision: 0,
        forkOf: { workspaceId: 'ws:s1', revision: 1 },
      });
      expect(await repository.head(WS_ORG, 'ws:s1:c')).to.deep.include({
        forkOf: { workspaceId: 'ws:s1', revision: 1 },
        mergeBase: 1,
      });
      await repository.append({
        manifest: storedManifest('ws:s1:c', 2),
        expectedRevision: 1,
      });
      await repository.append({
        manifest: storedManifest('ws:s1', 2),
        expectedRevision: 1,
        mergedFork: { workspaceId: 'ws:s1:c', mergeBase: 2 },
      });
      expect((await repository.head(WS_ORG, 'ws:s1:c'))?.mergeBase).to.equal(
        2,
      );
      await repository.setMergeBase(WS_ORG, 'ws:s1:c', 3);
      expect((await repository.head(WS_ORG, 'ws:s1:c'))?.mergeBase).to.equal(
        3,
      );
    });

    it('P6.4: a keyed merge is recorded on the fork with the merge base, revisions only', async () => {
      const { repository } = stores;
      await repository.append({
        manifest: storedManifest('ws:s1', 1),
        expectedRevision: 0,
      });
      await repository.append({
        manifest: storedManifest('ws:s1:fork:c', 1),
        expectedRevision: 0,
        forkOf: { workspaceId: 'ws:s1', revision: 1 },
      });
      await repository.append({
        manifest: storedManifest('ws:s1:fork:c', 2),
        expectedRevision: 1,
      });
      await repository.append({
        manifest: storedManifest('ws:s1', 2),
        expectedRevision: 1,
        mergedFork: {
          workspaceId: 'ws:s1:fork:c',
          mergeBase: 2,
          record: record('child-c', 2),
        },
      });
      expect(await repository.head(WS_ORG, 'ws:s1:fork:c')).to.deep.include({
        mergeBase: 2,
        lastMerge: record('child-c', 2),
      });
      await repository.setMergeBase(
        WS_ORG,
        'ws:s1:fork:c',
        2,
        record('child-d', null),
      );
      expect(
        (await repository.head(WS_ORG, 'ws:s1:fork:c'))?.lastMerge,
      ).to.deep.equal(record('child-d', null));
      await repository.append({
        manifest: storedManifest('ws:s1:fork:c', 3),
        expectedRevision: 2,
      });
      expect(
        (await repository.head(WS_ORG, 'ws:s1:fork:c'))?.lastMerge,
      ).to.deep.equal(record('child-d', null));
      expect((await repository.head(WS_ORG, 'ws:s1'))?.lastMerge ?? null).to.equal(
        null,
      );
    });

    it('P6.4 NF-2: one live writer per workspace; the holder renews and releases', async () => {
      const { writers } = stores;
      expect(await writers.acquire(lease('run-a', 2_000), 1_000)).to.deep.equal({
        acquired: true,
        lease: lease('run-a', 2_000),
        renewed: false,
      });
      expect(await writers.acquire(lease('run-b', 2_500), 1_100)).to.deep.equal({
        acquired: false,
        holder: 'run-a',
      });
      expect(
        (await writers.acquire(lease('run-a', 3_000), 1_200)).acquired,
      ).to.equal(true);
      expect(await writers.acquire(lease('run-a', 3_000), 1_200)).to.deep.include({
        renewed: true,
      });
      expect(
        await writers.acquire(lease('run-b', 4_000, 'ws:s1:fork:x'), 1_300),
      ).to.deep.include({ acquired: true });
      expect(await writers.release(WS_ORG, 'ws:s1', 'run-b')).to.equal(false);
      expect(
        (await writers.acquire(lease('run-b', 4_000), 1_300)).acquired,
      ).to.equal(false);
      expect(await writers.release(WS_ORG, 'ws:s1', 'run-a')).to.equal(true);
      expect(await writers.acquire(lease('run-b', 4_000), 1_400)).to.deep.include({
        acquired: true,
        renewed: false,
      });
      expect(await writers.acquire(lease('run-a', 9_000), 4_000)).to.deep.include({
        acquired: true,
        renewed: false,
      });
      expect(
        await writers.acquire({ ...lease('run-z', 9_000), orgId: WS_OTHER_ORG }, 4_100),
      ).to.deep.include({ acquired: true });
    });

    it('a merge naming something that is not a fork moves nothing', async () => {
      const { repository } = stores;
      await repository.append({
        manifest: storedManifest('ws:s1', 1),
        expectedRevision: 0,
      });
      expect(
        await workspaceCode(
          repository.append({
            manifest: storedManifest('ws:s1', 2),
            expectedRevision: 1,
            mergedFork: { workspaceId: 'ws:s1', mergeBase: 1 },
          }),
        ),
      ).to.equal('WORKSPACE_NOT_A_FORK');
      expect((await repository.head(WS_ORG, 'ws:s1'))?.revision).to.equal(1);
      expect(await repository.manifest(WS_ORG, 'ws:s1', 2)).to.equal(null);
      expect(
        await workspaceCode(repository.setMergeBase(WS_ORG, 'ws:s1', 1)),
      ).to.equal('WORKSPACE_NOT_A_FORK');
    });

    it('orgs do not see each other’s workspaces', async () => {
      const { repository } = stores;
      await repository.append({
        manifest: storedManifest('ws:s1', 1),
        expectedRevision: 0,
      });
      expect(await repository.head(WS_OTHER_ORG, 'ws:s1')).to.equal(null);
      expect(await repository.manifest(WS_OTHER_ORG, 'ws:s1', 1)).to.equal(null);
      await repository.append({
        manifest: storedManifest('ws:s1', 1, WS_OTHER_ORG),
        expectedRevision: 0,
      });
    });

    it('keeps one key per session and a tombstone after a shred', async () => {
      const { keys } = stores;
      const record = {
        orgId: WS_ORG,
        sessionId: 's1',
        keyId: 'key-1',
        wrapped: SEALED,
        createdAt: 1,
        destroyedAt: null,
      };
      expect(await keys.get(WS_ORG, 's1')).to.equal(null);
      expect(await keys.insert(record)).to.equal(true);
      expect(await keys.insert({ ...record, keyId: 'key-2' })).to.equal(false);
      expect(await keys.get(WS_ORG, 's1')).to.deep.equal(record);
      expect(await keys.get(WS_OTHER_ORG, 's1')).to.equal(null);
      expect(await keys.destroy(WS_ORG, 's1', 5)).to.equal(true);
      expect(await keys.get(WS_ORG, 's1')).to.deep.include({
        keyId: 'key-1',
        wrapped: null,
        destroyedAt: 5,
      });
      expect(await keys.destroy(WS_ORG, 's1', 9)).to.equal(false);
      expect((await keys.get(WS_ORG, 's1'))?.destroyedAt).to.equal(5);
      expect(await keys.insert(record)).to.equal(false);
      expect(await keys.destroy(WS_ORG, 'never-keyed', 7)).to.equal(false);
      expect(
        await keys.insert({ ...record, sessionId: 'never-keyed' }),
      ).to.equal(false);
    });

    it('P6.4 NF-2: of concurrent acquirers of one workspace exactly one holds it', async () => {
      const { writers } = stores;
      for (const round of [1, 2, 3]) {
        const workspaceId = `ws:s1:round-${String(round)}`;
        const answers = await Promise.all(
          Array.from({ length: 8 }, (_, index) =>
            writers.acquire(
              {
                orgId: WS_ORG,
                sessionId: 's1',
                workspaceId,
                runId: `run-${String(index)}`,
                expiresAt: 10_000,
              },
              1_000,
            ),
          ),
        );
        const winners = answers.filter((answer) => answer.acquired);
        expect(winners, JSON.stringify(answers)).to.have.length(1);
        const holder = winners[0]!.acquired ? winners[0]!.lease.runId : '';
        for (const answer of answers) {
          if (!answer.acquired) expect(answer.holder).to.equal(holder);
        }
      }
      // An expired lease taken by two at once: still one winner.
      const expired = await Promise.all(
        ['run-x', 'run-y', 'run-z'].map((runId) =>
          writers.acquire(
            {
              orgId: WS_ORG,
              sessionId: 's1',
              workspaceId: 'ws:s1:round-1',
              runId,
              expiresAt: 30_000,
            },
            20_000,
          ),
        ),
      );
      expect(expired.filter((answer) => answer.acquired)).to.have.length(1);
    });

    it('NW-10: of concurrent commits on one base exactly one wins', async () => {
      const { repository } = stores;
      await repository.append({
        manifest: storedManifest('ws:s1', 1),
        expectedRevision: 0,
      });
      const outcomes = await Promise.all(
        Array.from({ length: 8 }, (_, index) =>
          workspaceCode(
            repository.append({
              manifest: {
                ...storedManifest('ws:s1', 2),
                runId: `run-${String(index)}`,
              },
              expectedRevision: 1,
            }),
          ).catch((error: unknown) => {
            if (error instanceof Error && error.message === 'expected a WorkspaceError') return 'ok';
            throw error;
          }),
        ),
      );
      expect(outcomes.filter((o) => o === 'ok')).to.have.length(1);
      expect(outcomes.filter((o) => o === 'WORKSPACE_CONFLICT')).to.have.length(7);
      expect(
        (await repository.revisions(WS_ORG, 'ws:s1', { limit: 10 })).map((m) => m.revision),
      ).to.deep.equal([2, 1]);
    });

    it('NW-10: concurrent first commits create one head', async () => {
      const { repository } = stores;
      const outcomes = await Promise.all(
        Array.from({ length: 6 }, () =>
          workspaceCode(
            repository.append({
              manifest: storedManifest('ws:s2', 1),
              expectedRevision: 0,
            }),
          ).catch(() => 'ok'),
        ),
      );
      expect(outcomes.filter((o) => o === 'ok')).to.have.length(1);
      expect((await repository.head(WS_ORG, 'ws:s2'))?.revision).to.equal(1);
    });
  });
}
