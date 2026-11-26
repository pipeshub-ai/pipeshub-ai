/**
 * OWD-16 (SEC-AZ-04): a workspace belongs to one principal. In a shared
 * conversation with collaborator turns on, the session's workspace
 * (`ws:{sessionId}`) is its owner's; a collaborator's run, and every child it
 * spawns, reaches only the collaborator's own workspace and its forks.
 * Node's workspace routes enforce it whatever a worker asks for.
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
import { childRunDocument } from '../../../src/modules/agent-harness/domain/child-runs';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  childForkId,
  childForkOf,
  principalWorkspaceId,
  WorkspaceFileEntry,
  workspaceOfPrincipal,
} from '../../../src/modules/agent-harness/domain/workspace';
import { dataKeyRing } from './support/data-protection';
import { workspaceCode } from './support/workspace-repository-contract';

const OWNER_WS = 'ws:s1';
const MINE = 'ws:s1:p:u2';

const file = (path: string, text: string): WorkspaceFileEntry => ({
  path,
  objectId: `obj-${text}`,
  contentHash: `hmac-sha256:${text}`,
  sizeBytes: text.length,
  mimeType: 'text/plain',
});

const run = (
  runId: string,
  actorId: string,
  sessionRole?: 'owner' | 'collaborator',
  parent?: HarnessRun['parent'],
): HarnessRun =>
  ({
    runId,
    orgId: 'org-1',
    sessionId: 's1',
    actorId,
    ...(sessionRole !== undefined ? { sessionRole } : {}),
    ...(parent !== undefined ? { parent } : {}),
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

describe('OWD-16 per-principal workspaces in a shared conversation', () => {
  let runs: Map<string, HarnessRun>;
  let service: WorkspaceService;
  let lines: Array<[string, Record<string, unknown>]>;
  const owner = run('run-owner', 'u1', 'owner');
  const collaborator = run('run-collab', 'u2', 'collaborator');

  beforeEach(() => {
    runs = new Map();
    lines = [];
    const at = (message: string, meta: Record<string, unknown> = {}) => {
      lines.push([message, meta]);
    };
    const logger: HarnessLogger = { debug: at, info: at, warn: at, error: at };
    const ring = dataKeyRing();
    service = new WorkspaceService({
      runs: {
        get: async (orgId, runId) => runs.get(`${orgId}/${runId}`) ?? null,
      },
      repository: new InMemoryWorkspaceRepository(),
      cipher: ring.cipher,
      keys: new SessionWorkspaceKeys({
        store: new InMemoryWorkspaceKeyStore(),
        cipher: ring.cipher,
        logger,
        now: () => 42,
      }),
      writers: new InMemoryWorkspaceWriterStore(),
      logger,
      now: () => 1_000,
    });
    for (const r of [owner, collaborator]) runs.set(`org-1/${r.runId}`, r);
  });

  const commit = (r: HarnessRun, workspaceId: string, base = 0) =>
    service.commit(claims(r), workspaceId, {
      baseRevision: base,
      reason: 'autosave',
      changes: { upserts: [file('notes.txt', r.actorId)], deletes: [] },
    });

  it("OWD-16: a collaborator's run cannot read or write the owner's ws:{sessionId}", async () => {
    await commit(owner, OWNER_WS);
    expect(await workspaceCode(service.head(claims(collaborator), OWNER_WS))).to.equal(
      'WORKSPACE_FORBIDDEN',
    );
    expect(await workspaceCode(commit(collaborator, OWNER_WS, 1))).to.equal(
      'WORKSPACE_FORBIDDEN',
    );
    expect(
      await workspaceCode(
        service.fork(claims(collaborator), OWNER_WS, {
          atRevision: 1,
          forkId: `${MINE}:fork:c9`,
        }),
      ),
    ).to.equal('WORKSPACE_FORBIDDEN');
    const refused = lines.find(([message]) => message === 'workspace.refused');
    expect(refused?.[1]).to.include({ reason: 'other_principal' });
    expect(JSON.stringify(refused?.[1])).to.not.contain('notes.txt');
  });

  it('OWD-16: the collaborator works in their own workspace, which the owner cannot reach', async () => {
    const mine = await commit(collaborator, MINE);
    expect(mine.revision).to.equal(1);
    expect((await service.head(claims(collaborator), MINE)).files).to.have.length(1);
    expect(await workspaceCode(service.head(claims(owner), MINE))).to.equal(
      'WORKSPACE_FORBIDDEN',
    );
    expect(await service.head(claims(owner), OWNER_WS)).to.include({ revision: 0 });
  });

  it("OWD-16: a collaborator's child inherits the role and writes only its own fork of their workspace", async () => {
    const child = childRunDocument(
      collaborator,
      {
        childRunId: 'c1',
        invocationId: 'inv-1',
        requestHash: 'sha256:x',
        input: { query: 'q' } as never,
      } as never,
      1_000,
      { digest: (_org: string, key: string) => key },
    );
    expect(child).to.include({ actorId: 'u2', sessionRole: 'collaborator' });
    const forkId = childForkOf(MINE, 'c1');
    expect(forkId).to.equal('ws:s1:p:u2:fork:c1');
    const running = { ...child, status: 'running', workerId: 'worker-1', attemptId: 'attempt-c1', fence: 1 } as HarnessRun;
    runs.set('org-1/c1', running);
    await commit(collaborator, MINE);
    await service.fork(claims(collaborator), MINE, { atRevision: 1, forkId });
    expect((await commit(running, forkId, 1)).revision).to.equal(2);
    for (const other of [OWNER_WS, childForkId('s1', 'c1'), MINE]) {
      expect(await workspaceCode(commit(running, other, 1))).to.equal('WORKSPACE_FORBIDDEN');
    }
  });

  it("OWD-16: the owner's ids are unchanged, and match the worker's (harness_core.workspace.forks)", () => {
    expect(principalWorkspaceId({ sessionId: 's1', actorId: 'u1' })).to.equal('ws:s1');
    expect(principalWorkspaceId({ sessionId: 's1', actorId: 'u1', sessionRole: 'owner' })).to.equal('ws:s1');
    expect(principalWorkspaceId({ sessionId: 's1', actorId: 'u2', sessionRole: 'collaborator' })).to.equal(MINE);
    expect(childForkOf('ws:s1:fork:c0', 'c1')).to.equal(childForkId('s1', 'c1'));
    const cases: Array<[string, boolean, boolean]> = [
      ['ws:s1', true, false],
      ['ws:s1:fork:c1', true, false],
      ['ws:s1:p:u2', false, true],
      ['ws:s1:p:u2:fork:c1', false, true],
      ['ws:s1:p:u3', false, false],
      ['ws:s1:p:u2x', false, false],
      ['ws:s2', false, false],
    ];
    for (const [workspaceId, ownerReaches, collaboratorReaches] of cases) {
      expect(workspaceOfPrincipal({ sessionId: 's1', actorId: 'u1' }, workspaceId), workspaceId).to.equal(ownerReaches);
      expect(
        workspaceOfPrincipal({ sessionId: 's1', actorId: 'u2', sessionRole: 'collaborator' }, workspaceId),
        workspaceId,
      ).to.equal(collaboratorReaches);
    }
  });
});
