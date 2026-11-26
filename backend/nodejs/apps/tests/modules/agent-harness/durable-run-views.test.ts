/**
 * U3 RT-*, BV-*, AU-*, WV-*: the session owner's views of a durable run:
 * a run's (or a child's) timeline with its children's merge reports and its
 * spend, who may decide its approvals, and the session workspace's version
 * list and restore (a new revision, through the CAS and the writer lease).
 */
import 'reflect-metadata';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import {
  InMemoryWorkspaceKeyStore,
  InMemoryWorkspaceRepository,
  InMemoryWorkspaceWriterStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-workspace-store';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RunTimelineService } from '../../../src/modules/agent-harness/application/run-timeline-service';
import { SessionInteractionService } from '../../../src/modules/agent-harness/application/session-interactions';
import { SessionWorkspaceKeys } from '../../../src/modules/agent-harness/application/session-workspace-keys';
import { SessionWorkspaceService } from '../../../src/modules/agent-harness/application/session-workspace-service';
import {
  RESTORE_HOLDER_PREFIX,
  WorkspaceService,
} from '../../../src/modules/agent-harness/application/workspace-service';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_TIMELINES,
  AGENT_HARNESS_SESSION_INTERACTIONS,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  AGENT_HARNESS_SESSION_WORKSPACES,
} from '../../../src/modules/agent-harness/composition';
import { AttemptClaims } from '../../../src/modules/agent-harness/domain/authority';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import {
  WorkspaceError,
  WorkspaceFileEntry,
} from '../../../src/modules/agent-harness/domain/workspace';
import { SessionStreamAccess } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import { SessionRole } from '../../../src/modules/agent-harness/domain/session-roles';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { dataKeyRing } from './support/data-protection';
import { workspaceCode } from './support/workspace-repository-contract';

const ORG = 'org-1';
const SESSION = 's1';
const OWNER = 'user-owner';

const file = (path: string, text: string): WorkspaceFileEntry => ({
  path,
  objectId: `obj-${text}`,
  contentHash: `hmac-sha256:${text}`,
  sizeBytes: text.length,
  mimeType: 'text/plain',
});

const run = (runId: string, extra: Partial<HarnessRun> = {}): HarnessRun =>
  ({
    runId,
    orgId: ORG,
    sessionId: SESSION,
    actorId: OWNER,
    harnessMajor: 2,
    status: 'running',
    workerId: 'worker-1',
    attemptId: `attempt-${runId}`,
    fence: 1,
    revision: 3,
    ...extra,
  }) as HarnessRun;

const claims = (r: HarnessRun): AttemptClaims => ({
  orgId: r.orgId,
  runId: r.runId,
  attemptId: r.attemptId ?? '',
  fence: r.fence,
  workerId: r.workerId ?? '',
});

function world() {
  const runs = new Map<string, HarnessRun>();
  const repository = new InMemoryWorkspaceRepository();
  const writers = new InMemoryWorkspaceWriterStore();
  const ring = dataKeyRing();
  const lines: Array<[string, string, Record<string, unknown>]> = [];
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
  let now = 10_000;
  const workspaces = new WorkspaceService({
    runs: { get: async (orgId, runId) => runs.get(`${orgId}/${runId}`) ?? null },
    repository,
    cipher: ring.cipher,
    keys: new SessionWorkspaceKeys({
      store: new InMemoryWorkspaceKeyStore(),
      cipher: ring.cipher,
      logger,
      now: () => 42,
    }),
    writers,
    logger,
    now: () => now,
  });
  const audience: { value: SessionStreamAccess['audience'] | null } = {
    value: 'owner',
  };
  const sessions = {
    findStreamAccess: async () =>
      audience.value === null ? null : { audience: audience.value },
  };
  const parent = run('run-parent');
  runs.set(`${ORG}/run-parent`, parent);
  return {
    runs,
    repository,
    writers,
    lines,
    workspaces,
    audience,
    sessions,
    parent,
    tick: (ms: number) => {
      now += ms;
    },
    owner: new SessionWorkspaceService(sessions, workspaces),
    commit: (
      r: HarnessRun,
      workspaceId: string,
      base: number,
      ...upserts: WorkspaceFileEntry[]
    ) =>
      workspaces.commit(claims(r), workspaceId, {
        baseRevision: base,
        reason: 'autosave',
        changes: { upserts, deletes: [] },
      }),
  };
}

