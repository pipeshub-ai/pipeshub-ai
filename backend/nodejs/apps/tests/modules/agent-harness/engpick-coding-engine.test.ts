/**
 * ENGPICK: the coding engine of the default chat's Agent mode. The service's
 * checks are the existing admission checks (the org ceiling, the broker's
 * images, the vendor rules over the org's models); admission resolves the
 * person's choice, else the org's default, else none, and pins the engine as
 * CTL-6 pins an engine agent's (`x.pipeshub.codeTask`). In memory:
 * RunAdmissionService → InMemoryExecutionStore → ExecutionService.claim.
 */
import { expect } from 'chai';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { ModelAccessServiceOptions } from '../../../src/modules/agent-harness/application/model-access-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { resolveCodingEngine } from '../../../src/modules/agent-harness/domain/coding-engines';
import { engineRef, isAcpKind, RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { RELEASE_ENGINE_NAMES } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { noEngineCeilings, type EngineProfileCeilings } from '../../../src/modules/agent-harness/ports/engine-profiles';
import {
  HarnessConflictError,
  HarnessEngineProfileRefusedError,
  HarnessModelAccessRefusedError,
} from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ASSISTANT_AGENT_DEFINITION_KEY } from '../../../src/modules/agent-harness/domain/rollout';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { InMemoryConfiguredModels, testModelAccess, TestModel } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { testControlPlane } from '../control-plane/support/in-memory';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const USER = 'user-1';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const CODE_TASK = 'code_task_tool';
const ANTHROPIC_ONLY: TestModel[] = [{ provider: 'anthropic', modelKey: 'm-a', modelNames: ['claude-x'], isDefault: true }];
// The native engines: under a ceiling an org sets, an ACP engine (acp-<agent>) is listed only where it names it (CE-ACP-*).
const KINDS = [...new Set(RELEASE_ENGINE_PROFILES.map((p) => p.engine.kind))].filter((k) => !isAcpKind(k));
const ACP = RELEASE_ENGINE_PROFILES.find((p) => isAcpKind(p.engine.kind))!.engine;
const ALL_KINDS = [...new Set(RELEASE_ENGINE_PROFILES.map((p) => p.engine.kind))];
const ACP_KINDS = ALL_KINDS.filter(isAcpKind);

const codeTaskOf = (spec: unknown): { profile?: { engine?: { kind?: string } } } | undefined =>
  (spec as { extensions?: Record<string, Record<string, unknown>> }).extensions?.['x.pipeshub']?.['codeTask'] as
    | { profile?: { engine?: { kind?: string } } }
    | undefined;

