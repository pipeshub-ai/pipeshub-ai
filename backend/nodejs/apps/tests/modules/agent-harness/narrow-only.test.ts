/**
 * CPS NO-1..NO-5, NO-7: what a run asks for, and the constants it is pinned with, never
 * reach past its engine profile (R2 F5). Since CTL-6 the engine run is the turn's code_task
 * child: admitted as production admits (RunAdmissionService over the in-memory store, the org
 * ceiling in its settings document), the turn claimed (ExecutionService.claim) and its engine
 * child spawned (ChildRunService); the property runs the pure resolver.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { childEngineAuthority, ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { RunSpec, runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { PinnedEngineProfile, RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessRun, RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import {
  profileExecutor,
  RUN_SPEC_BUDGETS,
  resolveRunSpec,
  runSpecResolver,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const ENGINE_AGENT = { engines: ['opencode'] };
const EFFORT_RANK: Record<string, number> = { minimal: 0, low: 1, medium: 2, high: 3 };
const DAY_MS = 24 * 3600 * 1000;

describe('CPS narrow only: effort, deadline and budgets stay inside the engine profile', () => {
  let now: number;
  let lines: Array<[string, Record<string, unknown>]>;
  let store: InMemoryExecutionStore;
  let settings: FakeEngineCeilings;
  let service: ExecutionService;

  const admit = async (
    model: Partial<RunInputSnapshot['model']> = {},
    definition: Record<string, unknown> = {},
    deadlineMs = 60_000,
  ): Promise<HarnessRun> => {
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) =>
        agentDefinition({ agentKey, engineProfile: ENGINE_AGENT, ...definition }),
      ),
      executors: profileExecutor,
      engineProfiles: settings,
      modelAccess: testModelAccess(),
      logger: {
        debug: () => undefined,
        info: (event: string, meta?: unknown) => void lines.push([event, meta as Record<string, unknown>]),
        warn: () => undefined,
        error: () => undefined,
      },
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now)}-${Math.random()}`,
      deadlineMs,
      input: {
        query: 'q',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto', modelKey: 'model-1', modelName: 'gpt-x', modelProvider: 'openAI', ...model },
      },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    return outcome.admission.run;
  };

  /** The engine run the turn hands off to: its code_task child's pinned spec. */
  const pinned = async (run: HarnessRun): Promise<RunSpec> => {
    const claimed = await service.claim({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'w',
      attemptId: `attempt-${run.runId}`,
      leaseDurationMs: 1_000,
    });
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(settings),
      engineAccess: (subject) => testModelAccess().decide(subject),
    }).spawn(
      { orgId: run.orgId, runId: run.runId, workerId: 'w', attemptId: `attempt-${run.runId}`, fence: claimed.run.fence },
      { invocationId: 't1.0', goal: 'build it', grants: {}, executor: { kind: 'engine' } },
    );
    const child = await store.readClaimSubject(run.orgId, spawned.childRunId);
    return runSpecSchema.parse(child?.pinned?.spec);
  };

  const profileOf = (spec: RunSpec): PinnedEngineProfile => {
    if (spec.executor?.kind !== 'engine' || spec.executor.engine.profile === undefined) {
      return expect.fail('expected an engine run');
    }
    return spec.executor.engine.profile as PinnedEngineProfile;
  };

  beforeEach(() => {
    now = 1_000;
    lines = [];
    store = new InMemoryExecutionStore({ now: () => now });
    settings = new FakeEngineCeilings();
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
    });
  });

  it('NO-1: a requested effort above the profile gets no engine (no code_task), naming the dimension and the layer', async () => {
    await settings.write('org-1', { reasoningEffort: 'medium' }, 'p_admin');
    const run = await admit({ reasoningEffort: 'high' });
    expect(run.requires?.features).to.not.include('code_task_tool');
    const withheld = lines.find((line) => line[0] === 'run.code_task_withheld');
    expect(withheld?.[1]).to.include({ reason: 'engine_profile_widening', dimension: 'reasoningEffort', layer: 'request' });
  });

  it('NO-2: an effort at or below the profile is pinned in the spec and the profile alike', async () => {
    await settings.write('org-1', { reasoningEffort: 'medium' }, 'p_admin');
    const spec = await pinned(await admit({ reasoningEffort: 'low' }));
    expect(spec.model.reasoningEffort).to.equal('low');
    expect(profileOf(spec).reasoningEffort).to.equal('low');
  });

  it('NO-3: an agent default above the profile is pinned at the profile, never above it', async () => {
    await settings.write('org-1', { reasoningEffort: 'medium' }, 'p_admin');
    const spec = await pinned(await admit({}, { defaultReasoningEffort: 'high' }));
    expect(spec.model.reasoningEffort).to.equal('medium');
    expect(profileOf(spec).reasoningEffort).to.equal('medium');
  });

  it("NO-4: an engine run's active time is at most the profile's wall time", async () => {
    await settings.write('org-1', { limits: { maxWallS: 900 } }, 'p_admin');
    const spec = await pinned(await admit({}, {}, DAY_MS));
    expect(spec.budgets.activeTimeS).to.equal(900);
  });

  it("NO-5: the profile's token and USD limits bound the constant budgets; without them the constants hold", async () => {
    await settings.write('org-1', { limits: { maxTokens: 50_000, maxUsd: 2 } }, 'p_admin');
    const bounded = await pinned(await admit());
    expect(bounded.budgets).to.include({ maxTokens: 50_000, maxCostUsd: 2 });

    await settings.write('org-1', {}, 'p_admin');
    now += 1;
    const unbounded = await pinned(await admit());
    expect(unbounded.budgets).to.include({
      maxTokens: RUN_SPEC_BUDGETS.maxTokens,
      maxCostUsd: RUN_SPEC_BUDGETS.maxCostUsd,
    });
  });

  it('NO-7: for any profile and request, no pinned value is wider than a layer that bounds it', () => {
    const efforts = [undefined, 'minimal', 'low', 'medium', 'high'] as const;
    const ceilings = [null, 'low', 'medium', 'high'] as const;
    const release = RELEASE_ENGINE_PROFILES[0]!;
    let cases = 0;
    for (const requested of efforts) {
      for (const fallback of efforts) {
        for (const ceiling of ceilings) {
          for (const maxWallS of [null, 60, 7200]) {
            for (const maxTokens of [null, 10, 2_000_000]) {
              for (const maxUsd of [null, 0, 1, 50]) {
                const profile: PinnedEngineProfile = {
                  ...release,
                  reasoningEffort: ceiling,
                  limits: { ...release.limits, maxWallS, maxTokens, maxUsd },
                };
                const run = {
                  orgId: 'o',
                  runId: 'r',
                  sessionId: 's',
                  actorId: 'u',
                  createdAt: 0,
                  absoluteDeadlineAt: 3_600_000,
                  input: {
                    query: 'q',
                    recordIds: [],
                    filters: {},
                    attachments: [],
                    model: { chatMode: 'auto', ...(requested ? { reasoningEffort: requested } : {}) },
                  },
                } as unknown as HarnessRun;
                const spec = resolveRunSpec(
                  {
                    run,
                    agentDefinition: agentDefinition({ defaultReasoningEffort: fallback ?? null }),
                    via: 'session',
                    engineProfile: profile,
                  },
                  DURABLE,
                );
                const effort = spec.model.reasoningEffort;
                if (effort !== undefined && ceiling !== null) {
                  expect(EFFORT_RANK[effort]).to.be.at.most(EFFORT_RANK[ceiling]!);
                }
                expect(spec.budgets.activeTimeS).to.be.at.most(Math.min(3600, maxWallS ?? Infinity));
                expect(spec.budgets.maxTokens).to.be.at.most(Math.min(RUN_SPEC_BUDGETS.maxTokens, maxTokens ?? Infinity));
                expect(spec.budgets.maxCostUsd).to.be.at.most(Math.min(RUN_SPEC_BUDGETS.maxCostUsd, maxUsd ?? Infinity));
                cases += 1;
              }
            }
          }
        }
      }
    }
    expect(cases).to.equal(5 * 5 * 4 * 3 * 3 * 4);
  });
});