const scope = { orgId: ORG, sessionId: SESSION, actorId: OWNER };

describe('U3: session workspace versions and restore', () => {
  it('WV-1: newest first, the head named, metadata only, bounded and paged', async () => {
    const w = world();
    for (let i = 0; i < 4; i += 1) {
      await w.commit(
        w.parent,
        'ws:s1',
        i,
        file(`f${String(i)}`, `v${String(i)}`),
      );
    }
    const listed = await w.owner.versions(scope, { limit: 3 });
    expect(listed.workspaceId).to.equal('ws:s1');
    expect(listed.head).to.equal(4);
    expect(listed.versions.map((v) => v.revision)).to.deep.equal([4, 3, 2]);
    expect(listed.versions[0]).to.include({
      reason: 'autosave',
      fileCount: 4,
      parentRevision: 3,
      restoredByUser: false,
    });
    expect(JSON.stringify(listed)).not.to.contain('f0');
    const next = await w.owner.versions(scope, { limit: 3, before: 2 });
    expect(next.versions.map((v) => v.revision)).to.deep.equal([1]);
    const many = await w.owner.versions(scope, { limit: 10_000 });
    expect(many.versions).to.have.length(4);
    const empty = await world().owner.versions(scope, {});
    expect(empty).to.deep.equal({
      workspaceId: 'ws:s1',
      head: 0,
      versions: [],
    });
  });

  it('WV-2: restoring revision 2 at head 4 appends revision 5 with revision 2’s files; history is untouched', async () => {
    const w = world();
    await w.commit(w.parent, 'ws:s1', 0, file('a', 'one'));
    await w.commit(w.parent, 'ws:s1', 1, file('a', 'two'));
    await w.commit(w.parent, 'ws:s1', 2, file('a', 'three'), file('b', 'x'));
    await w.commit(w.parent, 'ws:s1', 3, file('a', 'four'));
    const before = JSON.stringify([...w.repository.manifests.values()]);
    const restored = await w.owner.restore(scope, {
      revision: 2,
      baseRevision: 4,
      idempotencyKey: 'k1',
    });
    expect(restored).to.deep.equal({
      workspaceId: 'ws:s1',
      revision: 5,
      restoredFrom: 2,
      replayed: false,
    });
    const head = await w.workspaces.head(claims(w.parent), 'ws:s1');
    const two = await w.workspaces.revision(claims(w.parent), 'ws:s1', 2);
    expect(head).to.deep.include({ revision: 5, parentRevision: 4 });
    expect(head.contentHash).to.equal(two.contentHash);
    expect(head.files).to.deep.equal(two.files);
    const after = [...w.repository.manifests.values()];
    expect(JSON.stringify(after.filter((m) => m.revision < 5))).to.equal(
      before,
    );
    const stored = after.find((m) => m.revision === 5);
    expect(stored?.reason).to.equal('rollback');
    expect(stored?.runId.startsWith(RESTORE_HOLDER_PREFIX)).to.equal(true);
    const listed = await w.owner.versions(scope, {});
    expect(listed.versions[0]).to.include({
      revision: 5,
      restoredByUser: true,
    });
  });

  it('WV-3: a head that moved since the list was read is a conflict; nothing is written', async () => {
    const w = world();
    await w.commit(w.parent, 'ws:s1', 0, file('a', 'one'));
    await w.commit(w.parent, 'ws:s1', 1, file('a', 'two'));
    expect(
      await workspaceCode(
        w.owner.restore(scope, {
          revision: 1,
          baseRevision: 1,
          idempotencyKey: 'k',
        }),
      ),
    ).to.equal('WORKSPACE_CONFLICT');
    expect((await w.repository.head(ORG, 'ws:s1'))?.revision).to.equal(2);
  });

  it('WV-4/WV-5: a live writer refuses the restore and keeps its lease; a restore holds the lease only while it writes', async () => {
    const w = world();
    await w.commit(w.parent, 'ws:s1', 0, file('a', 'one'));
    await w.commit(w.parent, 'ws:s1', 1, file('a', 'two'));
    const held = await w.workspaces.acquireWriter(claims(w.parent), 'ws:s1', {
      ttlMs: 60_000,
    });
    expect(held.acquired).to.equal(true);
    expect(
      await workspaceCode(
        w.owner.restore(scope, {
          revision: 1,
          baseRevision: 2,
          idempotencyKey: 'k',
        }),
      ),
    ).to.equal('WORKSPACE_WRITER_BUSY');
    expect((await w.repository.head(ORG, 'ws:s1'))?.revision).to.equal(2);
    const still = await w.workspaces.acquireWriter(claims(w.parent), 'ws:s1', {
      ttlMs: 60_000,
    });
    expect(still).to.deep.include({ acquired: true, renewed: true });
    await w.workspaces.releaseWriter(claims(w.parent), 'ws:s1');
    await w.owner.restore(scope, {
      revision: 1,
      baseRevision: 2,
      idempotencyKey: 'k',
    });
    // Released after the write: the run writes again at once.
    const again = await w.workspaces.acquireWriter(claims(w.parent), 'ws:s1', {
      ttlMs: 60_000,
    });
    expect(again).to.deep.include({ acquired: true, renewed: false });
  });

  it('WV-5: a run never takes the lease over from a restore while it writes', async () => {
    const w = world();
    await w.commit(w.parent, 'ws:s1', 0, file('a', 'one'));
    await w.writers.acquire(
      {
        orgId: ORG,
        sessionId: SESSION,
        workspaceId: 'ws:s1',
        runId: `${RESTORE_HOLDER_PREFIX}abc`,
        expiresAt: 40_000,
      },
      10_000,
    );
    const refused = await w.workspaces.acquireWriter(
      claims(w.parent),
      'ws:s1',
      {
        ttlMs: 60_000,
      },
    );
    expect(refused).to.deep.equal({
      workspaceId: 'ws:s1',
      acquired: false,
      holder: `${RESTORE_HOLDER_PREFIX}abc`,
    });
    w.tick(31_000);
    const after = await w.workspaces.acquireWriter(claims(w.parent), 'ws:s1', {
      ttlMs: 60_000,
    });
    expect(after.acquired).to.equal(true);
  });

  it('WV-5: a failed write still releases the restore’s lease', async () => {
    const w = world();
    await w.commit(w.parent, 'ws:s1', 0, file('a', 'one'));
    await w.commit(w.parent, 'ws:s1', 1, file('a', 'two'));
    const append = sinon
      .stub(w.repository, 'append')
      .rejects(new WorkspaceError('WORKSPACE_CONFLICT', 'moved'));
    expect(
      await workspaceCode(
        w.owner.restore(scope, {
          revision: 1,
          baseRevision: 2,
          idempotencyKey: 'k',
        }),
      ),
    ).to.equal('WORKSPACE_CONFLICT');
    append.restore();
    const lease = await w.workspaces.acquireWriter(claims(w.parent), 'ws:s1', {
      ttlMs: 60_000,
    });
    expect(lease).to.deep.include({ acquired: true, renewed: false });
  });

  it('WV-6: the same restore asked again answers the revision it wrote', async () => {
    const w = world();
    await w.commit(w.parent, 'ws:s1', 0, file('a', 'one'));
    await w.commit(w.parent, 'ws:s1', 1, file('a', 'two'));
    const request = { revision: 1, baseRevision: 2, idempotencyKey: 'k' };
    const first = await w.owner.restore(scope, request);
    const second = await w.owner.restore(scope, request);
    expect(second).to.deep.equal({ ...first, replayed: true });
    expect((await w.repository.head(ORG, 'ws:s1'))?.revision).to.equal(3);
    expect(
      await workspaceCode(
        w.owner.restore(scope, { ...request, idempotencyKey: 'other' }),
      ),
    ).to.equal('WORKSPACE_CONFLICT');
  });

  it('WV-7: other viewers, missing revisions and revision 0 are refused; nothing is written', async () => {
    const w = world();
    await w.commit(w.parent, 'ws:s1', 0, file('a', 'one'));
    for (const audience of ['shared', null] as const) {
      w.audience.value = audience;
      let refused: unknown;
      try {
        await w.owner.restore(scope, {
          revision: 1,
          baseRevision: 1,
          idempotencyKey: 'k',
        });
      } catch (error) {
        refused = error;
      }
      expect(refused, String(audience)).to.be.instanceOf(HarnessNotFoundError);
      let listed: unknown;
      try {
        await w.owner.versions(scope, {});
      } catch (error) {
        listed = error;
      }
      expect(listed).to.be.instanceOf(HarnessNotFoundError);
    }
    w.audience.value = 'owner';
    for (const revision of [0, 7]) {
      expect(
        await workspaceCode(
          w.owner.restore(scope, {
            revision,
            baseRevision: 1,
            idempotencyKey: 'k',
          }),
        ),
      ).to.equal('WORKSPACE_REVISION_NOT_FOUND');
    }
    expect((await w.repository.head(ORG, 'ws:s1'))?.revision).to.equal(1);
  });

  it('WV-8: logs say what was restored or why not, never a path', async () => {
    const w = world();
    await w.commit(w.parent, 'ws:s1', 0, file('SENTINEL/a.md', 'one'));
    await w.commit(w.parent, 'ws:s1', 1, file('SENTINEL/a.md', 'two'));
    await w.owner.restore(scope, {
      revision: 1,
      baseRevision: 2,
      idempotencyKey: 'k',
    });
    await workspaceCode(
      w.owner.restore(scope, {
        revision: 1,
        baseRevision: 2,
        idempotencyKey: 'x',
      }),
    );
    const restored = w.lines.filter(([, m]) => m === 'workspace.restored');
    const refused = w.lines.filter(
      ([, m]) => m === 'workspace.restore_refused',
    );
    expect(restored).to.have.length(1);
    expect(restored[0]?.[2]).to.include({
      revision: 3,
      toRevision: 1,
      orgId: ORG,
      sessionId: SESSION,
    });
    expect(
      refused.map(([level, , meta]) => [level, meta['reason']]),
    ).to.deep.equal([['warn', 'WORKSPACE_CONFLICT']]);
    expect(JSON.stringify(w.lines)).not.to.contain('SENTINEL');
  });
});

