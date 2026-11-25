/**
 * An install without the sandbox broker (an upgrade kept on SANDBOX_MODE=docker, Helm's docker mode) or with engine
 * runs off answers no broker health, so that silence alone proves nothing. Live workers do: once a loop worker is
 * live and none advertises `executor_engine`, the picker greys every engine `ENGINE_SANDBOX_MISSING`, a save is
 * refused with it, and admission withholds `code_task` with `engine_sandbox_missing` while the turn still runs on the
 * loop. Before any worker is seen nothing is decided against engines (ADR-CAP-01 D2).
 */
import { expect } from 'chai';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import type { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { engineExecutorAbsent } from '../../../src/modules/agent-harness/application/worker-feature-availability';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import type { EngineAvailability } from '../../../src/modules/agent-harness/ports/engine-availability';
import { testControlPlane } from '../control-plane/support/in-memory';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

/** A deployment with no broker: its health cannot be asked. */
const NO_BROKER: EngineAvailability = {
  unavailable: async () => {
    throw new Error('getaddrinfo ENOTFOUND sandbox-broker');
  },
  sandboxMissing: async () => {
    throw new Error('getaddrinfo ENOTFOUND sandbox-broker');
  },
};

const LOOP_ONLY = new Set(['chat', 'code_task_tool']);
const WITH_ENGINES = new Set(['chat', 'code_task_tool', 'executor_engine']);
const NONE_SEEN = new Set<string>();

const workers = (live: ReadonlySet<string>) => ({
  live: async (feature: string) => live.has(feature),
  metrics: { withheld: () => undefined },
});

const picker = (live: ReadonlySet<string>): CodingEngineService =>
  new CodingEngineService({
    ceilings: new FakeEngineCeilings(),
    access: {
      engineRefusals: async () => new Map(),
      signInsCarried: async () => true,
    },
    preferences: new InMemoryCodingEnginePreferences(),
    transaction: testControlPlane(),
    availability: NO_BROKER,
    engineWorkers: workers(live),
  });

async function admit(
  live: ReadonlySet<string>,
): Promise<{ features: readonly string[]; withheld: string }> {
  const lines: unknown[][] = [];
  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (...args) => void lines.push(args),
    warn: (...args) => void lines.push(args),
    error: (...args) => void lines.push(args),
  };
  const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
  const outcome = await new RunAdmissionService({
    store: new InMemoryExecutionStore({ now: () => 1_000 }),
    rollout: {
      decide: async () => V2,
      decideForOrg: async () => ({
        ...V2,
        harnessOn: true,
        resumeInFlightRuns: true,
      }),
    },
    clock: { now: () => 1_000 },
    agentAccess: grantingAccess((agentKey) =>
      agentDefinition({ agentKey, engineProfile: { engines: ['opencode'] } }),
    ),
    logger,
    engineProfiles: new FakeEngineCeilings(),
    modelAccess: testModelAccess(),
    engineAvailability: NO_BROKER,
    workerFeatures: workers(live) as never,
  }).submit({
    orgId: 'org-1',
    actorId: 'user-1',
    agentKey: 'agent-1',
    authorization: 'Bearer t',
    idempotencyKey: `k-${[...live].join('-') || 'none'}`,
    deadlineMs: 60_000,
    input: {
      query: 'q',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'auto' },
    },
  });
  if (outcome.route !== 'durable') return expect.fail('expected a durable run');
  const line = lines.find((entry) => entry[0] === 'run.code_task_withheld');
  return {
    features: outcome.admission.run.requires?.features ?? [],
    withheld: JSON.stringify(line ?? null),
  };
}

describe('engines on a deployment whose workers run none (no broker, or engine runs off)', () => {
  it('EXA-1: live workers without executor_engine prove it; none seen, or unreadable sightings, do not', async () => {
    expect(await engineExecutorAbsent(workers(LOOP_ONLY))).to.equal(true);
    expect(await engineExecutorAbsent(workers(WITH_ENGINES))).to.equal(false);
    expect(await engineExecutorAbsent(workers(NONE_SEEN))).to.equal(false);
    expect(await engineExecutorAbsent(undefined)).to.equal(false);
    const unreadable = {
      live: async () => Promise.reject(new Error('store down')),
    };
    expect(await engineExecutorAbsent(unreadable)).to.equal(false);
  });

  it('EXA-2: the picker greys every engine ENGINE_SANDBOX_MISSING, and a save is refused with it', async () => {
    const view = await picker(LOOP_ONLY).view('org-1', 'user-1');
    expect(view.engines.length).to.be.greaterThan(0);
    for (const engine of view.engines) {
      expect(engine).to.deep.equal({
        kind: engine.kind,
        available: false,
        reason: { code: 'ENGINE_SANDBOX_MISSING' },
      });
    }
    const refused = await picker(LOOP_ONLY)
      .setChoice('org-1', 'user-1', 'opencode')
      .catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal(
      'ENGINE_SANDBOX_MISSING',
    );
  });

  it('EXA-3: with an engine worker live, or before any worker is seen, the engines stay available', async () => {
    for (const live of [WITH_ENGINES, NONE_SEEN]) {
      const view = await picker(live).view('org-1', 'user-1');
      expect(view.engines.every((engine) => engine.available)).to.equal(
        true,
        [...live].join(','),
      );
    }
  });

  it("EXA-4: an engine agent's turn is admitted on the loop, code_task withheld as engine_sandbox_missing", async () => {
    const { features, withheld } = await admit(LOOP_ONLY);
    expect(features).to.not.include('executor_engine');
    expect(features).to.not.include('code_task_tool');
    expect(withheld).to.include('engine_sandbox_missing');
  });

  it('EXA-5: with an engine worker live the turn may hand off (code_task_tool required)', async () => {
    const { features, withheld } = await admit(WITH_ENGINES);
    expect(features).to.include('code_task_tool');
    expect(withheld).to.equal('null');
  });

  it('EXA-6: before any worker is seen the reason stays no_live_engine_worker, not a missing sandbox', async () => {
    const { features, withheld } = await admit(NONE_SEEN);
    expect(features).to.not.include('code_task_tool');
    expect(withheld).to.include('no_live_engine_worker');
    expect(withheld).to.not.include('engine_sandbox_missing');
  });
});
