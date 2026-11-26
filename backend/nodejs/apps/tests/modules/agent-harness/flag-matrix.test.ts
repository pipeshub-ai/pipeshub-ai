import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import {
  PlatformHarnessRolloutSettings,
  PlatformScheduledRuns,
} from '../../../src/modules/agent-harness/adapters/configuration/platform-rollout-settings';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ScheduleService } from '../../../src/modules/agent-harness/application/schedule-service';
import { HarnessScheduleRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import {
  AGENT_HARNESS_ERASURE_WORKER,
  AGENT_HARNESS_RETENTION_SWEEPER,
  prepareAgentHarnessAuthority,
  prepareAgentHarnessStore,
  startAgentHarnessBackgroundServices,
  startSessionErasureServices,
} from '../../../src/modules/agent-harness/composition';
import {
  AgentHarnessEnvironment,
  recordJournalCapability,
} from '../../../src/modules/agent-harness/config';
import { DURABLE_CHAT_MODES } from '../../../src/modules/agent-harness/domain/rollout';
import * as platformSettings from '../../../src/modules/configuration_manager/utils/util';
import { HarnessCapabilityService } from '../../../src/modules/agent-harness/application/capability-service';
import {
  ENV_FLAG_DEFAULTS,
  envFlag,
  InvalidEnvFlagError,
  validateEnvFlags,
} from '../../../src/libs/utils/env-flags';
import { readFileSync } from 'node:fs';
import {
  createApprovalsStore,
  createEngineProfilesStore,
  createSessionRetentionStore,
} from '../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { join } from 'node:path';
import {
  flagMatrix,
  matrixFlag,
  matrixReason,
} from './support/flag-matrix';
import { testControlPlane } from '../control-plane/support/in-memory';
import { singleStoreOrgSettings } from '../../../src/modules/agent-harness/adapters/configuration/single-store-org-settings';

/**
 * F3 behaviour cases, Node half, with the expected defaults and reasons read
 * from contracts/harness/flags.json: FB-1 all off is today's product; FB-4 /
 * FB-5 durable chat (and deep mode) on with one dependency off falls back to
 * legacy with the matrix's reason; FB-8 the kill switch overrides the Labs
 * flag. Python's half: backend/python/tests/unit/harness/test_flag_matrix.py.
 */

const ENV_FLAGS = flagMatrix()
  .flags.filter((f) => f.kind === 'env')
  .map((f) => f.name);

/** Every env flag at its default: none of them set. */
const ALL_OFF: AgentHarnessEnvironment = {};

const READY: AgentHarnessEnvironment = {
  REPLICA_SET_AVAILABLE: 'true',
  AGENT_HARNESS_RELAY_ENABLED: 'true',
  AGENT_HARNESS_WORKER_ENABLED: 'true',
};

const logger = () => ({
  info: sinon.stub(),
  warn: sinon.stub(),
  error: sinon.stub(),
});

/** A key-value store holding no platform settings: every Labs flag at its registry default. */
const emptyStore = { get: async () => null } as never;

const rollout = (
  env: AgentHarnessEnvironment,
  featureFlags: Record<string, boolean> | null,
  modelFamilies: string[] | null = null,
) => {
  if (featureFlags !== null) {
    sinon.stub(platformSettings, 'getPlatformSettingsFromStore').resolves({
      fileUploadMaxSizeBytes: 1,
      featureFlags,
      agentHarness: { modelFamilies },
    });
  }
  return new HarnessRollout(
    new PlatformHarnessRolloutSettings(emptyStore, env),
    { resolve: sinon.stub().resolves('openai') },
  );
};

const decide = (policy: HarnessRollout, chatMode = 'auto') =>
  policy.decide({ orgId: 'org-1', model: { chatMode, modelKey: 'm-1' } });

const withProcessEnv = async (
  env: AgentHarnessEnvironment,
  body: () => Promise<void> | void,
): Promise<void> => {
  const saved = Object.fromEntries(ENV_FLAGS.map((n) => [n, process.env[n]]));
  for (const name of ENV_FLAGS) delete process.env[name];
  Object.assign(process.env, env);
  try {
    await body();
  } finally {
    for (const name of ENV_FLAGS) {
      if (saved[name] === undefined) delete process.env[name];
      else process.env[name] = saved[name];
    }
  }
};

describe('F3 flag matrix behaviour (Node)', () => {
  afterEach(() => {
    sinon.restore();
    recordJournalCapability(null);
  });

  describe('FB-1 all off is today\'s product', () => {
    it('admits nothing to the harness without a replica set, whatever the org would choose', async () => {
      // The real settings reader over an empty store (it hashes SECRET_KEY before reading).
      sinon.stub(process, 'env').value({ ...process.env, SECRET_KEY: 'a1784cd219d184dd5ae37aeeb9389d1b6839256086392f82941b55bff458b9df' });
      const decision = await decide(rollout(ALL_OFF, null));
      expect(decision.useHarness).to.equal(false);
      expect(decision.reason).to.equal(
        matrixReason('durable_chat', 'REPLICA_SET_AVAILABLE'),
      );
      expect(matrixFlag('AGENT_HARNESS_KILL_SWITCH').default).to.equal(false);
    });

    it('BETA: at the shipped defaults (a replica set, nothing else set, no Labs saved) the harness runs but admits no turn: Labs is off (ADR-OPS-06 as amended)', async () => {
      sinon.stub(process, 'env').value({ ...process.env, SECRET_KEY: 'a1784cd219d184dd5ae37aeeb9389d1b6839256086392f82941b55bff458b9df' });
      expect(matrixFlag('AGENT_HARNESS_WORKER_ENABLED').default).to.equal(true);
      expect(matrixFlag('ENABLE_AGENT_HARNESS').default).to.equal(false);
      const decision = await decide(rollout({ REPLICA_SET_AVAILABLE: 'true' }, null));
      expect([decision.useHarness, decision.reason]).to.deep.equal([false, 'org_disabled']);
    });

    it('R50-OFF: the opt-out (AGENT_HARNESS_WORKER_ENABLED=false) switches the harness off and chat takes the classic path, whatever Labs says', async () => {
      const off: AgentHarnessEnvironment = { REPLICA_SET_AVAILABLE: 'true', AGENT_HARNESS_WORKER_ENABLED: 'false' };
      const decision = await decide(rollout(off, { ENABLE_AGENT_HARNESS: true }));
      expect([decision.useHarness, decision.reason]).to.deep.equal([false, 'infrastructure_unavailable']);
    });

    it('DUR3-G2: with the org\'s Labs switch on (a replica set, nothing else set) agent chat is durable', async () => {
      expect(matrixFlag('AGENT_HARNESS_RELAY_ENABLED').default).to.equal(true);
      const shipped: AgentHarnessEnvironment = { REPLICA_SET_AVAILABLE: 'true' };
      const decision = await decide(rollout(shipped, { ENABLE_AGENT_HARNESS: true }));
      expect([decision.useHarness, decision.reason]).to.deep.equal([true, 'enabled']);
      // The settings reader stays stubbed with Labs on for the rest of the test.
      const assistant = await rollout(shipped, null).decide({
        orgId: 'org-1',
        model: { chatMode: 'internal_search', modelKey: 'm-1' },
        assistant: true,
      });
      expect(assistant.useHarness).to.equal(true);
      // The kill switch is the operator's env setting (CTL-5).
      const killed = await decide(rollout({ ...shipped, AGENT_HARNESS_KILL_SWITCH: 'true' }, null));
      expect([killed.useHarness, killed.reason]).to.deep.equal([false, 'kill_switch']);
    });

    it('prepares no store, provisions no authority and starts no background service', async () => {
      const refresh = sinon.stub().resolves();
      await prepareAgentHarnessStore(logger(), ALL_OFF, {
        refresh,
      } as unknown as HarnessCapabilityService);
      expect(refresh.called).to.equal(false);

      const untouched = new Container();
      const get = sinon.spy(untouched, 'get');
      await prepareAgentHarnessAuthority(untouched, logger(), ALL_OFF);
      expect(get.called).to.equal(false);

      await withProcessEnv(ALL_OFF, () => {
        startAgentHarnessBackgroundServices(untouched);
      });
      expect(get.called).to.equal(false);
    });

    it('still erases conversations: erasure is always on', async () => {
      expect(
        flagMatrix().features.find((f) => f.name === 'session_erasure')
          ?.alwaysOn,
      ).to.equal(true);
      const container = new Container();
      const worker = { start: sinon.stub() };
      const sweeper = { start: sinon.stub() };
      container.bind(AGENT_HARNESS_ERASURE_WORKER).toConstantValue(worker);
      container.bind(AGENT_HARNESS_RETENTION_SWEEPER).toConstantValue(sweeper);
      await withProcessEnv(ALL_OFF, () =>
        startSessionErasureServices(
          container,
          logger(),
          sinon.stub().resolves({ created: [] }),
        ),
      );
      expect(worker.start.calledOnce).to.equal(true);
      expect(sweeper.start.calledOnce).to.equal(true);
    });
  });

  describe('FB-4 durable chat on, then each dependency off in turn', () => {
    const ON = { ENABLE_AGENT_HARNESS: true, AGENT_HARNESS_KILL_SWITCH: false };

    it('admits with every dependency on', async () => {
      const decision = await decide(rollout(READY, ON));
      expect(decision).to.include({ useHarness: true, reason: 'enabled' });
    });

    for (const name of Object.keys(READY)) {
      it(`${name} off → legacy with the matrix's reason`, async () => {
        const env = { ...READY, [name]: 'false' };
        const decision = await decide(rollout(env, ON));
        expect(decision).to.include({
          useHarness: false,
          reason: matrixReason('durable_chat', name),
        });
      });
    }

    it('a non-durable journal (standalone, PSA) → legacy with the matrix\'s reason', async () => {
      recordJournalCapability({ durable: false, reason: 'standalone' });
      const decision = await decide(rollout(READY, ON));
      expect(decision.reason).to.equal(
        matrixReason('durable_chat', 'journal_durable'),
      );
    });

    it('the org\'s Labs flag off → legacy with the matrix\'s reason', async () => {
      const decision = await decide(
        rollout(READY, { ...ON, ENABLE_AGENT_HARNESS: false }),
      );
      expect(decision.reason).to.equal(
        matrixReason('durable_chat', 'ENABLE_AGENT_HARNESS'),
      );
    });

    it('an empty model-family allowlist → legacy with the matrix\'s reason', async () => {
      const decision = await decide(rollout(READY, ON, []));
      expect(decision.reason).to.equal(
        matrixReason('durable_chat', 'agentHarness.modelFamilies'),
      );
    });
  });

  describe('FB-5 deep mode follows durable chat and the chat mode', () => {
    const ON = { ENABLE_AGENT_HARNESS: true };

    it('deep is a durable chat mode and runs durably when the org is on', async () => {
      expect(DURABLE_CHAT_MODES).to.include('deep');
      expect((await decide(rollout(READY, ON), 'deep')).useHarness).to.equal(
        true,
      );
    });

    it('deep with the org flag off → legacy (org_disabled)', async () => {
      const decision = await decide(
        rollout(READY, { ENABLE_AGENT_HARNESS: false }),
        'deep',
      );
      expect(decision.reason).to.equal(
        matrixReason('durable_chat', 'ENABLE_AGENT_HARNESS'),
      );
    });

    it('planExecute → legacy with the matrix\'s reason', async () => {
      const decision = await decide(rollout(READY, ON), 'planExecute');
      expect(decision.reason).to.equal(
        matrixReason('durable_chat', 'chatMode'),
      );
    });
  });

  describe('FB-8 the kill switch overrides the Labs flag', () => {
    it('kill switch on with the org flag on → legacy (kill_switch); runs in flight stay reachable', async () => {
      expect(matrixFlag('AGENT_HARNESS_KILL_SWITCH').killSwitchFor).to.equal(
        'ENABLE_AGENT_HARNESS',
      );
      const policy = rollout({ ...READY, AGENT_HARNESS_KILL_SWITCH: 'true' }, {
        ENABLE_AGENT_HARNESS: true,
      });
      expect((await decide(policy)).reason).to.equal(
        matrixReason('durable_chat', 'AGENT_HARNESS_KILL_SWITCH'),
      );
      expect(await policy.decideForOrg('org-1')).to.include({
        useHarness: false,
        resumeInFlightRuns: true,
      });
    });
  });

  describe('FM-F5 one boolean parse for every Node env flag', () => {
    const names = Object.keys(ENV_FLAG_DEFAULTS) as Array<
      keyof typeof ENV_FLAG_DEFAULTS
    >;

    for (const name of names) {
      it(`${name} accepts 1/true/yes/on and 0/false/no/off in any case`, () => {
        for (const raw of ['1', 'TRUE', ' yes ', 'On']) {
          expect(envFlag(name, { [name]: raw }), raw).to.equal(true);
        }
        for (const raw of ['0', 'False', 'no', 'OFF']) {
          expect(envFlag(name, { [name]: raw }), raw).to.equal(false);
        }
      });

      it(`${name} unset or blank is the matrix default`, () => {
        expect(envFlag(name, {})).to.equal(matrixFlag(name).default);
        expect(envFlag(name, { [name]: '' })).to.equal(matrixFlag(name).default);
      });

      it(`${name} malformed is a typed error naming the flag, also at startup (hardened profile)`, () => {
        const hardened = { PIPESHUB_HARDENED: 'true', [name]: 'enabled' };
        expect(() => envFlag(name, hardened))
          .to.throw(InvalidEnvFlagError)
          .with.property('flag', name);
        expect(() => validateEnvFlags(hardened)).to.throw(
          InvalidEnvFlagError,
          name,
        );
      });

      it(`R51-COMPAT: ${name} malformed starts on the compatibility profile, read as before`, () => {
        const legacy = name === 'REPLICA_SET_AVAILABLE' ? false : matrixFlag(name).default;
        expect(envFlag(name, { [name]: 'enabled' })).to.equal(legacy);
        expect(() => validateEnvFlags({ [name]: 'enabled' })).not.to.throw();
      });
    }

    it('the process validates the flags before it loads the application', () => {
      const index = readFileSync(join(__dirname, '../../../src/index.ts'), 'utf8');
      const validated = index.indexOf('validateEnvFlags();');
      expect(validated).to.be.greaterThan(index.indexOf('config();'));
      expect(validated).to.be.lessThan(
        index.indexOf("import { Application } from './app';"),
      );
    });
  });

  describe('FM-G3 a key-value store outage is never read as an absent document (Node)', () => {
    const down = {
      get: () => Promise.reject(new Error('kv down')),
      compareAndSet: () => Promise.reject(new Error('kv down')),
    } as never;
    const cipher = { encrypt: (v: string) => v, decrypt: (v: string) => v };

    for (const [name, create] of [
      ['approvals', createApprovalsStore],
      ['engineProfiles', createEngineProfilesStore],
      ['sessionRetention', createSessionRetentionStore],
    ] as const) {
      it(`${name}: the read rejects instead of answering "no document"`, async () => {
        let refused = false;
        try {
          await create(down, cipher).read();
        } catch {
          refused = true;
        }
        expect(refused).to.equal(true);
      });
    }

    it('rollout: the admission is refused, not sent to legacy as "org off"', async () => {
      sinon
        .stub(platformSettings, 'getPlatformSettingsFromStore')
        .rejects(new Error('kv down'));
      let refused = false;
      try {
        await decide(rollout(READY, null));
      } catch {
        refused = true;
      }
      expect(refused).to.equal(true);
    });
  });

  describe('CF-3 scheduled runs (P8a) follow the matrix', () => {
    const settings = (featureFlags: Record<string, boolean>) =>
      sinon.stub(platformSettings, 'getPlatformSettingsFromStore').resolves({
        fileUploadMaxSizeBytes: 1,
        featureFlags,
        agentHarness: { modelFamilies: null },
      });

    it('the Labs flag absent is the matrix default (off); on when set', async () => {
      settings({});
      expect(await new PlatformScheduledRuns(singleStoreOrgSettings(emptyStore)).enabled('org-1')).to.equal(
        matrixFlag('ENABLE_SCHEDULED_RUNS').default,
      );
      sinon.restore();
      settings({ ENABLE_SCHEDULED_RUNS: true });
      expect(await new PlatformScheduledRuns(singleStoreOrgSettings(emptyStore)).enabled('org-1')).to.equal(true);
    });

    it('off (or unreadable): every schedule request but delete is refused with the matrix reason', async () => {
      sinon.stub(platformSettings, 'getPlatformSettingsFromStore').rejects(new Error('kv down'));
      const store = new InMemoryScheduleStore();
      const service = new ScheduleService({ transaction: testControlPlane(),
        store,
        firer: { runNow: sinon.stub(), deliver: sinon.stub() } as never,
        actors: { isOrgAdmin: async () => true },
        setting: new PlatformScheduledRuns(singleStoreOrgSettings(emptyStore)),
      });
      const caller = { orgId: 'org-1', userId: 'u1', via: 'session' as const };
      const refusals: string[] = [];
      for (const call of [
        () => service.list(caller, 10),
        () => service.create(caller, { name: 'n', input: { query: 'q' }, trigger: { kind: 'webhook' } }),
      ]) {
        try {
          await call();
        } catch (error) {
          refusals.push((error as HarnessScheduleRefusedError).code.toLowerCase());
        }
      }
      expect(refusals).to.deep.equal([
        matrixReason('scheduled_runs', 'ENABLE_SCHEDULED_RUNS'),
        matrixReason('scheduled_runs', 'ENABLE_SCHEDULED_RUNS'),
      ]);
    });
  });
});
