/**
 * WP-26 GIT-FLOW, the repository picker's server side (C3.79's entry point, UI-4):
 *
 * - `GET/PUT/DELETE …/repository` beside `…/coding-engine`, owner only, org and user from the credential, saved by the
 *   chat's preferences store as one audited control-plane write (RP-1..RP-6);
 * - the checks and their catalogue codes: REPOSITORY_NOT_GITHUB, REPOSITORY_TOOLSET_MISSING (the toolset store asked
 *   with the caller's own credential), REPOSITORY_NEEDS_ENGINE, REPOSITORY_LOCKED (RP-3..RP-5);
 * - admission: a turn that hands work to an engine pins `x.pipeshub.repository` and joins the GitHub toolset's
 *   `create_pull_request` to the grant; its code_task (engine) child inherits both; a loop child and a turn with no
 *   engine pin none; a new chat's `repository` is checked, refused with its code, and saved once admitted (RA-1..RA-5).
 *
 * The parent and child pins are the shared vector `contracts/harness/vectors/repository-pin.json`, which the Python
 * worker's test reads (`tests/unit/harness/engines/test_wp26_repository_pin.py`).
 */
import 'reflect-metadata';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { SessionRepositoryService } from '../../../src/modules/agent-harness/application/session-repository-service';
import { pullRequestOf, sessionPullRequests } from '../../../src/modules/agent-harness/application/session-pull-requests';
import { runSpecSchema, type RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { childGrants, childRunSpec } from '../../../src/modules/agent-harness/domain/child-runs';
import { engineExecutor, type PinnedEngineProfile } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessConflictError, HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  branchNameOk,
  githubRepository,
  sessionBranch,
  type SessionRepository,
} from '../../../src/modules/agent-harness/domain/repository';
import { LOOP_EXECUTOR, principalRefFor, profileExecutor, runSpecResolver, type PinnedRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  AGENT_HARNESS_AGENT_ACCESS,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_REPOSITORIES,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { agentDefinition } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { testControlPlane } from '../control-plane/support/in-memory';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const USER = 'user-1';
const URL = 'https://github.com/acme/widgets';
const INSTANCE = 'gh-1';
const REPO: SessionRepository = { url: URL, baseRef: 'main', toolsetInstanceId: INSTANCE };
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const VECTOR = join(__dirname, '../../../../../../contracts/harness/vectors/repository-pin.json');

/** A toolset store that answers for the caller's credential: `gh-1` is theirs and signed in. */
class FakeToolsets {
  asked: Array<{ orgId: string; userId: string; instanceId: string; authorization: string }> = [];
  usable = new Set([INSTANCE]);
  down = false;

  async usableGithubInstance(request: { orgId: string; userId: string; instanceId: string; authorization: string }): Promise<boolean> {
    this.asked.push(request);
    if (this.down) throw new Error('connectors down');
    return this.usable.has(request.instanceId);
  }
}

/** A workspace head per session: `repo/.git/HEAD` there means the setup phase cloned it. */
class FakeWorkspaces {
  cloned = new Set<string>();

  async sessionFiles(scope: { sessionId: string }): Promise<{ revision: number; files: Array<{ path: string; sizeBytes: number }> }> {
    return this.cloned.has(scope.sessionId)
      ? { revision: 1, files: [{ path: 'repo/.git/HEAD', sizeBytes: 21 }] }
      : { revision: 0, files: [] };
  }
}

const recordingTransaction = (changes: unknown[]) =>
  ({
    run: async (_ctx: unknown, work: (tx: unknown) => Promise<{ change: unknown }>) => {
      const writes: Array<() => Promise<void>> = [];
      const done = await work({ external: (write: () => Promise<void>) => writes.push(write) });
      for (const write of writes) await write();
      changes.push(done.change);
      return done;
    },
  }) as never;

const silenceErrors = (): (() => void) => {
  const original = (ErrorMiddleware as any).logger;
  (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
  return () => {
    (ErrorMiddleware as any).logger = original;
  };
};

describe('WP-26 GIT-FLOW: the repository rules Node and the egress proxy share', () => {
  it('RP-0: only https://github.com/owner/name is a repository; branch names follow git; the session branch is the worker\'s', () => {
    expect(githubRepository(URL)).to.equal('acme/widgets');
    for (const refused of [
      'http://github.com/acme/widgets',
      'https://gitlab.com/acme/widgets',
      'https://github.com/acme/widgets.git',
      'https://github.com/acme/widgets/tree/main',
      'https://github.com/acme',
      'https://user:pw@github.com/acme/widgets',
      'https://github.com:8443/acme/widgets',
      'https://github.com/acme/widgets?x=1',
      'https://github.com/acme/../widgets',
      'https://github.com/acme/widgets/',
      'not a url',
    ]) {
      expect(githubRepository(refused), refused).to.equal(null);
    }
    expect(['main', 'release/1.x', 'feature/a-b_c'].every(branchNameOk)).to.equal(true);
    expect(['', '-x', '/x', 'x/', 'x.lock', 'a..b', 'a//b', 'a/.b', 'HEAD', 'a b', 'a~1'].some(branchNameOk)).to.equal(false);
    // harness_core/engines/git_checkout.py session_branch: unsafe characters become '-', edges trimmed, 80 at most.
    expect(sessionBranch('ses_123')).to.equal('pipeshub/session-ses_123');
    expect(sessionBranch('a b/c')).to.equal('pipeshub/session-a-b-c');
    expect(sessionBranch('..')).to.equal('pipeshub/session-session');
    expect(sessionBranch('x'.repeat(100))).to.equal(`pipeshub/session-${'x'.repeat(80)}`);
  });
});

describe('WP-26 GIT-FLOW: GET/PUT/DELETE …/repository', () => {
  let server: Server;
  let base: string;
  let restore: () => void;
  const preferences = new InMemoryCodingEnginePreferences();
  const toolsets = new FakeToolsets();
  const workspaces = new FakeWorkspaces();
  const changes: unknown[] = [];
  const opened = new Map<string, { number: number; url: string; state: string }>();
  let engine: string | null = 'codex';
  const agentEngines: Record<string, unknown> = { 'agent-coder': { engines: ['codex'] }, 'agent-plain': undefined };

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: ORG, userId: USER };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({
      assertOwner: async (scope: { orgId: string; actorId: string; sessionId: string }) => {
        expect([scope.orgId, scope.actorId]).to.deep.equal([ORG, USER]);
        if (scope.sessionId === 'theirs') throw new HarnessNotFoundError();
      },
    });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_AGENT_ACCESS).toConstantValue({
      authorizeRun: async (request: { agentKey: string }) => ({
        ...agentDefinition(request.agentKey),
        engineProfile: agentEngines[request.agentKey],
      }),
    });
    container.bind(AGENT_HARNESS_SESSION_REPOSITORIES).toConstantValue(
      new SessionRepositoryService({
        preferences,
        transaction: recordingTransaction(changes),
        toolsets,
        workspaces: workspaces as never,
        engines: { forTurn: async () => (engine === null ? { engine: null, source: 'none' } : { engine, source: 'user' }) } as never,
        pullRequests: async (_orgId, sessionId) => opened.get(sessionId) ?? null,
      }),
    );
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    restore = silenceErrors();
    server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
  });

  after(async () => {
    restore();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  beforeEach(() => {
    engine = 'codex';
    toolsets.down = false;
    toolsets.usable = new Set([INSTANCE]);
  });

  const call = (method: string, path: string, body?: unknown) =>
    fetch(`${base}${path}`, {
      method,
      headers: { 'content-type': 'application/json', authorization: 'Bearer caller' },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  const code = async (res: Response): Promise<string> => ((await res.json()) as { error: { code: string } }).error.code;

  it('RP-1: a chat with none answers 204; the owner saves one and reads it back with its session branch', async () => {
    expect((await call('GET', '/conversations/s-1/repository')).status).to.equal(204);
    const saved = await call('PUT', '/conversations/s-1/repository', REPO);
    expect(saved.status).to.equal(200);
    const view = { url: URL, baseRef: 'main', sessionBranch: 'pipeshub/session-s-1', cloned: false };
    expect(await saved.json()).to.deep.equal(view);
    const read = await call('GET', '/conversations/s-1/repository');
    expect(read.status).to.equal(200);
    expect(await read.json()).to.deep.equal(view);
    expect(await preferences.readSessionRepository(ORG, 's-1')).to.deep.equal(REPO);
    // The toolset store is asked with the caller's own credential; org and user come from it, never the body.
    expect(toolsets.asked.at(-1)).to.deep.equal({ orgId: ORG, userId: USER, instanceId: INSTANCE, authorization: 'Bearer caller' });
  });

  it('RP-2: a save is one audited control-plane change of repository session/<id>; no base branch reads as the default', async () => {
    const before = changes.length;
    const saved = await call('PUT', '/conversations/s-2/repository', { url: URL, toolsetInstanceId: INSTANCE });
    expect(((await saved.json()) as { baseRef: unknown }).baseRef).to.equal(null);
    expect(changes.length).to.equal(before + 1);
    expect(changes.at(-1)).to.deep.include({ object: { type: 'repository', id: 'session/s-2' }, changed: ['repository'] });
  });

  it('RP-3: refusals carry their catalogue code and write nothing', async () => {
    const before = changes.length;
    for (const url of ['https://gitlab.com/acme/widgets', 'https://github.com/acme/widgets.git', 'http://github.com/acme/widgets']) {
      const res = await call('PUT', '/conversations/s-3/repository', { url, toolsetInstanceId: INSTANCE });
      expect(res.status, url).to.equal(400);
      expect(await code(res)).to.equal('HTTP_AGENT_HARNESS_REPOSITORY_NOT_GITHUB');
    }
    const missing = await call('PUT', '/conversations/s-3/repository', { url: URL, toolsetInstanceId: 'gh-someone-else' });
    expect(missing.status).to.equal(409);
    expect(await code(missing)).to.equal('HTTP_AGENT_HARNESS_REPOSITORY_TOOLSET_MISSING');
    engine = null;
    const noEngine = await call('PUT', '/conversations/s-3/repository', REPO);
    expect(noEngine.status).to.equal(409);
    expect(await code(noEngine)).to.equal('HTTP_AGENT_HARNESS_REPOSITORY_NEEDS_ENGINE');
    toolsets.down = true;
    engine = 'codex';
    expect((await call('PUT', '/conversations/s-3/repository', REPO)).status).to.equal(503);
    expect(changes.length).to.equal(before);
    expect(await preferences.readSessionRepository(ORG, 's-3')).to.equal(null);
  });

  it("RP-4: in an agent's chat the agent's own engines decide, read with the caller's credential", async () => {
    expect((await call('PUT', '/agents/agent-coder/conversations/a-1/repository', REPO)).status).to.equal(200);
    const plain = await call('PUT', '/agents/agent-plain/conversations/a-2/repository', REPO);
    expect(plain.status).to.equal(409);
    expect(await code(plain)).to.equal('HTTP_AGENT_HARNESS_REPOSITORY_NEEDS_ENGINE');
  });

  it('RP-5: once the workspace holds the clone the repository is locked: the same one answers, another or none is refused', async () => {
    expect((await call('PUT', '/conversations/s-5/repository', REPO)).status).to.equal(200);
    workspaces.cloned.add('s-5');
    const same = await call('PUT', '/conversations/s-5/repository', REPO);
    expect(same.status).to.equal(200);
    expect(((await same.json()) as { cloned: boolean }).cloned).to.equal(true);
    const other = await call('PUT', '/conversations/s-5/repository', { ...REPO, url: 'https://github.com/acme/other' });
    expect(other.status).to.equal(409);
    expect(await code(other)).to.equal('HTTP_AGENT_HARNESS_REPOSITORY_LOCKED');
    const removed = await call('DELETE', '/conversations/s-5/repository');
    expect(removed.status).to.equal(409);
    expect(await code(removed)).to.equal('HTTP_AGENT_HARNESS_REPOSITORY_LOCKED');
    expect(await preferences.readSessionRepository(ORG, 's-5')).to.deep.equal(REPO);
  });

  it('RP-5b: before the clone, DELETE clears it as one audited change; clearing none is the same answer', async () => {
    expect((await call('PUT', '/conversations/s-6/repository', REPO)).status).to.equal(200);
    const before = changes.length;
    expect((await call('DELETE', '/conversations/s-6/repository')).status).to.equal(204);
    expect(changes.length).to.equal(before + 1);
    expect((await call('GET', '/conversations/s-6/repository')).status).to.equal(204);
    expect((await call('DELETE', '/conversations/s-6/repository')).status).to.equal(204);
    expect(changes.length).to.equal(before + 1);
  });

  it("RP-6: another person's chat is a 404 before anything is read; a malformed body or an org in it is a 400", async () => {
    const asked = toolsets.asked.length;
    for (const method of ['GET', 'PUT', 'DELETE']) {
      const res = await call(method, '/conversations/theirs/repository', method === 'PUT' ? REPO : undefined);
      expect(res.status, method).to.equal(404);
    }
    expect((await call('PUT', '/conversations/s-7/repository', { ...REPO, baseRef: '../main' })).status).to.equal(400);
    expect((await call('PUT', '/conversations/s-7/repository', { ...REPO, toolsetInstanceId: 'a/b' })).status).to.equal(400);
    expect((await call('PUT', '/conversations/s-7/repository', { ...REPO, orgId: 'org-2' })).status).to.equal(400);
    expect(toolsets.asked.length).to.equal(asked);
  });

  it('RP-7: once the engine opened a pull request, GET names it; before the clone none is looked for', async () => {
    const pr = { number: 7, url: 'https://github.com/acme/widgets/pull/7', state: 'open' };
    opened.set('s-8', pr);
    expect((await call('PUT', '/conversations/s-8/repository', REPO)).status).to.equal(200);
    expect(await (await call('GET', '/conversations/s-8/repository')).json()).to.not.have.property('pullRequest');
    workspaces.cloned.add('s-8');
    expect(((await (await call('GET', '/conversations/s-8/repository')).json()) as { pullRequest: unknown }).pullRequest).to.deep.equal(pr);
  });
});

describe('WP-26 GIT-FLOW: the pull request the chat opened, from its runs\' journals', () => {
  const progress = (seq: number, kind: string, text: string, data: Record<string, unknown>) =>
    ({ type: 'x.pipeshub.engine.progress', seq, data: { tool: 'code_task', taskId: 't', kind, text, data, turnId: 'trn', seq } }) as never;
  const created = (number: number) =>
    JSON.stringify({ success: true, message: 'Pull request created', data: { number, state: 'open', html_url: `https://github.com/acme/widgets/pull/${String(number)}`, head: { ref: 'pipeshub/session-s' } } });

  it('PR-1: a created pull request is read from the result, whole or cut short; anything else is not one', () => {
    expect(pullRequestOf(created(7))).to.deep.equal({ number: 7, url: 'https://github.com/acme/widgets/pull/7', state: 'open' });
    expect(pullRequestOf(created(9).split(',"head"')[0] as string)).to.deep.equal({ number: 9, url: 'https://github.com/acme/widgets/pull/9', state: 'open' });
    expect(pullRequestOf('{"success": false, "error": "Validation Failed"}')).to.equal(null);
    expect(pullRequestOf(JSON.stringify({ data: { number: 3, html_url: 'https://evil.example/acme/widgets/pull/3' } }))).to.equal(null);
  });

  it("PR-2: the latest finished child's last completed open_pull_request wins; failures and other tools are skipped", async () => {
    const journals: Record<string, unknown[]> = {
      newest: [progress(1, 'tool', 'open_pull_request', { callId: 'c1' }), progress(2, 'tool_result', 'open_pull_request', { callId: 'c1', status: 'failed', result: created(8) })],
      middle: [
        progress(1, 'tool_result', 'github__get_issue', { callId: 'c0', status: 'completed', result: created(1) }),
        progress(2, 'tool_result', 'pipeshub__open_pull_request', { callId: 'c2', status: 'completed', result: created(5) }),
        progress(3, 'tool_result', 'open_pull_request', { callId: 'c3', status: 'completed', result: created(6) }),
      ],
      oldest: [progress(1, 'tool_result', 'open_pull_request', { callId: 'c4', status: 'completed', result: created(2) })],
    };
    const asked: unknown[] = [];
    const read = sessionPullRequests({
      runs: {
        completedRootChildren: async (orgId: string, sessionId: string, limit: number) => {
          asked.push([orgId, sessionId, limit]);
          return [{ runId: 'newest' }, { runId: 'middle' }, { runId: 'oldest' }] as never;
        },
      },
      journal: { read: async (_orgId: string, runId: string) => journals[runId] as never },
    });
    expect(await read(ORG, 's-1')).to.deep.equal({ number: 6, url: 'https://github.com/acme/widgets/pull/6', state: 'open' });
    expect(asked).to.deep.equal([[ORG, 's-1', 20]]);
    const none = sessionPullRequests({
      runs: { completedRootChildren: async () => [{ runId: 'newest' }] as never },
      journal: { read: async (_orgId: string, runId: string) => journals[runId] as never },
    });
    expect(await none(ORG, 's-1')).to.equal(null);
  });
});

describe('WP-26 GIT-FLOW: admission pins the repository on engine work', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let execution: ExecutionService;
  let preferences: InMemoryCodingEnginePreferences;
  let settings: FakeEngineCeilings;
  let toolsets: FakeToolsets;
  let workspaces: FakeWorkspaces;
  let changes: unknown[];

  const engines = () =>
    new CodingEngineService({
      ceilings: settings,
      access: testModelAccess(),
      preferences,
      transaction: testControlPlane(),
      availability: { unavailable: async () => new Set<string>() },
    });
  const repositories = () =>
    new SessionRepositoryService({
      preferences,
      transaction: recordingTransaction(changes),
      toolsets,
      workspaces: workspaces as never,
      engines: engines(),
    });

  const submit = (options: { sessionId?: string; repository?: SessionRepository; chatMode?: string } = {}) => {
    now += 1;
    return new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: { authorizeRun: async (request) => agentDefinition({ agentKey: request.agentKey }) },
      executors: profileExecutor,
      roles: { roleOf: async () => 'owner' } as never,
      engineProfiles: settings,
      modelAccess: testModelAccess(),
      codingEngine: engines(),
      repositories: repositories(),
      newId: () => `id-${String(now)}`,
      workerFeatures: { live: async () => true, metrics: { withheld: () => undefined } } as never,
    }).submit({
      orgId: ORG,
      actorId: USER,
      ...(options.sessionId !== undefined ? { sessionId: options.sessionId } : {}),
      ...(options.repository !== undefined ? { repository: options.repository } : {}),
      authorization: 'Bearer caller',
      durableRequested: true,
      idempotencyKey: `k-${String(now)}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: options.chatMode ?? 'agent' } },
    });
  };

  const admit = async (options: Parameters<typeof submit>[0] = {}): Promise<HarnessRun> => {
    const outcome = await submit(options);
    if (outcome.route !== 'durable') return expect.fail(`expected a durable run, got ${JSON.stringify(outcome)}`);
    return outcome.admission.run;
  };

  const refusedWith = async (options: Parameters<typeof submit>[0]): Promise<string> => {
    try {
      await submit(options);
    } catch (error) {
      const cause = (error as { error?: unknown }).error ?? error;
      expect(cause).to.be.instanceOf(HarnessConflictError);
      return (cause as HarnessConflictError).code;
    }
    return expect.fail('expected a refusal');
  };

  const pin = async (run: HarnessRun): Promise<PinnedRunSpec> => {
    const claimed = await execution.claim({ orgId: run.orgId, runId: run.runId, workerId: 'w', attemptId: `a-${run.runId}`, leaseDurationMs: 1_000 });
    const spec = runSpecSchema.parse(claimed.pinned?.spec);
    return { ...(claimed.pinned as PinnedRunSpec), spec };
  };

  const end = async (run: HarnessRun) => {
    const current = (await store.get(ORG, run.runId))!;
    await execution.control({
      orgId: ORG,
      runId: run.runId,
      expectedRevision: current.revision,
      action: 'cancel',
      by: principalRefFor(USER),
      commandId: `end-${run.runId}`,
      dispatchCommandId: `end-${run.runId}:dispatch`,
    });
  };

  const runCount = (): number => (store as unknown as { runs: Map<string, unknown> }).runs.size;
  const extension = (spec: RunSpec): Record<string, unknown> =>
    (spec.extensions?.['x.pipeshub'] ?? {}) as Record<string, unknown>;
  const toolIds = (spec: RunSpec): string[] => spec.grants.tools.map((grant) => grant.toolId);
  const PR_GRANT = `github@${INSTANCE}/create_pull_request`;

  /** The code_task child the worker's `code_task` asks for: the parent's tools and workspace, on its engine profile. */
  const codeTaskChild = (parent: PinnedRunSpec): PinnedRunSpec => {
    const decision = childGrants(parent.spec, { mounts: ['/workspace'] }, 1);
    if (decision.grants === null) return expect.fail('expected a child grant');
    const profile = (extension(parent.spec)['codeTask'] as { profile: PinnedEngineProfile }).profile;
    return childRunSpec(parent, decision.grants, undefined, undefined, engineExecutor(profile));
  };

  beforeEach(() => {
    now = 1_000;
    store = new InMemoryExecutionStore({ now: () => now });
    preferences = new InMemoryCodingEnginePreferences();
    settings = new FakeEngineCeilings();
    toolsets = new FakeToolsets();
    workspaces = new FakeWorkspaces();
    changes = [];
    execution = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
    });
  });

  it("RA-1: a new chat's repository is checked, pinned on the turn that hands off and saved on the chat it creates", async () => {
    await preferences.writeUser(ORG, USER, 'codex');
    const run = await admit({ repository: REPO });
    const parent = await pin(run);
    expect(extension(parent.spec)['repository']).to.deep.equal(REPO);
    expect(toolIds(parent.spec)).to.include(PR_GRANT);
    expect(await preferences.readSessionRepository(ORG, run.sessionId)).to.deep.equal(REPO);
    expect(changes).to.have.length(1);
    expect(toolsets.asked.at(-1)).to.deep.include({ authorization: 'Bearer caller', userId: USER, orgId: ORG });
  });

  it("RA-2: the code_task child inherits the pin and the pull request grant; a loop child gets neither repository nor a reason to clone", async () => {
    await preferences.writeUser(ORG, USER, 'codex');
    const parent = await pin(await admit({ repository: REPO }));
    const child = codeTaskChild(parent);
    expect(child.spec.executor?.kind).to.equal('engine');
    expect(extension(child.spec)['repository']).to.deep.equal(REPO);
    expect(toolIds(child.spec)).to.include(PR_GRANT);
    const loopChild = childRunSpec(parent, childGrants(parent.spec, {}, 1).grants!, undefined, undefined, LOOP_EXECUTOR);
    expect(extension(loopChild.spec)).to.not.have.property('repository');
    // The shared vector the Python worker's test reads is exactly what this build pins.
    if (process.env.WRITE_REPOSITORY_PIN_VECTOR === '1') {
      writeFileSync(VECTOR, `${JSON.stringify({ parent: parent.spec, child: child.spec }, null, 2)}\n`);
    }
    const vector = JSON.parse(readFileSync(VECTOR, 'utf8')) as { parent: RunSpec; child: RunSpec };
    expect({ parent: parent.spec, child: child.spec }).to.deep.equal(vector);
  });

  it("RA-3: an existing chat's turns pin its saved repository; a turn that hands nothing to an engine pins none", async () => {
    await preferences.writeUser(ORG, USER, 'codex');
    // A chat is made by its first turn, cancelled before a worker claims it so the next one starts.
    const chat = async (): Promise<string> => {
      const first = await admit();
      await end(first);
      await preferences.writeSessionRepository(ORG, first.sessionId, REPO);
      return first.sessionId;
    };
    const handsOff = await chat();
    const engineTurn = await pin(await admit({ sessionId: handsOff }));
    expect(extension(engineTurn.spec)['repository']).to.deep.equal(REPO);
    expect(toolIds(engineTurn.spec)).to.include(PR_GRANT);
    const quiet = await chat();
    await preferences.writeSession(ORG, quiet, 'none');
    const noEngine = await pin(await admit({ sessionId: quiet }));
    expect(extension(noEngine.spec)).to.not.have.property('repository');
    expect(toolIds(noEngine.spec)).to.not.include(PR_GRANT);
  });

  it('RA-4: a new chat naming a repository with no engine is refused REPOSITORY_NEEDS_ENGINE before anything is written', async () => {
    expect(await refusedWith({ repository: REPO })).to.equal('REPOSITORY_NEEDS_ENGINE');
    expect(runCount()).to.equal(0);
    expect(toolsets.asked).to.have.length(0);
  });

  it("RA-5: a new chat's repository is refused with the PUT's codes: another host, a connection the person cannot use", async () => {
    await preferences.writeUser(ORG, USER, 'codex');
    expect(await refusedWith({ repository: { ...REPO, url: 'https://gitlab.com/acme/widgets' } })).to.equal('REPOSITORY_NOT_GITHUB');
    expect(await refusedWith({ repository: { ...REPO, toolsetInstanceId: 'gh-other' } })).to.equal('REPOSITORY_TOOLSET_MISSING');
    expect(runCount()).to.equal(0);
    expect(changes).to.have.length(0);
  });
});