describe('ENGPICK: the coding engine of the default chat', () => {
  let settings: FakeEngineCeilings;
  let ceilings: EngineProfileCeilings;
  let preferences: InMemoryCodingEnginePreferences;
  let missing: Set<string>;
  let lines: Array<[string, Record<string, unknown> | undefined]>;

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (event, meta) => void lines.push([event, meta]),
    warn: (event, meta) => void lines.push([event, meta]),
    error: (event, meta) => void lines.push([event, meta]),
  };

  const engines = (access: Partial<ModelAccessServiceOptions> = {}) =>
    new CodingEngineService({
      ceilings,
      access: testModelAccess(access),
      preferences,
      transaction: testControlPlane(),
      availability: { unavailable: async () => missing },
      logger,
    });

  beforeEach(() => {
    lines = [];
    missing = new Set();
    preferences = new InMemoryCodingEnginePreferences();
    settings = new FakeEngineCeilings();
    ceilings = settings;
  });

  describe('the choices every picker shows', () => {
    it('CE-1: only the engines the org ceiling allows are listed, in release order', async () => {
      expect((await engines().engines(ORG)).map((e) => e.kind)).to.deep.equal(KINDS);
      await settings.write(ORG, { engines: ['codex'] }, 'p_admin');
      expect(await engines().engines(ORG)).to.deep.equal([{ kind: 'codex', available: true }]);
    });

    it('CE-2: Claude Code over Anthropic models is refused on the managed offering with the vendor rule’s code, allowed self-hosted', async () => {
      const managed = await engines({ offering: 'managed', models: new InMemoryConfiguredModels(ANTHROPIC_ONLY) }).engines(ORG);
      expect(managed.find((e) => e.kind === 'claude_code')).to.deep.equal({
        kind: 'claude_code',
        available: false,
        reason: { code: 'OFFERING_NOT_PERMITTED' },
      });
      expect(managed.filter((e) => e.kind !== 'claude_code').every((e) => e.available)).to.equal(true);
      const oss = await engines({ offering: 'oss', models: new InMemoryConfiguredModels(ANTHROPIC_ONLY) }).engines(ORG);
      expect(oss.every((e) => e.available)).to.equal(true);
    });

    it('CE-3: an org without a configured model can use no engine (ROUTE_NOT_CONFIGURED)', async () => {
      const listed = await engines({ models: new InMemoryConfiguredModels([]) }).engines(ORG);
      expect(listed.map((e) => e.reason?.code)).to.deep.equal(KINDS.map(() => 'ROUTE_NOT_CONFIGURED'));
    });

    it('CE-ACP-1: an ACP engine is no choice until the org names it, then it is listed under its own name', async () => {
      expect((await engines().engines(ORG)).map((e) => e.kind)).to.not.include(ACP.kind);
      await settings.write(ORG, { engines: [...KINDS, ACP.kind] }, 'p_admin');
      const listed = await engines().engines(ORG);
      expect(listed.map((e) => e.kind)).to.deep.equal([...KINDS, ACP.kind]);
      expect(listed.find((e) => e.kind === ACP.kind)).to.deep.equal({
        kind: ACP.kind,
        name: 'opencode (ACP)',
        available: true,
      });
      expect(listed.filter((e) => e.kind !== ACP.kind).every((e) => !('name' in e))).to.equal(true);
    });

    it('CE-ACP-2: a named ACP engine this deployment has not installed is absent, never greyed', async () => {
      await settings.write(ORG, { engines: ['codex', `${ACP.kind}@${ACP.version}`] }, 'p_admin');
      missing = new Set([engineRef(ACP)]);
      expect((await engines().engines(ORG)).map((e) => e.kind)).to.deep.equal(['codex']);
      const refused = await engines().setChoice(ORG, USER, ACP.kind).catch((e: unknown) => e);
      expect(refused).to.be.instanceOf(HarnessConflictError);
      expect((refused as HarnessConflictError).code).to.equal('ENGINE_UNAVAILABLE');
    });

    it('CE-ACP-3: while the broker cannot say what is installed, an ACP engine is not offered', async () => {
      await settings.write(ORG, { engines: ['opencode', ACP.kind] }, 'p_admin');
      const blind = new CodingEngineService({
        ceilings: settings,
        access: testModelAccess(),
        preferences,
        transaction: testControlPlane(),
        availability: { unavailable: async () => Promise.reject(new Error('down')) },
        logger,
      });
      expect((await blind.engines(ORG)).map((e) => e.kind)).to.deep.equal(['opencode']);
      const silent = new CodingEngineService({
        ceilings: settings,
        access: testModelAccess(),
        preferences,
        transaction: testControlPlane(),
        availability: { unavailable: async () => null },
        logger,
      });
      expect((await silent.engines(ORG)).map((e) => e.kind)).to.deep.equal(['opencode']);
    });

    it('CE-ACP-4: a person cannot choose an ACP engine the org never named', async () => {
      const refused = await engines().setChoice(ORG, USER, ACP.kind).catch((e: unknown) => e);
      expect(refused).to.be.instanceOf(HarnessEngineProfileRefusedError);
      await settings.write(ORG, { engines: ['opencode', ACP.kind] }, 'p_admin');
      expect((await engines().setChoice(ORG, USER, ACP.kind)).choice).to.equal(ACP.kind);
    });

    it('CE-ACP-5: with no org ceiling every installed ACP engine is listed under its own name and may be chosen', async () => {
      ceilings = noEngineCeilings;
      const listed = await engines().engines(ORG);
      expect(listed.map((e) => e.kind)).to.deep.equal(ALL_KINDS);
      for (const kind of ACP_KINDS) {
        expect(listed.find((e) => e.kind === kind)).to.deep.equal({ kind, name: RELEASE_ENGINE_NAMES[kind], available: true });
        expect((await engines().setChoice(ORG, USER, kind)).choice).to.equal(kind);
      }
      missing = new Set([engineRef(ACP)]);
      expect((await engines().engines(ORG)).map((e) => e.kind)).to.not.include(ACP.kind);
    });

    it('CE-4: an engine the broker has no image for is ENGINE_UNAVAILABLE', async () => {
      const codex = RELEASE_ENGINE_PROFILES.find((p) => p.engine.kind === 'codex');
      missing = new Set([engineRef(codex!.engine)]);
      const listed = await engines().engines(ORG);
      expect(listed.find((e) => e.kind === 'codex')?.reason).to.deep.equal({ code: 'ENGINE_UNAVAILABLE' });
    });
  });

  describe('saving a choice', () => {
    it('CE-5: the person’s engine must be usable now; org_default and none always are; a refusal writes nothing', async () => {
      const service = engines({ offering: 'managed', models: new InMemoryConfiguredModels(ANTHROPIC_ONLY) });
      const refused = await service.setChoice(ORG, USER, 'claude_code').catch((e: unknown) => e);
      expect(refused).to.be.instanceOf(HarnessModelAccessRefusedError);
      expect((refused as HarnessModelAccessRefusedError).code).to.equal('offering_not_permitted');
      expect((await preferences.read(ORG, USER)).user).to.equal(null);
      await settings.write(ORG, { engines: ['opencode'] }, 'p_admin');
      const outside = await service.setChoice(ORG, USER, 'codex').catch((e: unknown) => e);
      expect(outside).to.be.instanceOf(HarnessEngineProfileRefusedError);
      expect((outside as HarnessEngineProfileRefusedError).dimension).to.equal('engine');
      expect((await service.setChoice(ORG, USER, 'opencode')).choice).to.equal('opencode');
      expect((await service.setChoice(ORG, USER, 'none')).choice).to.equal('none');
      expect((await service.setChoice(ORG, USER, 'org_default')).choice).to.equal('org_default');
      expect((await preferences.read(ORG, USER)).user).to.equal(null);
    });

    it('CE-6: the org default is checked the same way, logged with ids only, and null clears it', async () => {
      missing = new Set([engineRef(RELEASE_ENGINE_PROFILES.find((p) => p.engine.kind === 'codex')!.engine)]);
      expect(await engines().setOrgDefault(ORG, 'admin-1', 'codex').catch((e: unknown) => e)).to.be.instanceOf(
        HarnessConflictError,
      );
      missing = new Set();
      expect((await engines().setOrgDefault(ORG, 'admin-1', 'codex')).orgDefault).to.equal('codex');
      expect(lines).to.deep.include(['coding_engine.org_default_set', { event: 'coding_engine.org_default_set', orgId: ORG, actorId: 'admin-1', engine: 'codex' }]);
      expect((await engines().setOrgDefault(ORG, 'admin-1', null)).orgDefault).to.equal(null);
    });
  });

  describe('the engine of one turn', () => {
    it('CE-7: the person’s choice, else the org’s default, else none; a chosen engine is a setting, not a fallback', () => {
      expect(resolveCodingEngine({ user: null, org: null })).to.deep.equal({ engine: null, source: 'none' });
      expect(resolveCodingEngine({ user: null, org: 'codex' })).to.deep.equal({ engine: 'codex', source: 'org' });
      expect(resolveCodingEngine({ user: 'opencode', org: 'codex' })).to.deep.equal({ engine: 'opencode', source: 'user' });
      expect(resolveCodingEngine({ user: 'none', org: 'codex' })).to.deep.equal({ engine: null, source: 'user' });
    });

    it('CE-8: forTurn withholds an engine model access refuses, and anything it cannot read gives no engine, never a refusal', async () => {
      await preferences.writeOrg(ORG, 'claude_code');
      expect(await engines({ offering: 'managed', models: new InMemoryConfiguredModels(ANTHROPIC_ONLY) }).forTurn(ORG, USER)).to.deep.equal({
        engine: null,
        source: 'org',
        withheld: 'offering_not_permitted',
      });
      const down = new CodingEngineService({
        ceilings: settings,
        access: testModelAccess(),
        preferences: Object.assign(Object.create(preferences) as InMemoryCodingEnginePreferences, {
          read: () => Promise.reject(new Error('mongo down')),
        }) as never,
        transaction: testControlPlane(),
      });
      expect(await down.forTurn(ORG, USER)).to.deep.equal({ engine: null, source: 'none', withheld: 'unavailable' });
    });
  });

  describe('admission of a default-chat Agent-mode turn', () => {
    let now: number;
    let store: InMemoryExecutionStore;
    let execution: ExecutionService;

    const admit = async (
      chatMode = 'agent',
      agentKey?: string,
      access: Partial<ModelAccessServiceOptions> = {},
    ): Promise<HarnessRun> => {
      now += 1;
      const outcome = await new RunAdmissionService({
        store,
        rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
        clock: { now: () => now },
        agentAccess: grantingAccess((key) => agentDefinition({ agentKey: key })),
        logger,
        executors: profileExecutor,
        engineProfiles: ceilings,
        modelAccess: testModelAccess(),
        codingEngine: engines(access),
        workerFeatures: {
          live: async () => true,
          metrics: { withheld: () => undefined },
        } as never,
      }).submit({
        orgId: ORG,
        actorId: USER,
        ...(agentKey !== undefined ? { agentKey } : {}),
        authorization: 'Bearer t',
        durableRequested: true,
        idempotencyKey: `k-${String(now)}`,
        deadlineMs: 60_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode } },
      });
      if (outcome.route !== 'durable') return expect.fail(`expected a durable run, got ${JSON.stringify(outcome)}`);
      return outcome.admission.run;
    };

    const spec = async (run: HarnessRun) => {
      const claimed = await execution.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'w',
        attemptId: `attempt-${run.runId}`,
        leaseDurationMs: 1_000,
      });
      return runSpecSchema.parse(claimed.pinned?.spec);
    };

    beforeEach(() => {
      now = 1_000;
      store = new InMemoryExecutionStore({ now: () => now });
      execution = new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
        capabilities: { current: () => DURABLE },
        resolveSpec: runSpecResolver(profileExecutor),
      });
    });

    it('AD-1 (source user): the person’s engine is pinned as codeTask on a loop run that requires code_task', async () => {
      await preferences.writeOrg(ORG, 'opencode');
      await preferences.writeUser(ORG, USER, 'codex');
      const run = await admit();
      expect(run.requires?.features).to.include(CODE_TASK);
      const pinned = await spec(run);
      expect(pinned.executor).to.deep.equal({ kind: 'loop' });
      expect(codeTaskOf(pinned)?.profile?.engine?.kind).to.equal('codex');
      expect(lines.find(([event]) => event === 'run.coding_engine_chosen')?.[1]).to.include({ engine: 'codex', source: 'user' });
    });

    it('AD-ACP: with no org ceiling each ACP engine a person chooses, or the org defaults to, is admitted and pinned', async () => {
      ceilings = noEngineCeilings;
      for (const kind of ACP_KINDS) {
        await preferences.writeUser(ORG, USER, kind);
        expect(codeTaskOf(await spec(await admit()))?.profile?.engine?.kind).to.equal(kind);
      }
      await preferences.writeUser(ORG, USER, null);
      await preferences.writeOrg(ORG, ACP.kind);
      expect(codeTaskOf(await spec(await admit()))?.profile?.engine?.kind).to.equal(ACP.kind);
    });

    it('AD-2 (source org default): with no choice of their own, the org’s default engine is pinned', async () => {
      await preferences.writeOrg(ORG, 'opencode');
      const pinned = await spec(await admit());
      expect(codeTaskOf(pinned)?.profile?.engine?.kind).to.equal('opencode');
      expect(lines.find(([event]) => event === 'run.coding_engine_chosen')?.[1]).to.include({ source: 'org' });
    });

    it('AD-3 (source none): no default and no choice, or the person chose none: today’s Agent mode, no codeTask', async () => {
      let run = await admit();
      expect(run.requires?.features ?? []).to.not.include(CODE_TASK);
      expect(codeTaskOf(await spec(run))).to.equal(undefined);
      await preferences.writeOrg(ORG, 'codex');
      await preferences.writeUser(ORG, USER, 'none');
      run = await admit();
      expect(codeTaskOf(await spec(run))).to.equal(undefined);
      expect(lines.some(([event]) => event === 'run.coding_engine_chosen')).to.equal(false);
    });

    it('AD-4: a chosen engine the ceiling leaves out moves to one that is on (WP-111); one model access refuses is withheld and logged; the turn still runs', async () => {
      await preferences.writeOrg(ORG, 'codex');
      await settings.write(ORG, { engines: ['opencode'] }, 'p_admin');
      expect(codeTaskOf(await spec(await admit()))?.profile?.engine?.kind).to.equal('opencode');
      expect(lines.find(([event]) => event === 'run.coding_engine_moved')?.[1]).to.include({ from: 'codex', engine: 'opencode', reason: 'engine_off' });
      await settings.write(ORG, null, 'p_admin');
      await preferences.writeOrg(ORG, 'claude_code');
      lines = [];
      const refused = await admit('agent', undefined, { offering: 'managed', models: new InMemoryConfiguredModels(ANTHROPIC_ONLY) });
      expect(codeTaskOf(await spec(refused))).to.equal(undefined);
      expect(lines.find(([event]) => event === 'run.code_task_withheld')?.[1]).to.include({
        reason: 'offering_not_permitted',
        source: 'org',
      });
    });

    it('AD-5: only the assistant’s Agent mode reads the choice: a named agent and the search modes do not', async () => {
      await preferences.writeUser(ORG, USER, 'codex');
      expect(codeTaskOf(await spec(await admit('quick', 'agent-1')))).to.equal(undefined);
      expect(codeTaskOf(await spec(await admit('internal_search')))).to.equal(undefined);
      const agentMode = await admit('agent');
      expect(codeTaskOf(await spec(agentMode))?.profile?.engine?.kind).to.equal('codex');
      expect(ASSISTANT_AGENT_DEFINITION_KEY).to.equal('agentIdPlaceholder');
    });

    it('AD-6: a turn admitted after the choice changes pins the new engine; the earlier run keeps its pin', async () => {
      await preferences.writeUser(ORG, USER, 'codex');
      const first = await admit();
      await preferences.writeUser(ORG, USER, 'opencode');
      const second = await admit();
      expect(codeTaskOf(await spec(second))?.profile?.engine?.kind).to.equal('opencode');
      expect(codeTaskOf(await spec(first))?.profile?.engine?.kind).to.equal('codex');
    });
  });
});
