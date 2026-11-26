/**
 * WP-111 ENGINEOFF: an admin's engine switch (the org ceiling's `engines`) is a control, held by one policy object
 * (`EngineEligibility`) at every place an engine is chosen: a turn's admission, the default chat's engine choice, a
 * code_task child's spec pin and an engine run's claim. A turn whose chosen engine is off moves to an engine that is
 * on and says so; one with nowhere to go runs without code tools and says what to do; a run pinned before the switch
 * starts no new engine session.
 */
import { expect } from 'chai';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { codeToolsWithheld } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import {
  childEngineAuthority,
  ChildRunService,
} from '../../../src/modules/agent-harness/application/child-run-service';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { ceilingAllows, EngineEligibility } from '../../../src/modules/agent-harness/application/engine-eligibility';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { noEngineCeilings } from '../../../src/modules/agent-harness/ports/engine-profiles';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import { CODE_TOOLS_WITHHELD, HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  profileExecutor,
  runSpecResolver,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { testControlPlane } from '../control-plane/support/in-memory';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const USER = 'user-1';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot(
  { ...LEAST_CAPABILITIES, 'journal.durable': true },
  1,
);
/** Claude Code turned off: the admin cleared it under Engine profile → Engines. */
const CLAUDE_CODE_OFF = { engines: ['opencode', 'codex'] };

type Spec = ReturnType<typeof runSpecSchema.parse>;
const codeTaskEngine = (spec: Spec): string | undefined =>
  (
    spec.extensions?.['x.pipeshub'] as
      { codeTask?: { profile?: { engine?: { kind?: string } } } } | undefined
  )?.codeTask?.profile?.engine?.kind;

describe('WP-111 ENGINEOFF: an admin turning a coding engine off is enforced wherever an engine is chosen', () => {
  let now: number;
  let lines: Array<[string, Record<string, unknown> | undefined]>;
  let settings: FakeEngineCeilings;
  let preferences: InMemoryCodingEnginePreferences;
  let store: InMemoryExecutionStore;
  let execution: ExecutionService;
  let groupsDeny: Set<string>;

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (event, meta) => void lines.push([event, meta]),
    warn: (event, meta) => void lines.push([event, meta]),
    error: (event, meta) => void lines.push([event, meta]),
  };

  const groupAccess = {
    policy: async () => null,
    admits: async (
      _org: string,
      _user: string,
      subject: { kind: string; engine?: string },
    ) => subject.kind !== 'engine' || !groupsDeny.has(subject.engine ?? ''),
  } as never;

  const eligibility = () =>
    new EngineEligibility({ ceilings: settings, groupAccess, logger });

  const engines = () =>
    new CodingEngineService({
      ceilings: settings,
      access: testModelAccess(),
      preferences,
      transaction: testControlPlane(),
      availability: { unavailable: async () => new Set<string>() },
      logger,
      groupAccess,
    });

  const admit = async (agent?: {
    engineProfile: unknown;
  }): Promise<{ run: HarnessRun; spec: Spec; notices: unknown[] }> => {
    now += 1;
    const outcome = await new RunAdmissionService({
      store,
      rollout: {
        decide: async () => V2,
        decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }),
      },
      clock: { now: () => now },
      agentAccess: grantingAccess((key) =>
        agentDefinition({
          agentKey: key,
          ...(agent !== undefined
            ? { engineProfile: agent.engineProfile }
            : {}),
        }),
      ),
      logger,
      executors: profileExecutor,
      engineProfiles: settings,
      modelAccess: testModelAccess(),
      codingEngine: engines(),
      groupAccess,
      workerFeatures: {
        live: async () => true,
        metrics: { withheld: () => undefined },
      } as never,
    }).submit({
      orgId: ORG,
      actorId: USER,
      ...(agent !== undefined ? { agentKey: 'agent-1' } : {}),
      authorization: 'Bearer t',
      durableRequested: true,
      idempotencyKey: `k-${String(now)}`,
      deadlineMs: 60_000,
      input: {
        query: 'q',
        recordIds: [],
        filters: {},
        attachments: [],
        model:
          agent !== undefined
            ? {
                chatMode: 'auto',
                modelKey: 'model-1',
                modelName: 'gpt-x',
                modelProvider: 'openAI',
              }
            : { chatMode: 'agent' },
      },
    });
    if (outcome.route !== 'durable')
      return expect.fail(
        `expected a durable run, got ${JSON.stringify(outcome)}`,
      );
    const run = outcome.admission.run;
    const events = await store.journal.readStream(ORG, run.sessionId, 0, 100);
    const claimed = await execution.claim({
      orgId: ORG,
      runId: run.runId,
      workerId: 'w',
      attemptId: `attempt-${run.runId}`,
      leaseDurationMs: 60_000,
    });
    return {
      run: claimed.run,
      spec: runSpecSchema.parse(claimed.pinned?.spec),
      notices: events
        .filter((event) => event.type === 'x.pipeshub.run.notice')
        .map((event) => event.data),
    };
  };

  const children = () =>
    new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      logger,
      engineAuthority: childEngineAuthority(settings),
      engineAccess: (subject) => testModelAccess().decide(subject),
      engineEligibility: eligibility(),
    });

  const spawnCodeTask = (run: HarnessRun) =>
    children().spawn(
      {
        orgId: ORG,
        runId: run.runId,
        workerId: 'w',
        attemptId: `attempt-${run.runId}`,
        fence: run.fence,
      },
      {
        invocationId: 't1.0',
        goal: 'research the two repositories',
        grants: {},
        executor: { kind: 'engine' },
      },
    );

  beforeEach(async () => {
    now = 1_000;
    lines = [];
    groupsDeny = new Set();
    preferences = new InMemoryCodingEnginePreferences();
    settings = new FakeEngineCeilings();
    store = new InMemoryExecutionStore({ now: () => now });
    execution = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
      engineEligibility: eligibility(),
      logger: logger as never,
    });
  });

  describe('EngineEligibility, the one policy', () => {
    it('EO-1: no ceiling: every native engine is on, an ACP engine only when the ceiling names it', async () => {
      expect(
        await eligibility().check(ORG, USER, { kind: 'claude_code' }),
      ).to.equal(null);
      expect(
        await eligibility().check(ORG, USER, { kind: 'acp-gemini' }),
      ).to.equal('engine_off');
      expect(await eligibility().enabledKinds(ORG, USER)).to.deep.equal([
        'opencode',
        'codex',
        'claude_code',
      ]);
    });

    it('EO-1b: with no org ceiling every release engine is on, an ACP engine too; a ceiling that lists engines still holds', async () => {
      const all = [...new Set(RELEASE_ENGINE_PROFILES.map((p) => p.engine.kind))];
      for (const ceilings of [noEngineCeilings, null]) {
        const open = new EngineEligibility({ ceilings, groupAccess, logger });
        expect(await open.check(ORG, USER, { kind: 'acp-gemini' })).to.equal(null);
        expect(await open.enabledKinds(ORG, USER)).to.deep.equal(all);
      }
      expect(ceilingAllows({ engines: ['opencode'] }, { kind: 'acp-gemini' })).to.equal(false);
      expect(ceilingAllows({ mode: 'plan' }, { kind: 'acp-gemini' })).to.equal(true);
      expect(ceilingAllows({ mode: 'plan' }, { kind: 'acp-gemini' }, 'org')).to.equal(false);
    });

    it('EO-2: an engine the ceiling leaves out is off, by kind or kind@version; a group the person is not in is too', async () => {
      await settings.write(ORG, CLAUDE_CODE_OFF, 'p_admin');
      expect(
        await eligibility().check(ORG, USER, {
          kind: 'claude_code',
          version: '2.1.294',
        }),
      ).to.equal('engine_off');
      expect(await eligibility().check(ORG, USER, { kind: 'codex' })).to.equal(
        null,
      );
      expect(await eligibility().enabledKinds(ORG, USER)).to.deep.equal([
        'opencode',
        'codex',
      ]);
      await settings.write(ORG, { engines: ['opencode@1.18.34'] }, 'p_admin');
      expect(
        await eligibility().check(ORG, USER, {
          kind: 'opencode',
          version: '1.18.34',
        }),
      ).to.equal(null);
      expect(
        await eligibility().check(ORG, USER, {
          kind: 'opencode',
          version: '0.0.1',
        }),
      ).to.equal('engine_off');
      await settings.write(ORG, null, 'p_admin');
      groupsDeny.add('codex');
      expect(await eligibility().check(ORG, USER, { kind: 'codex' })).to.equal(
        'engine_group_required',
      );
    });

    it('EO-3: a ceiling that cannot be read is no answer: the check throws, so nothing starts on a guess', async () => {
      const down = new EngineEligibility({
        ceilings: { read: () => Promise.reject(new Error('kv down')) },
      });
      let thrown: unknown;
      try {
        await down.check(ORG, USER, { kind: 'codex' });
      } catch (error) {
        thrown = error;
      }
      expect(thrown).to.have.property('name', 'HarnessUnavailableError');
    });

    it('EO-4: a turn without code tools because its engine is off reads as engine_off, a closed reason the chat words', () => {
      expect(codeToolsWithheld('engine_off')).to.equal('engine_off');
      expect(CODE_TOOLS_WITHHELD).to.include('engine_off');
    });
  });

  describe("the default chat: the person's or the org's engine is off", () => {
    it('EO-5: the person chose Claude Code, the admin turned it off: the turn hands off to an engine that is on, and says so', async () => {
      await preferences.writeUser(ORG, USER, 'claude_code');
      await settings.write(ORG, CLAUDE_CODE_OFF, 'p_admin');
      const { spec, notices } = await admit();
      expect(codeTaskEngine(spec)).to.equal('opencode');
      expect(notices).to.deep.include({
        code: 'engine_switched',
        item: 'claude_code',
        engine: 'opencode',
      });
      expect(
        lines.find(([event]) => event === 'run.coding_engine_moved')?.[1],
      ).to.include({
        from: 'claude_code',
        engine: 'opencode',
        reason: 'engine_off',
      });
    });

    it("EO-6: the org's default is preferred over release order when it is on", async () => {
      await preferences.writeOrg(ORG, 'codex');
      await preferences.writeUser(ORG, USER, 'claude_code');
      await settings.write(ORG, CLAUDE_CODE_OFF, 'p_admin');
      const { spec, notices } = await admit();
      expect(codeTaskEngine(spec)).to.equal('codex');
      expect(notices).to.deep.include({
        code: 'engine_switched',
        item: 'claude_code',
        engine: 'codex',
      });
    });

    it('EO-7: the org default itself off moves too; an engine the person may not use (G-41) is never the fallback', async () => {
      await preferences.writeOrg(ORG, 'claude_code');
      await settings.write(ORG, CLAUDE_CODE_OFF, 'p_admin');
      groupsDeny.add('opencode');
      const { spec, notices } = await admit();
      expect(codeTaskEngine(spec)).to.equal('codex');
      expect(notices).to.deep.include({
        code: 'engine_switched',
        item: 'claude_code',
        engine: 'codex',
      });
    });

    it('EO-8: no engine left on: the turn answers without code tools and the chat says the engine is off', async () => {
      await preferences.writeUser(ORG, USER, 'claude_code');
      await settings.write(ORG, { engines: ['claude_code'] }, 'p_admin');
      groupsDeny.add('claude_code');
      const { spec, notices } = await admit();
      expect(codeTaskEngine(spec)).to.equal(undefined);
      expect(notices).to.deep.include({
        code: 'ran_without_code_tools',
        item: 'not_allowed',
      });
      // The person chose Codex, which is off, and the one engine on is one their groups do not hold.
      await preferences.writeUser(ORG, USER, 'codex');
      const off = await admit();
      expect(codeTaskEngine(off.spec)).to.equal(undefined);
      expect(off.notices).to.deep.include({
        code: 'ran_without_code_tools',
        item: 'engine_off',
      });
    });

    it('EO-9: an engine that is on is used as chosen, with no notice', async () => {
      await preferences.writeUser(ORG, USER, 'claude_code');
      const { spec, notices } = await admit();
      expect(codeTaskEngine(spec)).to.equal('claude_code');
      expect(notices).to.deep.equal([]);
    });
  });

  describe('an agent whose own engine is off', () => {
    it('EO-10: never moved outside the profile its owner set: no code tools, and the chat says the engine is off', async () => {
      await settings.write(ORG, CLAUDE_CODE_OFF, 'p_admin');
      const { spec, notices } = await admit({
        engineProfile: { engines: ['claude_code'] },
      });
      expect(codeTaskEngine(spec)).to.equal(undefined);
      expect(notices).to.deep.equal([
        { code: 'ran_without_code_tools', item: 'engine_off' },
      ]);
      expect(
        lines.find(([event]) => event === 'run.code_task_withheld')?.[1],
      ).to.include({ reason: 'engine_off' });
    });

    it('EO-11: a profile listing another engine that is on uses that one', async () => {
      await settings.write(ORG, CLAUDE_CODE_OFF, 'p_admin');
      const { spec, notices } = await admit({
        engineProfile: { engines: ['claude_code', 'codex'] },
      });
      expect(codeTaskEngine(spec)).to.equal('codex');
      expect(notices).to.deep.equal([]);
    });
  });

  describe('a run pinned before the switch starts no new engine session', () => {
    it('EO-12: the code_task child of a turn admitted on Claude Code is refused once Claude Code is off', async () => {
      const { run, spec } = await admit({
        engineProfile: { engines: ['claude_code'] },
      });
      expect(codeTaskEngine(spec)).to.equal('claude_code');
      await settings.write(ORG, CLAUDE_CODE_OFF, 'p_admin');
      let thrown: unknown;
      try {
        await spawnCodeTask(run);
      } catch (error) {
        thrown = error;
      }
      expect(thrown).to.have.property('code', 'ENGINE_OFF');
      expect(
        lines.find(([event]) => event === 'child.refused')?.[1],
      ).to.include({ reason: 'ENGINE_OFF' });
      const events = await store.journal.readStream(ORG, run.sessionId, 0, 100);
      expect(events.map((event) => event.type)).to.not.include('child.spawned');
    });

    it("EO-13: a limits change is not a switch: the child keeps its parent's pinned engine (C6-3)", async () => {
      const { run } = await admit({
        engineProfile: { engines: ['claude_code'] },
      });
      await settings.write(ORG, { limits: { maxSteps: 5 } }, 'p_admin');
      const spawned = await spawnCodeTask(run);
      const child = await store.readClaimSubject(ORG, spawned.childRunId);
      const childSpec = runSpecSchema.parse(child?.pinned?.spec);
      if (childSpec.executor?.kind !== 'engine')
        return expect.fail('expected an engine child');
      // The chat's "Delegating to …" names the parent's pinned engine: the child runs exactly that one.
      expect(childSpec.executor.engine.kind).to.equal('claude_code');
    });

    it('EO-14: an engine child pinned before the switch is not claimed after it: it fails engine_off, no session starts', async () => {
      const { run } = await admit({
        engineProfile: { engines: ['claude_code'] },
      });
      const spawned = await spawnCodeTask(run);
      await settings.write(ORG, CLAUDE_CODE_OFF, 'p_admin');
      let thrown: unknown;
      try {
        await execution.claim({
          orgId: ORG,
          runId: spawned.childRunId,
          workerId: 'engine-w',
          attemptId: 'engine-a1',
          leaseDurationMs: 60_000,
        });
      } catch (error) {
        thrown = error;
      }
      expect(thrown).to.have.property('code', 'RUN_NOT_CLAIMABLE');
      const child = await store.get(ORG, spawned.childRunId);
      expect(child?.status).to.equal('failed');
      const childEvents = await store.journal.readStream(
        ORG,
        spawned.childRunId,
        0,
        100,
      );
      expect(
        childEvents.find((event) => event.type === 'run.failed')?.data,
      ).to.include({ errorType: 'engine_off' });
      expect(
        lines.find(([event]) => event === 'run.engine_off')?.[1],
      ).to.include({ engine: 'claude_code' });
    });
  });
});
