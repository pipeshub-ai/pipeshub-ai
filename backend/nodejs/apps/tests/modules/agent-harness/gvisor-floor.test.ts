/**
 * GV-N1..GV-N6: coding engines need gVisor on a
 * production install. Node pins every release engine profile at the gVisor
 * floor; the pickers show an engine the broker cannot run at that floor as
 * ENGINE_ISOLATION_UNAVAILABLE and a save of it is refused; admission offers
 * no engine child for it. The documented dev/test override
 * (PIPESHUB_ENGINES_WITHOUT_GVISOR=true) lowers the floor to hardened runc and
 * the view tells admins (`withoutGvisor`). With runsc offered, gVisor runs.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { HttpEngineAvailability } from '../../../src/modules/agent-harness/adapters/http/http-engine-availability';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import {
  ENGINES_WITHOUT_GVISOR_ENV,
  enginesWithoutGvisor,
  GVISOR_INSTALL_GUIDE,
  GVISOR_REQUIRED_MESSAGE,
  offersFloor,
  releaseIsolationFloor,
} from '../../../src/modules/agent-harness/domain/engine-isolation';
import { RELEASE_ENGINE_PROFILES, releaseEngineProfiles } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { EngineAvailability } from '../../../src/modules/agent-harness/ports/engine-availability';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { testControlPlane } from '../control-plane/support/in-memory';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const PRODUCTION = {};
const OVERRIDE = { [ENGINES_WITHOUT_GVISOR_ENV]: 'true' };
const RUNC = new Set(['docker']);
const RUNSC = new Set(['docker', 'gvisor']);

const broker = (modes: ReadonlySet<string> | null): EngineAvailability => ({
  unavailable: async () => new Set<string>(),
  modes: async () => modes,
});

describe('GV-N: coding engines need gVisor on a production install', () => {
  let settings: FakeEngineCeilings;
  let lines: unknown[][];

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (...args) => void lines.push(['info', ...args]),
    warn: (...args) => void lines.push(['warn', ...args]),
    error: (...args) => void lines.push(['error', ...args]),
  };

  const service = (env: Record<string, string>, modes: ReadonlySet<string> | null) =>
    new CodingEngineService({
      ceilings: settings,
      access: testModelAccess(),
      preferences: new InMemoryCodingEnginePreferences(),
      transaction: testControlPlane(),
      availability: broker(modes),
      releases: releaseEngineProfiles(env),
      withoutGvisor: enginesWithoutGvisor(env),
      logger,
    });

  beforeEach(() => {
    lines = [];
    settings = new FakeEngineCeilings();
  });

  it('GV-N1: the release floor is gVisor; only the override, exactly "true", lowers it to hardened runc', () => {
    expect(releaseIsolationFloor(PRODUCTION)).to.equal('gvisor');
    expect(releaseEngineProfiles(PRODUCTION).every((p) => p.isolationMin === 'gvisor')).to.equal(true);
    expect(releaseEngineProfiles(OVERRIDE).every((p) => p.isolationMin === 'docker')).to.equal(true);
    for (const value of ['', 'false', '1', 'yes', 'ture']) {
      expect(enginesWithoutGvisor({ [ENGINES_WITHOUT_GVISOR_ENV]: value }), value).to.equal(false);
    }
    expect(enginesWithoutGvisor({ [ENGINES_WITHOUT_GVISOR_ENV]: ' TRUE ' })).to.equal(true);
    expect(process.env[ENGINES_WITHOUT_GVISOR_ENV]).to.equal(undefined);
    expect(RELEASE_ENGINE_PROFILES.every((p) => p.isolationMin === 'gvisor')).to.equal(true);
  });

  it('GV-N2: a runtime meets a floor at or below its rank, never above', () => {
    expect(offersFloor(RUNC, 'gvisor')).to.equal(false);
    expect(offersFloor(RUNSC, 'gvisor')).to.equal(true);
    expect(offersFloor(RUNC, 'docker')).to.equal(true);
    expect(offersFloor(new Set(['k8s']), 'gvisor')).to.equal(true);
    expect(offersFloor(RUNSC, 'microvm')).to.equal(false);
  });

  it('GV-N3: production without runsc: every engine is ENGINE_ISOLATION_UNAVAILABLE and a save is refused with the install guide', async () => {
    const engines = service(PRODUCTION, RUNC);
    const listed = await engines.engines(ORG);
    expect(listed.length).to.be.greaterThan(0);
    expect(listed.every((e) => !e.available && e.reason?.code === 'ENGINE_ISOLATION_UNAVAILABLE')).to.equal(true);
    const error = await engines.setOrgDefault(ORG, 'admin-1', 'codex').catch((e: unknown) => e);
    expect(error).to.be.instanceOf(HarnessConflictError);
    expect((error as HarnessConflictError).code).to.equal('ENGINE_ISOLATION_UNAVAILABLE');
    expect((error as Error).message).to.equal(GVISOR_REQUIRED_MESSAGE);
    expect(GVISOR_REQUIRED_MESSAGE).to.equal(
      `Coding engines need the gVisor sandbox runtime on this server — install guide: ${GVISOR_INSTALL_GUIDE}`,
    );
    expect((await engines.view(ORG, 'admin-1')).withoutGvisor).to.equal(undefined);
  });

  it('GV-N4: the override admits hardened runc and the view carries the admins’ warning', async () => {
    const view = await service(OVERRIDE, RUNC).view(ORG, 'admin-1');
    expect(view.engines.every((e) => e.available)).to.equal(true);
    expect(view.withoutGvisor).to.equal(true);
  });

  it('GV-N4b: the override on a broker that offers gVisor raises no warning: engines run there (R31-GVISO)', async () => {
    const view = await service(OVERRIDE, RUNSC).view(ORG, 'admin-1');
    expect(view.engines.every((e) => e.available)).to.equal(true);
    expect(view.withoutGvisor).to.equal(undefined);
    // A broker that cannot say what it offers keeps the warning: nothing shows the engines are not on runc.
    expect((await service(OVERRIDE, null).view(ORG, 'admin-1')).withoutGvisor).to.equal(true);
    expect((await service(PRODUCTION, RUNSC).view(ORG, 'admin-1')).withoutGvisor).to.equal(undefined);
  });

  it('GV-N5: with runsc offered, production engines are available; a broker that does not say admits', async () => {
    expect((await service(PRODUCTION, RUNSC).engines(ORG)).every((e) => e.available)).to.equal(true);
    expect((await service(PRODUCTION, null).engines(ORG)).every((e) => e.available)).to.equal(true);
  });

  describe('GV-N6: admission', () => {
    const admit = (availability: EngineAvailability) =>
      new RunAdmissionService({
        store: new InMemoryExecutionStore({ now: () => 1_000 }),
        rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
        clock: { now: () => 1_000 },
        agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, engineProfile: { engines: ['opencode'] } })),
        logger,
        engineProfiles: settings,
        modelAccess: testModelAccess(),
        engineAvailability: availability,
      }).submit({
        orgId: ORG,
        actorId: 'user-1',
        agentKey: 'agent-1',
        authorization: 'Bearer t',
        idempotencyKey: `k-${Math.random()}`,
        deadlineMs: 60_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      });

    it('a production broker without runsc gets no engine child, logged engine_isolation_unavailable', async () => {
      const outcome = await admit(broker(RUNC));
      if (outcome.route !== 'durable') return expect.fail('expected a durable run');
      expect(outcome.admission.run.requires?.features).to.not.include('code_task_tool');
      const withheld = lines.find((line) => line[1] === 'run.code_task_withheld');
      expect(JSON.stringify(withheld)).to.include('engine_isolation_unavailable');
    });

    it('runsc offered, or runtimes not reported: the engine child is offered', async () => {
      for (const modes of [RUNSC, null]) {
        const outcome = await admit(broker(modes));
        if (outcome.route !== 'durable') return expect.fail('expected a durable run');
        expect(outcome.admission.run.requires?.features).to.include('code_task_tool');
      }
    });
  });

  it('GV-N7: the broker health adapter reads the runtimes it offers', async () => {
    const fetchImpl = sinon.stub().resolves(
      new Response(JSON.stringify({ ok: true, modes: ['docker', 'gvisor'], active: 0, detail: {} }), { status: 200 }),
    );
    const adapter = new HttpEngineAvailability(
      async () => 'http://broker',
      async () => 'c',
      fetchImpl as unknown as typeof fetch,
    );
    expect([...((await adapter.modes()) ?? [])].sort()).to.deep.equal(['docker', 'gvisor']);
    expect(await adapter.unavailable()).to.equal(null);
    expect(await adapter.gvisorSyscallFilter()).to.equal(null);
    expect(fetchImpl.callCount).to.equal(1);
  });

  describe('GV-N8: gVisor without its own syscall filter is shown, not refused (R32-SECCOMP)', () => {
    const health = (filter: string | undefined) =>
      new HttpEngineAvailability(
        async () => 'http://broker',
        async () => 'c',
        (async () =>
          new Response(
            JSON.stringify({
              ok: true,
              modes: ['docker', 'gvisor'],
              active: 0,
              detail: filter === undefined ? {} : { gvisorSyscallFilter: filter },
            }),
            { status: 200 },
          )) as unknown as typeof fetch,
      );

    it('reads the broker’s on, off, or nothing', async () => {
      expect(await health('on').gvisorSyscallFilter()).to.equal(true);
      expect(await health('off').gvisorSyscallFilter()).to.equal(false);
      expect(await health(undefined).gvisorSyscallFilter()).to.equal(null);
      expect(await health('maybe').gvisorSyscallFilter()).to.equal(null);
    });

    const withFilter = (filter: boolean | null, failing = false) =>
      new CodingEngineService({
        ceilings: settings,
        access: testModelAccess(),
        preferences: new InMemoryCodingEnginePreferences(),
        transaction: testControlPlane(),
        availability: {
          ...broker(RUNSC),
          gvisorSyscallFilter: async () => {
            if (failing) throw new Error('broker away');
            return filter;
          },
        },
        releases: releaseEngineProfiles(PRODUCTION),
        withoutGvisor: false,
        logger,
      });

    it('off: every engine stays available and the view says false for the admins’ warning', async () => {
      const view = await withFilter(false).view(ORG, 'admin-1');
      expect(view.engines.every((e) => e.available)).to.equal(true);
      expect(view.gvisorSyscallFilter).to.equal(false);
    });

    it('on: the view says true; unknown or unreachable: absent', async () => {
      expect((await withFilter(true).view(ORG, 'admin-1')).gvisorSyscallFilter).to.equal(true);
      expect((await withFilter(null).view(ORG, 'admin-1')).gvisorSyscallFilter).to.equal(undefined);
      expect((await withFilter(false, true).view(ORG, 'admin-1')).gvisorSyscallFilter).to.equal(undefined);
    });
  });
});
