/**
 * H1 EI-4..EI-6 (E9 availability): an engine whose image the sandbox broker
 * can neither find nor pull gets no run. Since CTL-6 the turn runs on the loop
 * and is offered no code_task (no engine child), logged; nothing is refused.
 */
import { testModelAccess } from './support/model-access';
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { HttpEngineAvailability } from '../../../src/modules/agent-harness/adapters/http/http-engine-availability';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { RELEASE_ENGINE_PROFILES, engineRef } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { EngineAvailability } from '../../../src/modules/agent-harness/ports/engine-availability';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const OPENCODE = engineRef(RELEASE_ENGINE_PROFILES[0]!.engine);

describe('H1: an engine without its image gets no engine child (EI-4, EI-5)', () => {
  let store: InMemoryExecutionStore;
  let lines: unknown[][];

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (...args) => void lines.push(['info', ...args]),
    warn: (...args) => void lines.push(['warn', ...args]),
    error: (...args) => void lines.push(['error', ...args]),
  };

  const admit = async (engines?: EngineAvailability, engineProfile: unknown = { engines: ['opencode'] }) => {
    const settings = new FakeEngineCeilings();
    return new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => 1_000 },
      agentAccess: grantingAccess((agentKey) =>
        agentDefinition({ agentKey, ...(engineProfile !== null ? { engineProfile } : {}) }),
      ),
      logger,
      engineProfiles: settings,
      modelAccess: testModelAccess(),
      ...(engines !== undefined ? { engineAvailability: engines } : {}),
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${Math.random()}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
  };

  beforeEach(() => {
    store = new InMemoryExecutionStore({ now: () => 1_000 });
    lines = [];
  });

  it('EI-4: the broker lists the engine: a loop turn with no code_task, logged engine_unavailable', async () => {
    const outcome = await admit({ unavailable: async () => new Set([OPENCODE, 'codex@0.160.0']) });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    expect(outcome.admission.run.requires?.features).to.not.include('code_task_tool');
    expect(outcome.admission.run.requires?.features).to.not.include('executor_engine');
    const withheld = lines.find((line) => line[1] === 'run.code_task_withheld');
    expect(JSON.stringify(withheld)).to.include('engine_unavailable');
  });

  it('EI-5: available, unreported, or a broker that cannot be asked: admitted', async () => {
    for (const engines of [
      { unavailable: async () => new Set(['claude_code@2.1.289']) },
      { unavailable: async () => null },
      undefined,
    ] as const) {
      const outcome = await admit(engines);
      if (outcome.route !== 'durable') return expect.fail('expected a durable run');
      expect(outcome.admission.run.requires?.features).to.include('code_task_tool');
    }
    lines = [];
    const outcome = await admit({ unavailable: () => Promise.reject(new Error('broker down')) });
    expect(outcome.route).to.equal('durable');
    const warned = lines.filter((line) => line[0] === 'warn');
    expect(warned).to.have.length(1);
    expect(JSON.stringify(warned[0])).to.include('engine_availability.unknown');
  });

  it('EI-5: a loop agent never asks', async () => {
    const unavailable = sinon.stub().resolves(new Set([OPENCODE]));
    const outcome = await admit({ unavailable }, null);
    expect(outcome.route).to.equal('durable');
    expect(unavailable.called).to.equal(false);
  });
});

describe('H1: the broker health adapter (EI-6)', () => {
  const ok = (detail: Record<string, string>) =>
    new Response(JSON.stringify({ ok: true, modes: ['docker'], active: 0, detail }), { status: 200 });

  it('reads /v1/health with the worker credential and parses the engines it names', async () => {
    const fetchImpl = sinon.stub().resolves(ok({ enginesUnavailable: 'claude_code@2.1.289,codex@0.160.0' }));
    const adapter = new HttpEngineAvailability(
      async () => 'http://sandbox-broker:8095/',
      async () => 'worker-credential',
      fetchImpl as unknown as typeof fetch,
    );
    expect([...((await adapter.unavailable()) ?? [])].sort()).to.deep.equal(['claude_code@2.1.289', 'codex@0.160.0']);
    const [url, init] = fetchImpl.firstCall.args as [string, RequestInit];
    expect(url).to.equal('http://sandbox-broker:8095/v1/health');
    expect((init.headers as Record<string, string>)['Authorization']).to.equal('Bearer worker-credential');
  });

  it('caches an answer; says nothing for a broker that does not report or is not configured', async () => {
    let now = 0;
    const fetchImpl = sinon.stub().callsFake(async () => ok({ enginesUnavailable: '' }));
    const adapter = new HttpEngineAvailability(
      async () => 'http://broker',
      async () => 'c',
      fetchImpl as unknown as typeof fetch,
      { ttlMs: 30_000, now: () => now },
    );
    expect((await adapter.unavailable())?.size).to.equal(0);
    now = 29_000;
    await adapter.unavailable();
    expect(fetchImpl.callCount).to.equal(1);
    now = 31_000;
    await adapter.unavailable();
    expect(fetchImpl.callCount).to.equal(2);
    const silent = new HttpEngineAvailability(
      async () => 'http://broker',
      async () => 'c',
      (async () => ok({})) as unknown as typeof fetch,
    );
    expect(await silent.unavailable()).to.equal(null);
    const none = new HttpEngineAvailability(async () => null, async () => 'c');
    expect(await none.unavailable()).to.equal(null);
  });

  it('rejects when the broker refuses or answers nonsense, so admission fails open with a warning', async () => {
    for (const response of [new Response('no', { status: 500 }), new Response('[]', { status: 200 })]) {
      const adapter = new HttpEngineAvailability(
        async () => 'http://broker',
        async () => 'c',
        (async () => response) as unknown as typeof fetch,
      );
      const failed = await adapter.unavailable().catch((error: unknown) => error);
      expect(failed).to.be.instanceOf(Error);
    }
  });
});
