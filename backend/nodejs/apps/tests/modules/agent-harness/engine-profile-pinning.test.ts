/**
 * E4a: admission resolves the engine profile once and the claim pins it
 * (EP-18..EP-23); since CTL-6 as the turn's engine authority (`x.pipeshub.codeTask`), in memory: RunAdmissionService → InMemoryExecutionStore →
 * ExecutionService.claim with the same ExecutorResolver on both sides.
 */
import { testModelAccess } from './support/model-access';
import { expect } from 'chai';
import sinon from 'sinon';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  ExecutorResolver,
  profileExecutor,
  runSpecResolver,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { EngineProfileCeilings } from '../../../src/modules/agent-harness/ports/engine-profiles';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const ENGINE_AGENT = { engines: ['opencode'], limits: { maxSteps: 25 } };

describe('E4a: admission resolves the engine profile once; the claim pins it', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let settings: FakeEngineCeilings;
  let ceilings: EngineProfileCeilings;
  let executors: sinon.SinonSpy<Parameters<ExecutorResolver>, ReturnType<ExecutorResolver>>;
  let service: ExecutionService;
  let lines: unknown[][];
  let engineProfile: unknown;

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (...args) => void lines.push(['info', ...args]),
    warn: (...args) => void lines.push(['warn', ...args]),
    error: (...args) => void lines.push(['error', ...args]),
  };

  const admit = async () => {
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) =>
        agentDefinition({ agentKey, ...(engineProfile !== undefined ? { engineProfile } : {}) }),
      ),
      logger,
      executors,
      engineProfiles: ceilings,
      modelAccess: testModelAccess(),
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now)}-${Math.random()}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    if (outcome.route !== 'durable') expect.fail('expected a durable run');
    return outcome.admission.run;
  };

  const claim = (target: HarnessRun, worker = 'a') =>
    service.claim({
      orgId: target.orgId,
      runId: target.runId,
      workerId: worker,
      attemptId: `attempt-${worker}-${target.runId}`,
      leaseDurationMs: 1_000,
    });

  beforeEach(async () => {
    now = 1_000;
    lines = [];
    engineProfile = ENGINE_AGENT;
    store = new InMemoryExecutionStore({ now: () => now });
    settings = new FakeEngineCeilings();
    ceilings = settings;
    await settings.write('org-1', { limits: { maxSteps: 30, maxUsd: 2 }, isolationMin: 'gvisor' }, 'p_admin');
    executors = sinon.spy(profileExecutor);
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(executors),
    });
  });

  /** CTL-6: the profile is pinned as the turn's engine authority, which its code_task child narrows. */
  const authorityOf = (spec: unknown) =>
    (spec as { extensions: Record<string, { codeTask?: { profile: typeof RELEASE_ENGINE_PROFILES[number] } }> })
      .extensions['x.pipeshub']?.codeTask?.profile;

  it('EP-18: the turn runs on the loop and the claim pins the admitted profile as its engine authority', async () => {
    const claimed = await claim(await admit());
    const spec = runSpecSchema.parse(claimed.pinned?.spec);
    expect(spec.executor).to.deep.equal({ kind: 'loop' });
    const expected = {
      ...RELEASE_ENGINE_PROFILES[0]!,
      isolationMin: 'gvisor',
      limits: { ...RELEASE_ENGINE_PROFILES[0]!.limits, maxSteps: 25, maxUsd: 2 },
    };
    // Narrowed to an account kind only when the child spawns (OI-CPD3-1 moves to the spawn with CTL-6).
    expect(authorityOf(spec)).to.deep.equal(expected);
    expect(canonicalDigest(authorityOf(spec))).to.equal(canonicalDigest(expected));
  });

  it('EP-19: a ceiling changed after admission reaches the next run only', async () => {
    const first = await admit();
    await settings.write('org-1', { limits: { maxSteps: 10 } }, 'p_admin');
    engineProfile = { engines: ['opencode'] };
    const second = await admit();
    const pinnedFirst = authorityOf((await claim(first, 'a')).pinned?.spec);
    const pinnedSecond = authorityOf((await claim(second, 'b')).pinned?.spec);
    expect(pinnedFirst?.limits.maxSteps).to.equal(25);
    expect(pinnedFirst?.isolationMin).to.equal('gvisor');
    expect(pinnedSecond?.limits.maxSteps).to.equal(10);
    expect(pinnedSecond?.isolationMin).to.equal('gvisor');
    expect(canonicalDigest(pinnedFirst)).to.not.equal(canonicalDigest(pinnedSecond));
  });

  it('EP-20: an agent that widens the org ceiling gets no code_task, named and logged once; the turn runs', async () => {
    engineProfile = { limits: { maxSteps: 40 } };
    const run = await admit();
    expect(authorityOf((await claim(run)).pinned?.spec)).to.equal(undefined);
    const withheld = lines.filter((line) => line[1] === 'run.code_task_withheld');
    expect(withheld).to.have.length(1);
    expect(withheld[0]?.[2]).to.include({
      reason: 'engine_profile_widening',
      dimension: 'limits.maxSteps',
      layer: 'agent',
    });
    expect(JSON.stringify(lines)).to.not.include('maxSteps":40');
  });

  it('EP-20: an agent profile that is not a narrowing gets no code_task, never a guessed one', async () => {
    engineProfile = { invalid: true };
    const run = await admit();
    expect(authorityOf((await claim(run)).pinned?.spec)).to.equal(undefined);
    expect(lines.find((line) => line[1] === 'run.code_task_withheld')?.[2]).to.include({
      reason: 'engine_profile_invalid',
      layer: 'agent',
    });
  });

  it('EP-21: an agent without an engine profile runs on the loop and no ceiling is read', async () => {
    engineProfile = undefined;
    const read = sinon.spy(settings, 'read');
    const claimed = await claim(await admit());
    expect(claimed.pinned?.spec.executor).to.deep.equal({ kind: 'loop' });
    expect(read.callCount).to.equal(0);
  });

  it('EP-22: a ceiling that cannot be read gives no code_task, never a profile on defaults', async () => {
    ceilings = { read: () => Promise.reject(new Error('kv down')) };
    const run = await admit();
    expect(authorityOf((await claim(run)).pinned?.spec)).to.equal(undefined);
    expect(lines.find((line) => line[1] === 'run.code_task_withheld')?.[2]).to.include({ reason: 'unavailable' });
  });

  it('EP-23: admission and the claim ask the same resolver with the same profile', async () => {
    const claimed = await claim(await admit());
    expect(executors.callCount).to.equal(2);
    const [atAdmission, atClaim] = executors.getCalls().map((call) => call.args[0]);
    expect(atAdmission?.engineProfile).to.deep.equal(atClaim?.engineProfile);
    expect(executors.getCall(0).returnValue).to.deep.equal(claimed.pinned?.spec.executor);
  });

  it('EP-23: composition passes the one resolver to admission and to ExecutionService', () => {
    const root = join(__dirname, '../../../src/modules/agent-harness');
    const composition = [
      join(root, 'composition.ts'),
      ...readdirSync(join(root, 'binders')).map((name) => join(root, 'binders', name)),
    ]
      .map((file) => readFileSync(file, 'utf8'))
      .join('\n');
    expect(composition).to.include('executors: profileExecutor');
    expect(composition).to.include('runSpecResolver(profileExecutor)');
    expect(composition).to.not.match(/loopExecutor/);
  });
});
