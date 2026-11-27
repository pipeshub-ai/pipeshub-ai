/**
 * G-48 (C20.49): the degradation chain, as Node takes part in it. G48-1: an engine agent's turn that runs without
 * code tools says so, and why, in a closed set of reasons the chat words (`ran_without_code_tools`), journaled with
 * run.admitted. G48-2: a loop run of an agent that grants more than one model pins the others as alternates, in the
 * agent's order, for an outage only, so the worker's model policy (P4.7) can move a turn whose provider is down.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { codeToolsWithheld } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import type { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import type { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { admittedJournalAppend, CODE_TOOLS_WITHHELD } from '../../../src/modules/agent-harness/domain/execution';
import { ALTERNATE_MODEL_ON, alternateModelRoutes, MAX_ALTERNATE_MODELS, pinRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { KNOWN_WORKER_FEATURES, LEGACY_RUN_FEATURES } from '../../../src/modules/agent-harness/domain/worker-compat';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import type { EngineAvailability } from '../../../src/modules/agent-harness/ports/engine-availability';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/alternate-models.json'), 'utf-8'),
) as { on: string[]; max: number; agentModels: Array<{ provider: string; modelName: string }>; policy: unknown[] };

const quiet: HarnessLogger = { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined };
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

async function admit(definition: AgentRunDefinition, availability?: EngineAvailability, live: readonly string[] = []) {
  const store = new InMemoryExecutionStore({ now: () => 1_000 });
  const outcome = await new RunAdmissionService({
    store,
    rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
    clock: { now: () => 1_000 },
    agentAccess: grantingAccess(() => definition),
    logger: quiet,
    engineProfiles: new FakeEngineCeilings(),
    modelAccess: testModelAccess(),
    ...(availability !== undefined ? { engineAvailability: availability } : {}),
    workerFeatures: { live: async (feature: string) => live.includes(feature), metrics: { withheld: () => undefined } } as never,
  }).submit({
    orgId: 'org-1',
    actorId: 'user-1',
    agentKey: definition.agentKey,
    authorization: 'Bearer t',
    idempotencyKey: 'k-g48',
    deadlineMs: 60_000,
    input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
  });
  if (outcome.route !== 'durable') throw new Error('expected a durable run');
  const run = outcome.admission.run;
  const events = await store.journal.readStream('org-1', run.sessionId, 0, 100);
  const { spec } = pinRunSpec({ run, agentDefinition: definition, via: 'session' }, DURABLE);
  return {
    run,
    spec,
    notices: events.filter((event) => event.type === 'x.pipeshub.run.notice').map((event) => event.data),
  };
}

const NO_SANDBOX: EngineAvailability = { unavailable: async () => new Set<string>(), sandboxMissing: async () => true };

describe('G-48: the degradation chain (C20.49)', () => {
  describe('G48-1: a turn that runs without code tools says so, and why', () => {
    it("an engine agent's turn on a server without the engine sandbox is admitted with the notice, journaled with run.admitted", async () => {
      const { run, notices } = await admit(agentDefinition({ engineProfile: { engines: ['opencode'] } }), NO_SANDBOX);
      expect(run.requires?.features).to.not.include('code_task_tool');
      expect(notices).to.deep.equal([{ code: 'ran_without_code_tools', item: 'not_available' }]);
    });

    it('an agent without an engine profile gets no notice: nothing was withheld', async () => {
      const { notices } = await admit(agentDefinition({ engineProfile: null }), NO_SANDBOX);
      expect(notices).to.deep.equal([]);
    });

    it('every withholding reason maps to one of the four the chat words; an org rule reads as not allowed', () => {
      const cases: Record<string, string> = {
        unavailable: 'not_available',
        engine_sandbox_missing: 'not_available',
        engine_unavailable: 'not_available',
        no_live_engine_worker: 'not_available',
        engine_version_stopped: 'paused_by_admin',
        egress_stopped: 'paused_by_admin',
        engine_isolation_unavailable: 'isolation_unavailable',
        engine_group_required: 'not_allowed',
        engine_not_allowed: 'not_allowed',
        model_access_denied: 'not_allowed',
      };
      for (const [reason, item] of Object.entries(cases)) {
        expect(codeToolsWithheld(reason), reason).to.equal(item);
        expect(CODE_TOOLS_WITHHELD).to.include(item);
      }
    });

    it('the notice is journaled with its reason as the item', () => {
      const append = admittedJournalAppend(
        { orgId: 'org-1', sessionId: 'ses-1', runId: 'run-1', actorId: 'user-1' },
        'session',
        undefined,
        'interactive',
        [{ code: 'ran_without_code_tools', item: 'paused_by_admin' }],
      );
      expect(append.events[1]!.data).to.deep.equal({ code: 'ran_without_code_tools', item: 'paused_by_admin' });
    });
  });

  describe("G48-2: an agent's other models are pinned as alternates for an outage", () => {
    it('while a live worker moves runs (model_fallback), a loop run pins the chosen model first, then the agent’s others, each for 5xx, timeout and an exhausted rate limit', async () => {
      const { run, spec } = await admit(agentDefinition({ engineProfile: null }), undefined, ['model_fallback']);
      expect(run.requires?.features).to.include('model_fallback');
      expect(spec.model.policy).to.deep.equal([
        { provider: 'openai', model: 'gpt-x' },
        { provider: 'anthropic', model: 'claude-x', on: ['5xx', 'timeout', '429_exhausted'] },
      ]);
    });

    it('no live worker moves runs: one route, as before, and the run does not require the feature', async () => {
      const { run, spec } = await admit(agentDefinition({ engineProfile: null }));
      expect(run.requires?.features).to.not.include('model_fallback');
      expect(spec.model.policy).to.deep.equal([{ provider: 'openai', model: 'gpt-x' }]);
    });

    it('an agent with one model, or none (the org default), has no alternate', async () => {
      const one = agentDefinition({ engineProfile: null });
      const { run, spec } = await admit({ ...one, models: one.models.slice(0, 1) }, undefined, ['model_fallback']);
      expect(run.requires?.features).to.not.include('model_fallback');
      expect(spec.model.policy).to.deep.equal([{ provider: 'openai', model: 'gpt-x' }]);
      expect(alternateModelRoutes(null, { provider: 'pipeshub', model: 'org-default' })).to.deep.equal([]);
    });

    it('never a content filter (moving past a filter would weaken it), never the chosen one twice, at most two', () => {
      const models = ['a', 'b', 'c', 'd'].map((name) => ({ modelKey: name, modelName: name, provider: 'p', isReasoning: false }));
      const routes = alternateModelRoutes(agentDefinition({ models }), { provider: 'p', model: 'b' });
      expect(routes.map((route) => route.model)).to.deep.equal(['a', 'c']);
      expect(routes).to.have.length(MAX_ALTERNATE_MODELS);
      for (const route of routes) expect(route.on).to.not.include('content_filter');
    });
  });

  it('G48-2: Node pins what vectors/alternate-models.json says, which the worker’s policy test reads too', () => {
    expect([...ALTERNATE_MODEL_ON]).to.deep.equal(VECTOR.on);
    expect(MAX_ALTERNATE_MODELS).to.equal(VECTOR.max);
    const models = VECTOR.agentModels.map((m, i) => ({ modelKey: `k${i}`, modelName: m.modelName, provider: m.provider, isReasoning: false }));
    const { spec } = pinRunSpec(
      { run: { orgId: 'o', runId: 'r', sessionId: 's', actorId: 'u', createdAt: 0, absoluteDeadlineAt: 60_000, harnessMajor: 2, requires: { foldVersion: 8, features: ['model_fallback'] }, input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } } as never, agentDefinition: agentDefinition({ models, engineProfile: null }), via: 'session' },
      DURABLE,
    );
    expect(spec.model.policy).to.deep.equal(VECTOR.policy);
  });

  it('G48-2: a run admitted before requirements were recorded never needs model_fallback (it pinned no alternate)', () => {
    expect(LEGACY_RUN_FEATURES).to.not.include('model_fallback');
    expect(KNOWN_WORKER_FEATURES).to.include('model_fallback');
  });
});
