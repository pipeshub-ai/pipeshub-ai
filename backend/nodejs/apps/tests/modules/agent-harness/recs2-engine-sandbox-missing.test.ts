/**
 * recs2 item 5: on a deployment whose coding engines have no sandbox (Helm today: no egress proxy, no
 * agent-sandbox, P5.3), engine choices are disabled up front with a plain reason, never offered and failed at run
 * time. The broker says so in its health (`engineSandbox: missing`, as it does when no egress proxy is wired); the
 * pickers show every engine unavailable with `ENGINE_SANDBOX_MISSING`, a save is refused with it, and an admission
 * withholds `code_task` with that reason.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { HttpEngineAvailability } from '../../../src/modules/agent-harness/adapters/http/http-engine-availability';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import {
  CodingEngineService,
  ENGINE_SANDBOX_MISSING_MESSAGE,
} from '../../../src/modules/agent-harness/application/coding-engine-service';
import type { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import type { EngineAvailability } from '../../../src/modules/agent-harness/ports/engine-availability';
import { testControlPlane } from '../control-plane/support/in-memory';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const HELM: EngineAvailability = {
  unavailable: async () => new Set<string>(),
  sandboxMissing: async () => true,
};
const COMPOSE: EngineAvailability = {
  unavailable: async () => new Set<string>(),
  sandboxMissing: async () => false,
};

const service = (availability: EngineAvailability): CodingEngineService => {
  return new CodingEngineService({
    ceilings: new FakeEngineCeilings(),
    access: { engineRefusals: async () => new Map(), signInsCarried: async () => true },
    preferences: new InMemoryCodingEnginePreferences(),
    transaction: testControlPlane(),
    availability,
  });
};

describe('recs2: coding engines without their sandbox add-on are disabled up front', () => {
  it('the pickers show every engine unavailable with ENGINE_SANDBOX_MISSING', async () => {
    const view = await service(HELM).view('org-1', 'user-1');
    expect(view.engines.length).to.be.greaterThan(0);
    for (const engine of view.engines) {
      expect(engine).to.deep.equal({ kind: engine.kind, available: false, reason: { code: 'ENGINE_SANDBOX_MISSING' } });
    }
  });

  it('where the sandbox is installed the engines stay available', async () => {
    const view = await service(COMPOSE).view('org-1', 'user-1');
    expect(view.engines.every((engine) => engine.available)).to.equal(true);
  });

  it('a save of an engine is refused with the plain reason, before any run', async () => {
    const refused = await service(HELM)
      .setChoice('org-1', 'user-1', 'opencode')
      .catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('ENGINE_SANDBOX_MISSING');
    expect((refused as Error).message).to.equal(ENGINE_SANDBOX_MISSING_MESSAGE);
    expect(ENGINE_SANDBOX_MISSING_MESSAGE).to.equal(
      'Coding engines need the sandbox add-on, not installed on this deployment',
    );
  });

  it("an engine agent's turn runs on the loop with no code_task, withheld as engine_sandbox_missing", async () => {
    const lines: unknown[][] = [];
    const logger: HarnessLogger = {
      debug: () => undefined,
      info: (...args) => void lines.push(['info', ...args]),
      warn: (...args) => void lines.push(['warn', ...args]),
      error: (...args) => void lines.push(['error', ...args]),
    };
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const outcome = await new RunAdmissionService({
      store: new InMemoryExecutionStore({ now: () => 1_000 }),
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => 1_000 },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, engineProfile: { engines: ['opencode'] } })),
      logger,
      engineProfiles: new FakeEngineCeilings(),
      modelAccess: testModelAccess(),
      engineAvailability: HELM,
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: 'k-helm',
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    expect(outcome.admission.run.requires?.features).to.not.include('code_task_tool');
    const withheld = lines.find((line) => line[1] === 'run.code_task_withheld');
    expect(JSON.stringify(withheld)).to.include('engine_sandbox_missing');
  });

  it("the broker health adapter reads the broker's engineSandbox and says nothing when it is absent", async () => {
    const health = (detail: Record<string, string>) =>
      (async () =>
        new Response(JSON.stringify({ ok: true, modes: ['docker'], active: 0, detail }), {
          status: 200,
        })) as unknown as typeof fetch;
    const at = (detail: Record<string, string>) =>
      new HttpEngineAvailability(async () => 'http://broker', async () => 'c', health(detail));
    expect(await at({ engineSandbox: 'missing' }).sandboxMissing()).to.equal(true);
    expect(await at({ engineSandbox: 'ready' }).sandboxMissing()).to.equal(false);
    expect(await at({}).sandboxMissing()).to.equal(false);
    const fetchImpl = sinon.stub().callsFake(health({ engineSandbox: 'missing', enginesUnavailable: '' }));
    const cached = new HttpEngineAvailability(async () => 'http://broker', async () => 'c', fetchImpl);
    expect([await cached.sandboxMissing(), (await cached.unavailable())?.size]).to.deep.equal([true, 0]);
    expect(fetchImpl.callCount).to.equal(1, 'one health answer serves both questions');
  });
});
