/**
 * SIGNGAP: a server whose workers cannot carry a person's own vendor sign-in
 * (no live worker advertises `vendor_signin`: the sign-in add-on, the
 * egress proxy's tunnels, is off) offers none. Before this, such a
 * server still offered the Claude sign-in, an admin allowed it, a person
 * turned it on, and every Claude Code task of theirs failed inside the engine
 * with `signin_unavailable`.
 *
 * SG-1 model access: a person who turned it on earlier runs on the org's
 * model access, with the notice why; SG-2 the person's and the admin's views
 * say so and saving it on is refused (ENGINE_SIGNIN_UNAVAILABLE), turning it
 * off never is; SG-3 the coding-engine view carries it; SG-4 a code_task
 * child of such a person runs on org access and the chat is told once, and
 * with the add-on its own-plan child requires a worker that carries it;
 * SG-5 the routes. With the add-on nothing changes.
 */
import { expect } from 'chai';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import {
  InMemoryEngineSignIns,
  InMemorySignInRelay,
} from '../../../src/modules/agent-harness/adapters/in-memory-engine-signins';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { childEngineAuthority, ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { EngineSignInService } from '../../../src/modules/agent-harness/application/engine-signin-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { ModelAccessService } from '../../../src/modules/agent-harness/application/model-access-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { AGENT_HARNESS_ENGINE_SIGNINS } from '../../../src/modules/agent-harness/composition';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { KNOWN_WORKER_FEATURES } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import { ERROR_CODES } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/errors';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { planAccountId } from '../../../src/modules/agent-harness/domain/engine-signin';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import type { PinnedEngineProfile } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessModelAccessRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun, runNoticeEvent } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion } from '../../../src/modules/agent-harness/projection/transcript';
import { journalOnlySteps, project } from '../../contracts/support/durable';
import { VENDOR_SIGNIN_FEATURE } from '../../../src/modules/agent-harness/domain/worker-compat';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { Harness, startHarness } from './support/model-accounts';
import { HARNESS_ENABLED } from './support/rollout';
import { auditedControlPlane, testControlPlane } from '../control-plane/support/in-memory';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const USER = 'user-1';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

const profileOf = (kind: string): PinnedEngineProfile => {
  const found = RELEASE_ENGINE_PROFILES.find((p) => p.engine.kind === kind);
  if (found === undefined) throw new Error(`no release profile for ${kind}`);
  return found;
};

async function refusal(promise: Promise<unknown>): Promise<Error> {
  try {
    await promise;
  } catch (error) {
    return error as Error;
  }
  throw new Error('expected a refusal');
}

const code = (error: Error): string =>
  error instanceof HarnessModelAccessRefusedError ? error.code : error.name;

describe('SIGNGAP: the worker capability and the catalogue', () => {
  it('vendor_signin is a worker feature Node knows, and ENGINE_SIGNIN_UNAVAILABLE a model-access code', () => {
    expect(VENDOR_SIGNIN_FEATURE).to.equal('vendor_signin');
    expect(KNOWN_WORKER_FEATURES).to.include('vendor_signin');
    expect(ERROR_CODES.ENGINE_SIGNIN_UNAVAILABLE).to.deep.include({ status: 409, group: 'model_access', retryable: false });
  });
});

describe('SIGNGAP SG-1: model access without a worker that carries sign-ins', () => {
  let carried: boolean;
  let asked: number;
  let access: ModelAccessService;

  const subject = (kind = 'claude_code') => ({
    orgId: ORG,
    actorId: USER,
    definition: null,
    model: { chatMode: 'auto' as const },
    engineProfile: profileOf(kind),
    signIn: () => {
      asked += 1;
      return Promise.resolve({ via: 'session', surface: 'web', shared: false });
    },
  });

  beforeEach(() => {
    carried = false;
    asked = 0;
    access = testModelAccess({
      signIns: () => ({ usable: () => Promise.resolve({ enabled: true, allowed: true }) }),
      signInsCarried: () => Promise.resolve(carried),
    });
  });

  it('SG-1a: a person who turned it on runs on the org\'s model access, and the decision says why', async () => {
    const decided = await access.decide(subject());
    expect(decided?.access.access.route).to.equal('gateway');
    expect(decided?.access.access.accountKind).to.equal('org_api_key');
    expect(decided?.engineProfile.accountKinds).to.deep.equal(['org_api_key']);
    expect(decided?.notice).to.equal('signin_unavailable');
    expect(asked, 'the turn\'s origin is not read for a plan that cannot run').to.equal(0);
  });

  it('SG-1b: the org switch off is not a refusal either: the option does not exist on this server', async () => {
    access = testModelAccess({
      signIns: () => ({ usable: () => Promise.resolve({ enabled: true, allowed: false }) }),
      signInsCarried: () => Promise.resolve(false),
    });
    const decided = await access.decide(subject());
    expect(decided?.access.access.route).to.equal('gateway');
    expect(decided?.notice).to.equal('signin_unavailable');
  });

  it('SG-1c: with the add-on nothing changes: the plan is pinned, no notice', async () => {
    carried = true;
    const decided = await access.decide(subject());
    expect(decided?.access.access).to.deep.include({ route: 'vendor_sandbox_login', accountId: planAccountId(USER) });
    expect(decided?.notice).to.equal(undefined);
    expect(asked).to.equal(1);
  });

  it('SG-1d: someone who never turned it on, or another engine, gets no notice', async () => {
    access = testModelAccess({
      signIns: () => ({ usable: () => Promise.resolve({ enabled: false, allowed: true }) }),
      signInsCarried: () => Promise.resolve(false),
    });
    expect((await access.decide(subject()))?.notice).to.equal(undefined);
    expect((await testModelAccess({
      signIns: () => ({ usable: () => Promise.resolve({ enabled: true, allowed: true }) }),
      signInsCarried: () => Promise.resolve(false),
    }).decide(subject('codex')))?.notice).to.equal(undefined);
  });
});

describe('SIGNGAP SG-2: the person\'s view, and saving', () => {
  let carried: boolean;
  let store: InMemoryEngineSignIns;
  let service: EngineSignInService;

  beforeEach(async () => {
    carried = true;
    store = new InMemoryEngineSignIns();
    store.addUser(ORG, USER);
    service = new EngineSignInService({
      store,
      relay: new InMemorySignInRelay(() => null),
      access: testModelAccess({ signInsCarried: () => Promise.resolve(carried) }),
      transaction: auditedControlPlane().transaction,
    });
    await service.setEnabled(ORG, USER, true);
    carried = false;
  });

  it('SG-2a: the person sees it unavailable on this server, with the reason', async () => {
    const view = await service.view(ORG, USER);
    expect(view.claude).to.deep.include({ state: 'not_allowed', enabled: false, reason: 'engine_signin_unavailable' });
  });

  it('SG-2b: turning it on is refused with the catalogue code; turning it off never is', async () => {
    expect(code(await refusal(service.setEnabled(ORG, USER, true)))).to.equal('engine_signin_unavailable');
    expect((await service.setEnabled(ORG, USER, false)).claude.reason).to.equal('engine_signin_unavailable');
  });

  it('SG-2c: once a worker carries it, the person\'s view is available again', async () => {
    await service.setEnabled(ORG, USER, true).catch(() => undefined);
    carried = true;
    expect((await service.view(ORG, USER)).claude).to.deep.include({ state: 'available' });
  });
});

describe('SIGNGAP SG-3: the coding-engine view', () => {
  it('carries vendorSignIn from the live workers', async () => {
    const settings = new FakeEngineCeilings();
    for (const carried of [false, true]) {
      const view = await new CodingEngineService({
        ceilings: settings,
        access: testModelAccess({ signInsCarried: () => Promise.resolve(carried) }),
        preferences: new InMemoryCodingEnginePreferences(),
        transaction: testControlPlane(),
      }).view(ORG, USER);
      expect(view.vendorSignIn).to.equal(carried);
    }
  });
});

describe('SIGNGAP SG-4: a Claude Code code_task child of a person who turned their account on', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let settings: FakeEngineCeilings;
  let service: ExecutionService;
  let payloads: InMemoryPayloadStore;
  let lines: Array<[string, unknown]>;

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (event: string, meta?: unknown) => void lines.push([event, meta]),
    warn: (event: string, meta?: unknown) => void lines.push([event, meta]),
    error: (event: string, meta?: unknown) => void lines.push([event, meta]),
  };

  const signedInAccess = (carried: boolean) =>
    testModelAccess({
      signIns: () => ({ usable: () => Promise.resolve({ enabled: true, allowed: true }) }),
      signInsCarried: () => Promise.resolve(carried),
    });

  const admit = async (): Promise<HarnessRun> => {
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) =>
        agentDefinition({ agentKey, engineProfile: { engines: ['claude_code'], limits: { maxSteps: 25 } } }),
      ),
      logger,
      executors: profileExecutor,
      engineProfiles: settings,
      modelAccess: testModelAccess(),
      workerFeatures: {
        live: async () => true,
        metrics: { withheld: () => undefined },
      } as never,
    }).submit({
      orgId: ORG,
      actorId: USER,
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now)}`,
      deadlineMs: 60_000,
      input: {
        query: 'write the file',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto', modelKey: 'model-2', modelName: 'claude-x', modelProvider: 'anthropic' },
      },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    return outcome.admission.run;
  };

  const spawn = async (carried: boolean) => {
    const run = await admit();
    const claimed = await service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'w',
      attemptId: 'a-1',
      leaseDurationMs: 60_000,
    });
    const journals = new JournalService(payloads, testCipher());
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals,
      workspaces: null,
      logger,
      engineAuthority: childEngineAuthority(settings),
      sessionShared: () => Promise.resolve(false),
      engineAccess: (subject) => signedInAccess(carried).decide(subject),
    }).spawn(
      { orgId: ORG, runId: run.runId, workerId: 'w', attemptId: 'a-1', fence: claimed.run.fence },
      { invocationId: 't1.0', goal: 'write the travel policy file', grants: {}, executor: { kind: 'engine' } },
    );
    const child = await store.readClaimSubject(ORG, spawned.childRunId);
    const notices = (await store.journal.readStream(ORG, run.sessionId, 0, 1_000)).filter(
      (event) => event.runId === run.runId && event.type === 'x.pipeshub.run.notice',
    );
    return { run, child, spec: runSpecSchema.parse(child?.pinned?.spec), notices };
  };

  beforeEach(async () => {
    now = 1_000;
    lines = [];
    payloads = new InMemoryPayloadStore();
    store = new InMemoryExecutionStore({ now: () => now });
    settings = new FakeEngineCeilings();
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
    });
  });

  it('SG-4a: without the add-on the child runs on the org\'s model access and the chat is told once', async () => {
    const { child, spec, notices } = await spawn(false);
    if (spec.executor?.kind !== 'engine') return expect.fail('expected an engine child');
    expect(spec.executor.engine.kind).to.equal('claude_code');
    const access = spec.model.policy[0]?.access;
    expect(access?.route).to.equal('gateway');
    expect(access?.accountKind).to.not.equal('user_plan_sandbox_login');
    expect(child?.run.requires?.features).to.not.include(VENDOR_SIGNIN_FEATURE);
    expect(notices.map((event) => (event.data as { code?: string }).code)).to.deep.equal(['signin_unavailable']);
    expect(lines.find(([event]) => event === 'child.signin_unavailable')?.[1]).to.include({ engine: 'claude_code' });
  });

  it('SG-4b: with the add-on the child is pinned on the person\'s plan, needs a worker that carries it, no notice', async () => {
    const { child, spec, notices } = await spawn(true);
    expect(spec.model.policy[0]?.access?.route).to.equal('vendor_sandbox_login');
    expect(child?.run.requires?.features).to.include(VENDOR_SIGNIN_FEATURE);
    expect(notices).to.deep.equal([]);
  });
});

describe('SIGNGAP SG-5: the routes', () => {
  const ME = '/api/v1/agent-harness/engine-accounts/me';
  let h: Harness;
  let carried: boolean;

  beforeEach(async () => {
    h = await startHarness();
    carried = true;
    const store = new InMemoryEngineSignIns();
    for (const user of ['u1', 'admin-1']) store.addUser('org-1', user);
    h.container.bind(AGENT_HARNESS_ENGINE_SIGNINS).toConstantValue(
      new EngineSignInService({
        store,
        access: testModelAccess({ signInsCarried: () => Promise.resolve(carried) }),
        transaction: h.audit.transaction,
      }),
    );
  });
  afterEach(() => h.close());

  it('SG-5a: without the add-on the save is refused 409 with the catalogue code; the read says why', async () => {
    carried = false;
    h.setCaller({ userId: 'u1', orgId: 'org-1' });
    const mine = await h.call('PUT', `${ME}/claude`, { enabled: true });
    expect(mine.status, mine.text).to.equal(409);
    expect(mine.text).to.contain('AGENT_HARNESS_ENGINE_SIGNIN_UNAVAILABLE');
    expect((await h.call('GET', ME)).json.claude).to.deep.include({ state: 'not_allowed', reason: 'engine_signin_unavailable' });
  });

  it('SG-5b: with the add-on nothing changes', async () => {
    h.setCaller({ userId: 'u1', orgId: 'org-1' });
    expect((await h.call('PUT', `${ME}/claude`, { enabled: true })).status).to.equal(200);
  });
});

describe('SIGNGAP SG-6: the chat shows the notice Node writes beside child.spawned, live and saved', () => {
  const RUN = 'run-parent';
  const sha = (c: string): string => `sha256:${c.repeat(64)}`;
  const inline = (value: unknown) => inlineContentRef(canonicalJsonBytes(value));

  const events = (): JournalEvent[] => {
    const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
      ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
      ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
      ['turn.started', { turn: 1 }, 'worker'],
      [
        'model.response',
        {
          messageRef: inline({ text: '', toolCalls: [{ id: 'call-1', name: 'code_task', args: { goal: 'Write it' } }] }),
          toolCalls: [{ callId: 'call-1', modelName: 'code_task', argsDigest: sha('c') }],
          usage: { inputTokens: 1, outputTokens: 1 },
          provider: 'p',
          transport: 't',
        },
        'worker',
      ],
      ['child.spawned', { childRunId: 'run-child', invocationId: 't1.0', taskId: 'policy', grantDigest: sha('d') }, 'node'],
      ['x.pipeshub.run.notice', { code: 'signin_unavailable' }, 'node'],
      ['child.completed', { childRunId: 'run-child', resultRef: { ref: 'inline:e30=', hash: sha('e'), size: 2 }, labels: { integrity: 'internal' }, outcome: 'answered' }, 'node'],
      ['x.pipeshub.answer.finalized', { outcome: 'answered', answer: 'Done.', partTexts: ['Done.'], citations: [] }, 'worker'],
      ['run.completed', { outcome: 'answered' }, 'node'],
    ];
    return specs.map(
      ([type, data, writer], index) =>
        ({
          orgId: 'org-1',
          sessionId: 'ses-1',
          streamId: 'ses-1',
          seq: index + 1,
          runId: RUN,
          attemptId: writer === 'worker' ? `${RUN}.a1` : undefined,
          fence: writer === 'worker' ? 1 : undefined,
          type,
          schemaVersion: 1,
          critical: !type.startsWith('x.'),
          ts: '2026-10-20T12:00:00.000Z',
          writer,
          visibility: 'owner',
          data,
        }) as JournalEvent,
    );
  };

  it('Node may journal it on the parent', () => {
    expect(() =>
      validateJournalAppend({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        runId: RUN,
        writer: 'node',
        events: [runNoticeEvent('signin_unavailable')],
      } as never),
    ).to.not.throw();
  });

  it('the answer carries it once, saved and live', () => {
    const journalled = events();
    const parts = (buildCompletion(foldTimeline(journalled, RUN)) as { parts: Array<Record<string, unknown>> }).parts;
    expect(parts.filter((p) => p['type'] === 'notice')).to.deep.equal([{ type: 'notice', code: 'signin_unavailable', runId: RUN }]);
    const replay = { scenario: 'signgap', query: 'q', runId: RUN, sessionId: 'ses-1', events: journalled, live: [] };
    const frames = project(replay, journalOnlySteps(replay)).map((f) => f.frame);
    expect(frames.filter((f) => f['name'] === 'notice').map((f) => f['value'])).to.deep.equal([{ code: 'signin_unavailable' }]);
  });
});
