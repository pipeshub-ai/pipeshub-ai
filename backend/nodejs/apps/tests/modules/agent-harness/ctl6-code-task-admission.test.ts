/**
 * CTL-6: a coder agent's turn runs on the loop. Admission keeps the agent's engine profile as the run's engine
 * authority, pinned on the spec as `x.pipeshub.codeTask`, from which the loop's `code_task` tool spawns an engine
 * child, while a live worker runs engines; the run requires `code_task_tool` so only a worker that offers the tool
 * claims it. In memory:
 * RunAdmissionService → InMemoryExecutionStore → ExecutionService.claim.
 */
import { testModelAccess } from './support/model-access';
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { childEngineAuthority, ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { profileExecutor, resolveRunSpec, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { EngineProfileCeilings } from '../../../src/modules/agent-harness/ports/engine-profiles';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const ENGINE_AGENT = { engines: ['opencode'], limits: { maxSteps: 25 } };
const CODE_TASK = 'code_task_tool';

const codeTaskOf = (spec: unknown): { profile?: { engine?: { kind?: string } } } | undefined =>
  ((spec as { extensions?: Record<string, Record<string, unknown>> }).extensions?.['x.pipeshub']?.['codeTask'] as
    | { profile?: { engine?: { kind?: string } } }
    | undefined);

describe('CTL-6: a coder agent turn runs on the loop with code_task', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let settings: FakeEngineCeilings;
  let ceilings: EngineProfileCeilings;
  let service: ExecutionService;
  let engineProfile: unknown;
  let live: Set<string>;
  let withheld: string[];
  let lines: Array<[string, unknown]>;

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (event: string, meta?: unknown) => void lines.push([event, meta]),
    warn: (event: string, meta?: unknown) => void lines.push([event, meta]),
    error: (event: string, meta?: unknown) => void lines.push([event, meta]),
  };

  let inputArtifacts: Array<{ artifactId: string; version?: number }> | undefined;

  const admit = async (): Promise<HarnessRun> => {
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) =>
        agentDefinition({ agentKey, ...(engineProfile !== undefined ? { engineProfile } : {}) }),
      ),
      logger,
      executors: profileExecutor,
      engineProfiles: ceilings,
      modelAccess: testModelAccess(),
      workerFeatures: {
        live: async (feature: string) => live.has(feature),
        metrics: { withheld: (feature: string) => void withheld.push(feature) },
      } as never,
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now)}-${Math.random()}`,
      deadlineMs: 60_000,
      input: {
        query: 'q',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto', modelKey: 'model-1', modelName: 'gpt-x', modelProvider: 'openAI' },
        ...(inputArtifacts !== undefined ? { inputArtifacts } : {}),
      },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    return outcome.admission.run;
  };

  const claim = async (target: HarnessRun, worker = 'a') => {
    const claimed = await service.claim({
      orgId: target.orgId,
      runId: target.runId,
      workerId: worker,
      attemptId: `attempt-${worker}-${target.runId}`,
      leaseDurationMs: 1_000,
    });
    return runSpecSchema.parse(claimed.pinned?.spec);
  };

  beforeEach(async () => {
    now = 1_000;
    lines = [];
    withheld = [];
    inputArtifacts = undefined;
    live = new Set([CODE_TASK, 'executor_engine', 'agent_code']);
    engineProfile = ENGINE_AGENT;
    store = new InMemoryExecutionStore({ now: () => now });
    settings = new FakeEngineCeilings();
    ceilings = settings;
    await settings.write('org-1', { limits: { maxSteps: 30 } }, 'p_admin');
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
    });
  });

  it('C6-1/C6-2: the turn is a loop run that pins the engine authority as codeTask and requires the tool', async () => {
    const run = await admit();
    expect(run.requires?.features).to.include(CODE_TASK);
    expect(run.requires?.features).to.not.include('executor_engine');
    const spec = await claim(run);
    expect(spec.executor).to.deep.equal({ kind: 'loop' });
    expect(codeTaskOf(spec)?.profile?.engine?.kind).to.equal('opencode');
    expect(codeTaskOf(spec)).to.have.nested.property('profile.limits.maxSteps', 25);
    // The handoff is a child run on a fork of the session workspace: the loop may spawn one and grant the mount.
    expect(spec.budgets.maxChildren).to.be.at.least(1);
    expect((spec.extensions?.['x.pipeshub'] as { mounts?: string[] }).mounts).to.deep.equal(['/workspace']);
  });

  it('C6-3: the pin never re-resolves: the same spec on a second claim, a pre-CTL-6 engine run stays engine', async () => {
    const run = await admit();
    const first = await claim(run, 'a');
    now += 5_000;
    const again = await claim(run, 'b');
    expect(again.executor).to.deep.equal(first.executor);
    expect(codeTaskOf(again)).to.deep.equal(codeTaskOf(first));
    // A run admitted before CTL-6: its stored profile, and no code_task_tool requirement.
    const subject = {
      run: { ...run, requires: { ...run.requires!, features: run.requires!.features.filter((f) => f !== CODE_TASK) } },
      agentDefinition: agentDefinition({ agentKey: 'agent-1', engineProfile: ENGINE_AGENT }),
      engineProfile: codeTaskOf(first)!.profile as never,
      via: 'session',
    };
    const legacy = resolveRunSpec(subject as never, DURABLE);
    expect(legacy.executor?.kind).to.equal('engine');
    expect(codeTaskOf(legacy)).to.equal(undefined);
  });

  describe('C6-5: no engine authority, no code_task, and the agent still answers on the loop', () => {
    it('a loop agent', async () => {
      engineProfile = undefined;
      const run = await admit();
      expect(run.requires?.features).to.not.include(CODE_TASK);
      expect(codeTaskOf(await claim(run))).to.equal(undefined);
    });

    it('an org ceiling that admits no engine the agent names: withheld, not refused', async () => {
      await settings.write('org-1', { engines: ['codex'] }, 'p_admin');
      const run = await admit();
      const spec = await claim(run);
      expect(spec.executor).to.deep.equal({ kind: 'loop' });
      expect(codeTaskOf(spec)).to.equal(undefined);
      // WP-111: the admin's switch reads as the engine being off, not as a profile asking for too much.
      expect(lines.find(([event]) => event === 'run.code_task_withheld')?.[1]).to.include({
        reason: 'engine_off',
      });
    });

    it('a ceiling that cannot be read: withheld, not refused', async () => {
      ceilings = { read: () => Promise.reject(new Error('kv down')) };
      const spec = await claim(await admit());
      expect(codeTaskOf(spec)).to.equal(undefined);
      expect(lines.find(([event]) => event === 'run.code_task_withheld')?.[1]).to.include({ reason: 'unavailable' });
    });

    it('no live worker runs engines (HARNESS_ENGINE_RUNS off everywhere): withheld and counted', async () => {
      live.delete('executor_engine');
      const run = await admit();
      expect(run.requires?.features).to.not.include(CODE_TASK);
      expect(codeTaskOf(await claim(run))).to.equal(undefined);
      expect(withheld).to.include(CODE_TASK);
    });
  });

  it('C6-6: a ceiling dropped between turns reaches the next turn only', async () => {
    const first = await admit();
    await settings.write('org-1', { engines: ['codex'] }, 'p_admin');
    const second = await admit();
    expect(codeTaskOf(await claim(first, 'a'))?.profile?.engine?.kind).to.equal('opencode');
    expect(codeTaskOf(await claim(second, 'b'))).to.equal(undefined);
  });

  it('C6-3/C6-6: the code_task child is pinned from the parent\'s pinned authority, not a ceiling read later', async () => {
    const run = await admit();
    const claimed = await service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'w',
      attemptId: 'a-1',
      leaseDurationMs: 60_000,
    });
    await settings.write('org-1', { limits: { maxSteps: 5 } }, 'p_admin');
    const payloads = new InMemoryPayloadStore();
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(payloads, testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(settings),
      engineAccess: (subject) => testModelAccess().decide(subject),
    }).spawn(
      { orgId: 'org-1', runId: run.runId, workerId: 'w', attemptId: 'a-1', fence: claimed.run.fence },
      { invocationId: 't1.0', goal: 'write the travel policy file', grants: {}, executor: { kind: 'engine' } },
    );
    const child = await store.readClaimSubject('org-1', spawned.childRunId);
    const spec = runSpecSchema.parse(child?.pinned?.spec);
    if (spec.executor?.kind !== 'engine') return expect.fail('expected an engine child');
    expect(spec.executor.engine.kind).to.equal('opencode');
    expect(spec.executor.engine.profile?.limits.maxSteps).to.equal(25);
  });

  const spawnEngineChild = async (run: HarnessRun, executor: boolean) => {
    const claimed = await service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'w',
      attemptId: 'a-1',
      leaseDurationMs: 60_000,
    });
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(settings),
      engineAccess: (subject) => testModelAccess().decide(subject),
    }).spawn(
      { orgId: 'org-1', runId: run.runId, workerId: 'w', attemptId: 'a-1', fence: claimed.run.fence },
      {
        invocationId: 't1.0',
        goal: 'summarise the attached sheet',
        grants: {},
        ...(executor ? { executor: { kind: 'engine' as const } } : {}),
      },
    );
    return store.get('org-1', spawned.childRunId);
  };

  it('input artifacts: the turn is a loop run, and its code_task child gets exactly the turn\'s artifacts', async () => {
    inputArtifacts = [{ artifactId: 'art-sheet', version: 2 }];
    const run = await admit();
    expect(run.requires?.features).to.include(CODE_TASK);
    // The loop run lists them (code_task_tool); an engine worker is needed only by the child.
    expect(run.requires?.features).to.not.include('engine_inputs');
    const child = await spawnEngineChild(run, true);
    expect(child?.input?.inputArtifacts).to.deep.equal([{ artifactId: 'art-sheet', version: 2 }]);
    expect(child?.requires?.features).to.include('engine_inputs');
  });

  it('input artifacts reach no loop child', async () => {
    inputArtifacts = [{ artifactId: 'art-sheet', version: 2 }];
    const run = await admit();
    const child = await spawnEngineChild(run, false);
    expect(child?.input?.inputArtifacts).to.equal(undefined);
  });

  it('SL-14 (C2.07, C2.20): with release_pin live, the turn and its engine child pin the contract, catalog and image', async () => {
    live.add('release_pin');
    const image = `sha256:${'3'.repeat(64)}`;
    const run = await admit();
    expect(run.requires?.features).to.include('release_pin');
    const claimed = await service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'w',
      attemptId: 'a-1',
      leaseDurationMs: 60_000,
    });
    const parentSpec = runSpecSchema.parse(claimed.pinned?.spec);
    const parentRelease = (parentSpec.extensions?.['x.pipeshub'] as { release?: Record<string, unknown> }).release;
    expect(parentRelease).to.include({ pipeshubContract: 1 });
    // The loop turn runs no engine image.
    expect(parentSpec.pins).to.equal(undefined);
    let asked = 0;
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(settings),
      engineAccess: (subject) => testModelAccess().decide(subject),
      engineImages: {
        imageDigests: async () => {
          asked += 1;
          return new Map([['opencode@1.18.34', image]]);
        },
      },
    }).spawn(
      { orgId: 'org-1', runId: run.runId, workerId: 'w', attemptId: 'a-1', fence: claimed.run.fence },
      { invocationId: 't1.0', goal: 'write the travel policy file', grants: {}, executor: { kind: 'engine' } },
    );
    const child = await store.readClaimSubject('org-1', spawned.childRunId);
    const spec = runSpecSchema.parse(child?.pinned?.spec);
    expect(asked).to.equal(1);
    expect(spec.pins).to.deep.equal({ sandboxImage: image });
    expect((spec.extensions?.['x.pipeshub'] as { release?: Record<string, unknown> }).release).to.include({
      pipeshubContract: 1,
    });
    expect(child?.run.requires?.features).to.include('release_pin');
  });
});
