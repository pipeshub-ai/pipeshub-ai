/**
 * P3.2 Node side: the workspace service's authority, sealing, key and log
 * rules (NW-2..NW-4, NW-7, NW-8) over the in-memory stores, and the stores
 * contract (NW-9) on the in-memory adapters.
 */
import { expect } from 'chai';
import {
  InMemoryWorkspaceKeyStore,
  InMemoryWorkspaceRepository,
  InMemoryWorkspaceWriterStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-workspace-store';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { SessionWorkspaceKeys } from '../../../src/modules/agent-harness/application/session-workspace-keys';
import { WorkspaceService } from '../../../src/modules/agent-harness/application/workspace-service';
import { AttemptClaims } from '../../../src/modules/agent-harness/domain/authority';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { WorkspaceFileEntry } from '../../../src/modules/agent-harness/domain/workspace';
import { dataKeyRing, DataKeyRing } from './support/data-protection';
import {
  describeWorkspaceStoresContract,
  workspaceCode,
} from './support/workspace-repository-contract';

const SECRET_PATH = 'reports/SENTINEL-q3-layoffs.xlsx';

const file = (path: string, text: string): WorkspaceFileEntry => ({
  path,
  objectId: `obj-${text}`,
  contentHash: `hmac-sha256:${text}`,
  sizeBytes: text.length,
  mimeType: 'text/plain',
});

const run = (runId: string, sessionId: string, orgId = 'org-1'): HarnessRun =>
  ({
    runId,
    orgId,
    sessionId,
    status: 'running',
    workerId: 'worker-1',
    attemptId: `attempt-${runId}`,
    fence: 1,
  }) as HarnessRun;

const claims = (r: HarnessRun): AttemptClaims => ({
  orgId: r.orgId,
  runId: r.runId,
  attemptId: r.attemptId ?? '',
  fence: r.fence,
  workerId: r.workerId ?? '',
});

describeWorkspaceStoresContract('in-memory', async () => ({
  repository: new InMemoryWorkspaceRepository(),
  keys: new InMemoryWorkspaceKeyStore(),
  writers: new InMemoryWorkspaceWriterStore(),
}));

describe('P3.2 workspace service', () => {
  let runs: Map<string, HarnessRun>;
  let repository: InMemoryWorkspaceRepository;
  let keyStore: InMemoryWorkspaceKeyStore;
  let ring: DataKeyRing;
  let service: WorkspaceService;
  let keys: SessionWorkspaceKeys;
  let lines: Array<[string, string, Record<string, unknown>]>;
  let a: HarnessRun;
  let now: number;

  beforeEach(() => {
    runs = new Map();
    repository = new InMemoryWorkspaceRepository();
    keyStore = new InMemoryWorkspaceKeyStore();
    ring = dataKeyRing();
    lines = [];
    const at =
      (level: string) =>
      (message: string, meta: Record<string, unknown> = {}) => {
        lines.push([level, message, meta]);
      };
    const logger: HarnessLogger = {
      debug: at('debug'),
      info: at('info'),
      warn: at('warn'),
      error: at('error'),
    };
    keys = new SessionWorkspaceKeys({ store: keyStore, cipher: ring.cipher, logger, now: () => 42 });
    service = new WorkspaceService({
      runs: { get: async (orgId, runId) => runs.get(`${orgId}/${runId}`) ?? null },
      repository,
      cipher: ring.cipher,
      keys,
      writers: new InMemoryWorkspaceWriterStore(),
      logger,
      now: () => now,
    });
    now = 1_000;
    a = run('run-a', 's1');
    runs.set('org-1/run-a', a);
  });

  const commit = (
    r: HarnessRun,
    workspaceId: string,
    base: number,
    ...upserts: WorkspaceFileEntry[]
  ) =>
    service.commit(claims(r), workspaceId, {
      baseRevision: base,
      reason: 'autosave',
      changes: { upserts, deletes: [] },
    });

  it('commits, reads back and seals the file list at rest (NW-4)', async () => {
    const committed = await commit(a, 'ws:s1', 0, file(SECRET_PATH, 'x'));
    expect(committed).to.deep.include({
      revision: 1,
      parentRevision: null,
      createdAt: 1_000,
    });
    expect(await service.head(claims(a), 'ws:s1')).to.deep.equal(committed);
    const atRest = JSON.stringify([...repository.manifests.values()]);
    expect(atRest).to.not.contain('SENTINEL');
    expect(atRest).to.not.contain('hmac-sha256:x');
    expect(await workspaceCode(commit(a, 'ws:s1', 0, file('b', 'y')))).to.equal(
      'WORKSPACE_CONFLICT',
    );
  });

  it('NW-2: refuses a workspace of another session and logs why', async () => {
    for (const workspaceId of ['ws:s2', 'ws:s10', 'ws:s2:child']) {
      expect(
        await workspaceCode(service.head(claims(a), workspaceId)),
      ).to.equal('WORKSPACE_FORBIDDEN');
    }
    expect(
      await workspaceCode(
        service.fork(claims(a), 'ws:s1', { atRevision: 0, forkId: 'ws:s2:x' }),
      ),
    ).to.equal('WORKSPACE_FORBIDDEN');
    const refusal = lines.find(
      ([, message]) => message === 'workspace.refused',
    );
    expect(refusal?.[2]).to.deep.include({
      reason: 'not_in_session',
      orgId: 'org-1',
      sessionId: 's1',
      runId: 'run-a',
      attemptId: 'attempt-run-a',
      fence: 1,
    });
  });

  it('NW-3: an attempt that no longer owns the run cannot write', async () => {
    const stale = { ...claims(a), attemptId: 'attempt-old' };
    let error: unknown;
    try {
      await service.commit(stale, 'ws:s1', {
        baseRevision: 0,
        reason: 'autosave',
        changes: { upserts: [file('a', 'a')], deletes: [] },
      });
    } catch (caught) {
      error = caught;
    }
    expect(error).to.be.instanceOf(HarnessConflictError);
    expect((error as HarnessConflictError).code).to.equal('STALE_ATTEMPT');
    expect(repository.heads.size).to.equal(0);
  });

  it('forks, merges disjoint changes, reports overlaps and repositories, and rolls back', async () => {
    await commit(a, 'ws:s1', 0, file('a', '1'), file('.git/HEAD', 'h'));
    await service.fork(claims(a), 'ws:s1', {
      atRevision: 1,
      forkId: 'ws:s1:l',
    });
    await service.fork(claims(a), 'ws:s1', {
      atRevision: 1,
      forkId: 'ws:s1:r',
    });
    await commit(a, 'ws:s1:l', 1, file('a', '2'), file('.git/refs/l', '1'));
    await commit(a, 'ws:s1:r', 1, file('a', '3'), file('.git/refs/r', '1'));
    const left = await service.merge(claims(a), 'ws:s1', { forkId: 'ws:s1:l' });
    expect(left.status).to.equal('merged');
    const right = await service.merge(claims(a), 'ws:s1', {
      forkId: 'ws:s1:r',
    });
    expect(right).to.deep.include({
      status: 'conflict',
      repositoryConflicts: ['.git'],
    });
    expect(right.conflicts.map((c) => c.path)).to.deep.equal(['a']);
    expect(right.manifest.revision).to.equal(2);
    const rolled = await service.rollback(claims(a), 'ws:s1', {
      toRevision: 1,
    });
    expect(rolled.revision).to.equal(3);
    expect(rolled.contentHash).to.equal(
      (await service.revision(claims(a), 'ws:s1', 1)).contentHash,
    );
    expect(
      await workspaceCode(
        service.merge(claims(a), 'ws:s1', { forkId: 'ws:s1' }),
      ),
    ).to.equal('WORKSPACE_NOT_A_FORK');
  });

  it('two concurrent retries of one fork get the same fork', async () => {
    await commit(a, 'ws:s1', 0, file('a', '1'));
    const [first, second] = await Promise.all([
      service.fork(claims(a), 'ws:s1', { atRevision: 1, forkId: 'ws:s1:c' }),
      service.fork(claims(a), 'ws:s1', { atRevision: 1, forkId: 'ws:s1:c' }),
    ]);
    expect(second).to.deep.equal(first);
    expect(
      await workspaceCode(
        service.fork(claims(a), 'ws:s1', { atRevision: 0, forkId: 'ws:s1:c' }),
      ),
    ).to.equal('WORKSPACE_FORK_CONFLICT');
  });

  it('NW-7: one key per session, wrapped at rest; a shred is final', async () => {
    const b = run('run-b', 's1');
    const other = run('run-c', 's2');
    runs.set('org-1/run-b', b);
    runs.set('org-1/run-c', other);
    const first = await service.issueKey(claims(a));
    const again = await service.issueKey(claims(b));
    expect(first.keyDomain).to.equal('ws:s1');
    expect(again.key.equals(first.key)).to.equal(true);
    expect(again.keyId).to.equal(first.keyId);
    const elsewhere = await service.issueKey(claims(other));
    expect(elsewhere.key.equals(first.key)).to.equal(false);
    const atRest = JSON.stringify([...keyStore.records.values()]);
    expect(atRest).to.not.contain(first.key.toString('base64'));
    expect(atRest).to.not.contain(first.key.toString('hex'));

    expect(await keys.shred('org-1', 's1')).to.equal(true);
    expect(await workspaceCode(service.issueKey(claims(a)))).to.equal(
      'WORKSPACE_KEY_DESTROYED',
    );
    expect(
      (await service.issueKey(claims(other))).key.equals(elsewhere.key),
    ).to.equal(true);
  });

  it('NW-7: an org shred makes the session key unreadable too', async () => {
    await service.issueKey(claims(a));
    await ring.keys.shred('org-1');
    expect(await workspaceCode(service.issueKey(claims(a)))).to.equal(
      'WORKSPACE_KEY_DESTROYED',
    );
  });

  it('NW-8: lifecycle lines carry correlation fields, never a path or a key', async () => {
    await commit(a, 'ws:s1', 0, file(SECRET_PATH, 'x'));
    await service.fork(claims(a), 'ws:s1', {
      atRevision: 1,
      forkId: 'ws:s1:c',
    });
    await commit(a, 'ws:s1:c', 1, file('more', 'y'));
    await service.merge(claims(a), 'ws:s1', { forkId: 'ws:s1:c' });
    await service.rollback(claims(a), 'ws:s1', { toRevision: 1 });
    const issued = await service.issueKey(claims(a));
    const events = lines.map(([level, message]) => `${level}:${message}`);
    expect(events).to.include.members([
      'info:workspace.committed',
      'info:workspace.forked',
      'info:workspace.merged',
      'info:workspace.rolled_back',
      'info:workspace.key_created',
      'info:workspace.key_issued',
    ]);
    const committed = lines.find(([, m]) => m === 'workspace.committed')?.[2];
    expect(committed).to.deep.include({
      orgId: 'org-1',
      sessionId: 's1',
      runId: 'run-a',
      attemptId: 'attempt-run-a',
      fence: 1,
      workspaceId: 'ws:s1',
      revision: 1,
      reason: 'autosave',
    });
    const all = JSON.stringify(lines);
    expect(all).to.not.contain('SENTINEL');
    expect(all).to.not.contain(issued.key.toString('base64'));
  });

  describe('P6.4', () => {
    const forkOf = async (forkId: string) =>
      service.fork(claims(a), 'ws:s1', {
        atRevision: (await service.head(claims(a), 'ws:s1')).revision,
        forkId,
      });

    it('NF-1: keepTarget merges the rest, keeps the target’s side and reports it', async () => {
      await commit(a, 'ws:s1', 0, file('a', 'base'));
      await forkOf('ws:s1:fork:c');
      await commit(a, 'ws:s1', 1, file('a', 'parent'));
      await commit(a, 'ws:s1:fork:c', 1, file('a', 'child'), file('b', 'new'));
      const merged = await service.merge(claims(a), 'ws:s1', {
        forkId: 'ws:s1:fork:c',
        mergeKey: 'child-c',
        onConflict: 'keepTarget',
      });
      expect(merged).to.deep.include({
        status: 'partial',
        merged: ['b'],
        forkRevision: 2,
        repositoryConflicts: [],
      });
      expect(merged.conflicts.map((c) => c.path)).to.deep.equal(['a']);
      expect(merged.manifest.revision).to.equal(3);
      const files = new Map(merged.manifest.files.map((f) => [f.path, f]));
      expect(files.get('a')?.contentHash).to.equal('hmac-sha256:parent');
      expect(files.get('b')?.contentHash).to.equal('hmac-sha256:new');
      const atRest = JSON.stringify(
        await repository.head('org-1', 'ws:s1:fork:c'),
      );
      expect(atRest).to.not.contain('"a"');
      expect(atRest).to.contain('child-c');
    });

    it('NF-1: the same merge key answers the recorded merge and writes nothing', async () => {
      await commit(a, 'ws:s1', 0, file('a', 'base'));
      await forkOf('ws:s1:fork:c');
      await commit(a, 'ws:s1:fork:c', 1, file('c', 'c'));
      const request = {
        forkId: 'ws:s1:fork:c',
        mergeKey: 'child-c',
        onConflict: 'keepTarget' as const,
      };
      const first = await service.merge(claims(a), 'ws:s1', request);
      await commit(a, 'ws:s1', 2, file('later', 'l'));
      const replay = await service.merge(claims(a), 'ws:s1', request);
      expect(replay).to.deep.equal(first);
      expect((await service.head(claims(a), 'ws:s1')).revision).to.equal(3);
      expect(lines.map(([, m]) => m)).to.include('workspace.merge_replayed');
      const unkeyed = await service.merge(claims(a), 'ws:s1', {
        forkId: 'ws:s1:fork:c',
      });
      expect(unkeyed.status).to.equal('up_to_date');
    });

    it('R27REST-FU-1: a merge says how it changed each path of the target, and a replay says the same', async () => {
      await commit(a, 'ws:s1', 0, file('kept', 'k'), file('gone', 'g'));
      await forkOf('ws:s1:fork:c');
      await service.commit(claims(a), 'ws:s1:fork:c', {
        baseRevision: 1,
        reason: 'autosave',
        changes: { upserts: [file('kept', 'child'), file('out/q3.csv', 'n')], deletes: ['gone'] },
      });
      const request = { forkId: 'ws:s1:fork:c', mergeKey: 'child-c', onConflict: 'keepTarget' as const };
      const first = await service.merge(claims(a), 'ws:s1', request);
      expect(first.merged).to.deep.equal(['gone', 'kept', 'out/q3.csv']);
      expect(first.kinds).to.deep.equal({ gone: 'deleted', kept: 'modified', 'out/q3.csv': 'added' });
      const replay = await service.merge(claims(a), 'ws:s1', request);
      expect(replay.kinds).to.deep.equal(first.kinds);
      const again = await service.merge(claims(a), 'ws:s1', { forkId: 'ws:s1:fork:c' });
      expect(again).to.deep.include({ status: 'up_to_date', kinds: {} });
    });

    it('NF-2: a writer lease is answered, renewed, refused by name and released', async () => {
      const b = run('run-b', 's1');
      runs.set('org-1/run-b', b);
      const first = await service.acquireWriter(claims(a), 'ws:s1', {
        ttlMs: 500,
      });
      expect(first).to.deep.equal({
        workspaceId: 'ws:s1',
        acquired: true,
        holder: 'run-a',
        expiresAt: 1_500,
        renewed: false,
      });
      expect(
        await service.acquireWriter(claims(b), 'ws:s1', { ttlMs: 500 }),
      ).to.deep.equal({ workspaceId: 'ws:s1', acquired: false, holder: 'run-a' });
      expect(
        await service.acquireWriter(claims(a), 'ws:s1', { ttlMs: 500 }),
      ).to.deep.include({ renewed: true });
      expect(await service.releaseWriter(claims(b), 'ws:s1')).to.deep.equal({
        workspaceId: 'ws:s1',
        released: false,
      });
      now = 1_600;
      expect(
        await service.acquireWriter(claims(b), 'ws:s1', { ttlMs: 500 }),
      ).to.deep.include({ acquired: true, holder: 'run-b' });
      expect(await service.releaseWriter(claims(b), 'ws:s1')).to.deep.equal({
        workspaceId: 'ws:s1',
        released: true,
      });
      const events = lines.map(([level, message]) => `${level}:${message}`);
      expect(events).to.include.members([
        'info:workspace.writer_acquired',
        'warn:workspace.writer_busy',
        'info:workspace.writer_released',
      ]);
    });

    it('NF-2: the lease of a run that ended is taken over before it expires', async () => {
      const b = run('run-b', 's1');
      runs.set('org-1/run-b', b);
      await service.acquireWriter(claims(a), 'ws:s1', { ttlMs: 60_000 });
      runs.set('org-1/run-a', { ...a, status: 'failed' } as HarnessRun);
      expect(
        await service.acquireWriter(claims(b), 'ws:s1', { ttlMs: 500 }),
      ).to.deep.include({ acquired: true, holder: 'run-b', renewed: false });
      expect(lines.map(([, m]) => m)).to.include('workspace.writer_taken_over');
    });

    it('CF-7: a child writes only its own fork; reads stay session-wide', async () => {
      await commit(a, 'ws:s1', 0, file('a', '1'));
      await forkOf('ws:s1:fork:child-1');
      await forkOf('ws:s1:fork:child-2');
      const child = {
        ...run('child-1', 's1'),
        parent: { runId: 'run-a', invocationId: 't1.0', depth: 1 },
      } as HarnessRun;
      runs.set('org-1/child-1', child);
      expect(
        (await commit(child, 'ws:s1:fork:child-1', 1, file('x', 'x'))).revision,
      ).to.equal(2);
      for (const target of ['ws:s1', 'ws:s1:fork:child-2']) {
        expect(
          await workspaceCode(commit(child, target, 1, file('y', 'y'))),
        ).to.equal('WORKSPACE_FORBIDDEN');
        expect(
          await workspaceCode(
            service.acquireWriter(claims(child), target, { ttlMs: 500 }),
          ),
        ).to.equal('WORKSPACE_FORBIDDEN');
      }
      expect(
        await workspaceCode(
          service.rollback(claims(child), 'ws:s1', { toRevision: 0 }),
        ),
      ).to.equal('WORKSPACE_FORBIDDEN');
      expect(
        await workspaceCode(
          service.fork(claims(child), 'ws:s1', {
            atRevision: 1,
            forkId: 'ws:s1:fork:child-2',
          }),
        ),
      ).to.equal('WORKSPACE_FORBIDDEN');
      expect((await service.head(claims(child), 'ws:s1')).revision).to.equal(1);
      expect(
        lines.some(
          ([, m, meta]) =>
            m === 'workspace.refused' && meta['reason'] === 'not_writable',
        ),
      ).to.equal(true);
    });

    it('CF-4/CF-5: a child’s fork is taken at the head once, then reused at its own revision', async () => {
      await commit(a, 'ws:s1', 0, file('a', '1'));
      expect(
        await service.forkForChild(claims(a), 'ws:s1', 'ws:s1:fork:c'),
      ).to.deep.equal({ workspaceId: 'ws:s1', revision: 1 });
      await commit(a, 'ws:s1', 1, file('a', '2'));
      expect(
        await service.forkForChild(claims(a), 'ws:s1', 'ws:s1:fork:c'),
      ).to.deep.equal({ workspaceId: 'ws:s1', revision: 1 });
      await commit(a, 'ws:s1:other', 0, file('o', 'o'));
      expect(
        await workspaceCode(
          service.forkForChild(claims(a), 'ws:s1', 'ws:s1:other'),
        ),
      ).to.equal('WORKSPACE_FORK_CONFLICT');
    });
  });
});