describe('U3: a run’s timeline for its session’s owner', () => {
  const journalOf = (
    runId: string,
    events: Array<[string, Record<string, unknown>]>,
  ) =>
    events.map(
      ([type, data], index): JournalEvent => ({
        orgId: ORG,
        sessionId: SESSION,
        streamId: runId === 'run-parent' ? SESSION : runId,
        seq: index + 1,
        runId,
        type,
        schemaVersion: 1,
        critical: true,
        ts: '2026-10-04T12:00:00.000Z',
        writer: 'worker',
        visibility: 'owner',
        data,
      }),
    );

  async function timelineWorld() {
    const w = world();
    const child = run('run-child', {
      parent: { runId: 'run-parent', invocationId: 't1.0', depth: 1 },
    } as Partial<HarnessRun>);
    w.runs.set(`${ORG}/run-child`, child);
    // The child's fork merged with a conflict on `a` (P6.4).
    await w.commit(w.parent, 'ws:s1', 0, file('a', 'base'));
    await w.workspaces.fork(claims(w.parent), 'ws:s1', {
      atRevision: 1,
      forkId: 'ws:s1:fork:run-child',
    });
    await w.commit(w.parent, 'ws:s1', 1, file('a', 'parent'));
    await w.commit(
      child,
      'ws:s1:fork:run-child',
      1,
      file('a', 'child'),
      file('b', 'new'),
    );
    await w.workspaces.merge(claims(w.parent), 'ws:s1', {
      forkId: 'ws:s1:fork:run-child',
      mergeKey: 'run-child',
      onConflict: 'keepTarget',
    });
    w.runs.set(`${ORG}/run-child`, { ...child, status: 'completed' });
    const journals = new Map<string, JournalEvent[]>([
      [
        'run-parent',
        journalOf('run-parent', [
          [
            'child.spawned',
            { childRunId: 'run-child', invocationId: 't1.0', taskId: 'jira' },
          ],
          [
            'child.spawned',
            { childRunId: 'run-unmerged', invocationId: 't1.1' },
          ],
          ['child.completed', { childRunId: 'run-child', outcome: 'answered' }],
        ]),
      ],
      [
        'run-child',
        journalOf('run-child', [
          [
            'x.pipeshub.engine.progress',
            {
              tool: 'code_task',
              taskId: 'run-child',
              kind: 'started',
              text: 'goal',
              data: {},
            },
          ],
        ]),
      ],
    ]);
    const streamAccess = sinon
      .stub()
      .callsFake(async (_scope, runId: string) => {
        const found = w.runs.get(`${ORG}/${runId}`);
        if (found === undefined) throw new HarnessNotFoundError();
        if (w.audience.value === null) throw new HarnessNotFoundError();
        return { run: found, access: { audience: w.audience.value } };
      });
    const ledger = new InMemoryBudgetLedger(() => 10_000);
    await ledger.openRoot(
      ORG,
      'run-parent',
      { usdMicros: 5_000_000, tokens: 1_000_000 },
      { usdMicros: 420_000, tokens: 1_234 },
    );
    const service = new RunTimelineService({
      runs: { streamAccess },
      journal: {
        readRun: async (_org, runId, after) =>
          (journals.get(runId) ?? []).filter((e) => e.seq > after),
      },
      journals: { resolve: async (events) => events },
      workspaces: w.workspaces,
      ledger,
    });
    return { w, service };
  }

  it('WP89-C1 (C10.05): the run cost route reads its status and spend from the ledger, the owner only', async () => {
    const { w, service } = await timelineWorld();
    const view = await service.cost(scope, 'run-parent');
    expect(view.status).to.equal('running');
    expect(view.budget?.committed).to.deep.equal({ usdMicros: 420_000, tokens: 1_234 });
    w.audience.value = 'viewer' as never;
    let refused: unknown;
    try {
      await service.cost(scope, 'run-parent');
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessNotFoundError);
  });

  it('RT-1/RT-3/BV-1: the parent: children with what their merge did, and its spend', async () => {
    const { service } = await timelineWorld();
    const view = await service.timeline(scope, 'run-parent');
    expect(view).to.include({
      runId: 'run-parent',
      parentRunId: null,
      status: 'running',
    });
    expect(view.parts.map((p) => p['type'])).to.deep.equal([
      'sub_agent',
      'sub_agent',
    ]);
    const [merged, unmerged] = view.children;
    expect(merged).to.deep.include({
      runId: 'run-child',
      role: 'pipeshub-subagent-jira',
      status: 'completed',
    });
    expect(merged?.merge).to.deep.include({
      status: 'partial',
      into: 'ws:s1',
      revision: 3,
      forkRevision: 2,
      merged: ['b'],
      repositoryConflicts: [],
    });
    expect(merged?.merge?.conflicts.map((c) => [c.path, c.kind])).to.deep.equal(
      [['a', 'both_changed']],
    );
    expect(unmerged?.merge).to.equal(null);
    expect(view.budget).to.deep.equal({
      cap: { usdMicros: 5_000_000, tokens: 1_000_000 },
      committed: { usdMicros: 420_000, tokens: 1_234 },
      reserved: { usdMicros: 0, tokens: 0 },
      available: { usdMicros: 4_580_000, tokens: 998_766 },
      exhausted: false,
      estimated: 0,
    });
  });

  it('RT-1/BV-1: a child’s timeline comes from its own stream; a run without an account has no budget', async () => {
    const { service } = await timelineWorld();
    const view = await service.timeline(scope, 'run-child');
    expect(view).to.include({
      runId: 'run-child',
      parentRunId: 'run-parent',
      status: 'completed',
    });
    expect(view.engine?.frames.map((f) => f.kind)).to.deep.equal(['started']);
    expect(view.parts.map((p) => p['type'])).to.deep.equal(['engine_task']);
    expect(view.merge).to.deep.include({
      status: 'partial',
      revision: 3,
      forkRevision: 2,
    });
    expect(view.budget).to.equal(null);
    expect((await service.timeline(scope, 'run-parent')).merge).to.equal(null);
  });

  it('RT-2: a shared viewer or a stranger is refused', async () => {
    const { w, service } = await timelineWorld();
    for (const audience of ['shared', null] as const) {
      w.audience.value = audience;
      let refused: unknown;
      try {
        await service.timeline(scope, 'run-parent');
      } catch (error) {
        refused = error;
      }
      expect(refused, String(audience)).to.be.instanceOf(HarnessNotFoundError);
    }
  });
});

describe('U3: who may decide a run’s approvals', () => {
  const authority = async (
    actorId: string,
    role: SessionRole | null,
    approvers: string[] = [],
    admins: string[] = [],
  ) => {
    const service = new SessionInteractionService({
      executions: {
        get: async (_org: string, runId: string) =>
          runId === 'run-parent' ? run('run-parent') : null,
      } as never,
      interactions: {} as never,
      sessions: {
        findStreamAccess: async () =>
          role === null
            ? null
            : { audience: role === 'owner' ? 'owner' : 'shared' },
      } as never,
      roles: { roleOf: async () => role },
      approvers: new InMemoryApproverDirectory(
        new Map([[ORG, new Set(approvers)]]),
        new Map([[ORG, new Set(admins)]]),
      ),
    });
    return service.authority(
      { orgId: ORG, sessionId: SESSION, actorId },
      'run-parent',
    );
  };

  it('AU-1, AP-06: the decision route’s own rule; only the run’s actor may edit', async () => {
    expect(await authority(OWNER, 'owner')).to.deep.equal({
      approve: true,
      deny: true,
      edit: true,
    });
    expect(
      await authority('approver', 'collaborator', ['approver']),
    ).to.deep.equal({
      approve: true,
      deny: true,
      edit: false,
    });
    expect(await authority('owner-2', 'owner')).to.deep.equal({
      approve: false,
      deny: true,
      edit: false,
    });
    expect(await authority('admin', 'viewer', [], ['admin'])).to.deep.equal({
      approve: false,
      deny: true,
      edit: false,
    });
    expect(await authority('collab', 'collaborator')).to.deep.equal({
      approve: false,
      deny: false,
      edit: false,
    });
  });

  it('AU-2: not a participant, or another session’s run: 404', async () => {
    let refused: unknown;
    try {
      await authority('stranger', null);
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessNotFoundError);
  });
});

describe('U3: the owner’s routes (OA-1)', () => {
  let server: Server;
  let base: string;
  const calls: Record<string, unknown[]> = {};
  let originalLogger: unknown;

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: ORG, userId: OWNER };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_TIMELINES).toConstantValue({
      timeline: async (s: unknown, runId: string) => {
        (calls['timeline'] ??= []).push([s, runId]);
        if (runId === 'run-missing') throw new HarnessNotFoundError();
        return { runId };
      },
    });
    container.bind(AGENT_HARNESS_SESSION_INTERACTIONS).toConstantValue({
      authority: async () => ({ approve: false, deny: true }),
    });
    container.bind(AGENT_HARNESS_SESSION_WORKSPACES).toConstantValue({
      versions: async (s: unknown, page: unknown) => {
        (calls['versions'] ??= []).push([s, page]);
        return { workspaceId: 'ws:s1', head: 0, versions: [] };
      },
      restore: async (s: unknown, request: { baseRevision: number }) => {
        (calls['restore'] ??= []).push([s, request]);
        if (request.baseRevision === 9) {
          throw new WorkspaceError('WORKSPACE_CONFLICT', 'moved');
        }
        if (request.baseRevision === 8) {
          throw new WorkspaceError('WORKSPACE_WRITER_BUSY', 'busy');
        }
        return {
          workspaceId: 'ws:s1',
          revision: 5,
          restoredFrom: 2,
          replayed: false,
        };
      },
    });
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = {
      error: sinon.stub(),
      warn: sinon.stub(),
      info: sinon.stub(),
      debug: sinon.stub(),
    };
    server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
  });

  after(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const json = (body: unknown) => ({
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });

  it('serves the timeline and the approval authority on agent and chat sessions', async () => {
    for (const session of [
      '/conversations/s1',
      '/agents/agent-1/conversations/s1',
    ]) {
      const timeline = await fetch(`${base}${session}/runs/run-1/timeline`);
      expect(timeline.status, session).to.equal(200);
      expect(await timeline.json()).to.deep.equal({ runId: 'run-1' });
      const authority = await fetch(
        `${base}${session}/runs/run-1/interactions/authority`,
      );
      expect(await authority.json()).to.deep.equal({
        approve: false,
        deny: true,
      });
      expect(
        (await fetch(`${base}${session}/runs/run-missing/timeline`)).status,
      ).to.equal(404);
    }
    expect((calls['timeline']?.[0] as unknown[])[0]).to.deep.equal({
      orgId: ORG,
      actorId: OWNER,
      sessionId: 's1',
    });
  });

  it('lists versions with a bounded query; refuses a bad one', async () => {
    const ok = await fetch(
      `${base}/conversations/s1/workspace/versions?limit=20&before=7`,
    );
    expect(ok.status).to.equal(200);
    expect((calls['versions']?.at(-1) as unknown[])[1]).to.deep.equal({
      limit: 20,
      before: 7,
    });
    for (const query of [
      'limit=0',
      'limit=101',
      'before=0',
      'limit=x',
      'other=1',
    ]) {
      const bad = await fetch(
        `${base}/conversations/s1/workspace/versions?${query}`,
      );
      expect(bad.status, query).to.equal(400);
    }
  });

  it('restores; maps a conflict and a busy writer to 409 with their codes; validates the body', async () => {
    const ok = await fetch(
      `${base}/agents/agent-1/conversations/s1/workspace/versions/2/restore`,
      json({ baseRevision: 4, idempotencyKey: 'restore-2-4' }),
    );
    expect(ok.status).to.equal(200);
    expect(await ok.json()).to.deep.equal({
      workspaceId: 'ws:s1',
      revision: 5,
      restoredFrom: 2,
      replayed: false,
    });
    expect((calls['restore']?.at(-1) as unknown[])[1]).to.deep.equal({
      revision: 2,
      baseRevision: 4,
      idempotencyKey: 'restore-2-4',
    });
    for (const [base_, code] of [
      [9, 'WORKSPACE_CONFLICT'],
      [8, 'WORKSPACE_WRITER_BUSY'],
    ] as const) {
      const refused = await fetch(
        `${base}/conversations/s1/workspace/versions/2/restore`,
        json({ baseRevision: base_, idempotencyKey: 'k' }),
      );
      expect(refused.status).to.equal(409);
      expect(JSON.stringify(await refused.json())).to.contain(code);
    }
    for (const body of [
      {},
      { baseRevision: -1, idempotencyKey: 'k' },
      { baseRevision: 1 },
      { baseRevision: 1, idempotencyKey: 'k', extra: 1 },
    ]) {
      const bad = await fetch(
        `${base}/conversations/s1/workspace/versions/2/restore`,
        json(body),
      );
      expect(bad.status, JSON.stringify(body)).to.equal(400);
    }
    const badRevision = await fetch(
      `${base}/conversations/s1/workspace/versions/x/restore`,
      json({ baseRevision: 1, idempotencyKey: 'k' }),
    );
    expect(badRevision.status).to.equal(400);
  });
});
