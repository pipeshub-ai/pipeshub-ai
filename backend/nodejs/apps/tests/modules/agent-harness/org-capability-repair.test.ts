/**
 * ADR-OPS-08: an org's control-plane grants catch up when its harness turns on. While the harness is off for an org,
 * the query service's CPD-7 dual write sends nothing, so an org recorded as migrated keeps stale grants for agents
 * changed meanwhile. A Labs save from off to on, and a start with the switch on, run the capability migration's
 * repair for the org in the background; a failure never holds the save and is retried on the org's next turn.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as cmConfig from '../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../src/libs/encryptor/encryptor';
import { setPlatformSettings } from '../../../src/modules/configuration_manager/controller/cm_controller';
import { CapabilityMigration } from '../../../src/modules/control-plane/application/capability-migration';
import type { CapabilityRepair, OrgReport } from '../../../src/modules/control-plane';
import { OrgCapabilityRepair } from '../../../src/modules/agent-harness/application/org-capability-repair';
import { orgHarnessTurnedOn, recordOrgHarnessTurnedOn } from '../../../src/modules/agent-harness/config';
import { InMemoryCapabilityLedger, InMemoryGrantStore, InMemoryInstallationStore } from '../control-plane/support/in-memory-stores';
import { auditedControlPlane } from '../control-plane/support/in-memory';
import { CUSTOM_MCP, LegacyFacts, legacyFacts, migrationDeps, type CapabilityWorld } from '../control-plane/support/capability-world';
import { ORG_A, ORG_B } from '../control-plane/support/cp-objects';
import { inRequestOrg } from '../../helpers/request-org-context';
import { randomUUID } from 'crypto';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { fixedRollout, HARNESS_ENABLED, KILL_SWITCH } from './support/rollout';

const SWITCH_ON = { AGENT_HARNESS_WORKER_ENABLED: 'true' };
const SWITCH_OFF = { AGENT_HARNESS_WORKER_ENABLED: 'false' };

/** Facts the plan holds nothing of, so a whole apply records the org as migrated. */
const wholeFacts = () => {
  const facts = legacyFacts();
  return {
    ...facts,
    toolsetInstances: facts.toolsetInstances.filter((instance) => instance.toolsetType !== 'no-such-toolset'),
    agents: facts.agents.map((agent) => ({ ...agent, toolsets: agent.toolsets.filter((toolset) => toolset.instanceId !== null) })),
  };
};

const inMemoryWorld = (): CapabilityWorld & { entries: (orgId: string) => number } => {
  const installations = new InMemoryInstallationStore();
  const grants = new InMemoryGrantStore();
  const controlPlane = auditedControlPlane();
  return {
    installations,
    grants,
    ledger: new InMemoryCapabilityLedger(installations, grants),
    transaction: controlPlane.transaction,
    configEntries: async (orgId) => controlPlane.configEntries(orgId).length,
    entries: (orgId) => controlPlane.configEntries(orgId).length,
  };
};

class CasStore {
  value: string | null = null;
  async get<T>(): Promise<T | null> {
    return this.value as T | null;
  }
  async compareAndSet<T>(_key: string, expected: T | null, next: T): Promise<boolean> {
    if (this.value !== expected) return false;
    this.value = next as unknown as string;
    return true;
  }
  store(document: Record<string, unknown>): void {
    this.value = `encrypted:${JSON.stringify(document)}`;
  }
}

class Logger {
  readonly lines: Array<[string, string, Record<string, unknown> | undefined]> = [];
  debug(): void {}
  info(message: string, meta?: Record<string, unknown>): void {
    this.lines.push(['info', message, meta]);
  }
  warn(message: string, meta?: Record<string, unknown>): void {
    this.lines.push(['warn', message, meta]);
  }
  error(message: string, meta?: Record<string, unknown>): void {
    this.lines.push(['error', message, meta]);
  }
}

const report = (orgId: string, failed: string | null = null): OrgReport => ({
  orgId,
  mode: 'apply',
  created: 0,
  updated: 0,
  unchanged: 0,
  removed: 0,
  reverted: 0,
  holds: [],
  drifted: [],
  failed,
  migrated: true,
});

describe('ADR-OPS-08: control-plane grants catch up when an org turns the harness on', () => {
  let kv: CasStore;

  beforeEach(() => {
    kv = new CasStore();
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'aes-256-gcm', secretKey: 'a'.repeat(64) } as never);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (value: string) => `encrypted:${value}`,
      decrypt: (value: string) => value.replace('encrypted:', ''),
    } as never);
  });
  afterEach(() => {
    recordOrgHarnessTurnedOn(null);
    sinon.restore();
  });

  const save = async (body: Record<string, unknown>, env: Record<string, string> = SWITCH_ON) => {
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis(), end: sinon.stub() };
    const next = sinon.stub();
    await inRequestOrg(setPlatformSettings(kv as never, auditedControlPlane().transaction, env))(
      { body, user: { userId: 'u-admin', orgId: ORG_A }, headers: {} } as never,
      res as never,
      next,
    );
    return { res, next };
  };

  const labs = (on: boolean) => kv.store({ featureFlagsVersion: 2, featureFlags: { ENABLE_AGENT_HARNESS: on } });

  describe('the Labs save', () => {
    it('Labs off, an agent edited, Labs on: the agent grant matches the agent as it is now', async () => {
      const world = inMemoryWorld();
      const source = new LegacyFacts();
      source.orgs.set(ORG_A, wholeFacts());
      const migration = new CapabilityMigration(migrationDeps(world, source));
      expect((await migration.runFor('apply', [ORG_A]))[0]).to.include({ failed: null, migrated: true });
      expect((await world.grants.get(ORG_A, 'agent:a8'))?.entries).to.deep.equal([{ installation: CUSTOM_MCP, tools: ['ping'], required: false }]);

      // Labs off: the agent is edited and the query service asks for no sync.
      labs(false);
      const facts = wholeFacts();
      source.orgs.set(ORG_A, {
        ...facts,
        agents: facts.agents.map((agent) =>
          agent.agentKey === 'a8' ? { ...agent, mcpServers: [{ instanceId: CUSTOM_MCP, tools: ['ping', 'pong'] }] } : agent,
        ),
      });

      const repair = new OrgCapabilityRepair({ migration, orgs: async () => [ORG_A], harnessOn: async () => true });
      const repaired: Array<Promise<boolean>> = [];
      recordOrgHarnessTurnedOn((orgId) => repaired.push(repair.request(orgId)));
      const { res, next } = await save({ featureFlags: { ENABLE_AGENT_HARNESS: true } });
      expect(next.called).to.equal(false);
      expect(res.status.calledWith(200)).to.equal(true);
      expect(repaired).to.have.length(1);
      expect(await repaired[0]).to.equal(true);
      expect((await world.grants.get(ORG_A, 'agent:a8'))?.entries).to.deep.equal([{ installation: CUSTOM_MCP, tools: ['ping', 'pong'], required: false }]);
    });

    it('only the edge from off to on asks for it, and only with the switch on', async () => {
      const turnedOn: string[] = [];
      recordOrgHarnessTurnedOn((orgId) => turnedOn.push(orgId));
      labs(true);
      await save({ featureFlags: { ENABLE_AGENT_HARNESS: true } });
      labs(true);
      await save({ featureFlags: { ENABLE_BETA_CONNECTORS: true } });
      labs(false);
      await save({ featureFlags: { ENABLE_BETA_CONNECTORS: true } });
      labs(true);
      await save({ featureFlags: { ENABLE_AGENT_HARNESS: false } });
      expect(turnedOn).to.deep.equal([]);
      labs(false);
      await save({ featureFlags: { ENABLE_AGENT_HARNESS: true } });
      expect(turnedOn).to.deep.equal([ORG_A]);
    });

    it('a save on an install with the switch off asks for nothing (it is refused before it lands)', async () => {
      const turnedOn: string[] = [];
      recordOrgHarnessTurnedOn((orgId) => turnedOn.push(orgId));
      labs(false);
      const { next } = await save({ featureFlags: { ENABLE_AGENT_HARNESS: true } }, SWITCH_OFF);
      expect(next.firstCall.args[0].statusCode).to.equal(400);
      expect(turnedOn).to.deep.equal([]);
    });

    it('stays fast when the repair hangs or fails: the save answers before it, and a throwing listener changes nothing', async () => {
      const hanging: CapabilityRepair = { repair: () => new Promise<OrgReport | null>(() => undefined) };
      const repair = new OrgCapabilityRepair({ migration: hanging, orgs: async () => [], harnessOn: async () => true });
      recordOrgHarnessTurnedOn((orgId) => {
        void repair.request(orgId);
      });
      labs(false);
      const started = Date.now();
      const { res, next } = await save({ featureFlags: { ENABLE_AGENT_HARNESS: true } });
      expect(Date.now() - started).to.be.below(1_000);
      expect(next.called).to.equal(false);
      expect(res.status.calledWith(200)).to.equal(true);

      recordOrgHarnessTurnedOn(() => {
        throw new Error('listener broke');
      });
      labs(false);
      const second = await save({ featureFlags: { ENABLE_AGENT_HARNESS: true } });
      expect(second.next.called).to.equal(false);
      expect(second.res.status.calledWith(200)).to.equal(true);
      expect(() => orgHarnessTurnedOn(ORG_A)).to.not.throw();
    });
  });

  describe('the repair', () => {
    it('is idempotent: a second repair, and one of an org already in line, writes nothing', async () => {
      const world = inMemoryWorld();
      const source = new LegacyFacts();
      source.orgs.set(ORG_A, wholeFacts());
      const migration = new CapabilityMigration(migrationDeps(world, source));
      await migration.runFor('apply', [ORG_A]);
      const entries = world.entries(ORG_A);
      const first = await migration.repair(ORG_A);
      expect(first).to.include({ created: 0, updated: 0, removed: 0, failed: null, migrated: true });
      const second = await migration.repair(ORG_A);
      expect(second).to.deep.equal(first);
      expect(world.entries(ORG_A)).to.equal(entries);
    });

    it('leaves an org never recorded as migrated alone: its first apply stays the operator\'s', async () => {
      const world = inMemoryWorld();
      const source = new LegacyFacts();
      source.orgs.set(ORG_A, wholeFacts());
      const migration = new CapabilityMigration(migrationDeps(world, source));
      expect(await migration.repair(ORG_A)).to.equal(null);
      expect(source.reads).to.equal(0);
      expect(world.entries(ORG_A)).to.equal(0);
      expect(await world.ledger.migrated(ORG_A)).to.equal(false);
    });

    it('a failure is logged and the org\'s next turn retries, at most once per interval', async () => {
      let now = 1_000;
      let fail = true;
      const calls: string[] = [];
      const migration: CapabilityRepair = {
        repair: async (orgId) => {
          calls.push(orgId);
          return fail ? report(orgId, 'legacy_source_unavailable') : report(orgId);
        },
      };
      const logger = new Logger();
      const repair = new OrgCapabilityRepair({ migration, orgs: async () => [], harnessOn: async () => true, logger, now: () => now, retryAfterMs: 500 });
      expect(await repair.request(ORG_A)).to.equal(false);
      expect(logger.lines).to.deep.equal([['warn', 'control_plane.capability_repair_failed', { orgId: ORG_A, reasonCode: 'legacy_source_unavailable' }]]);
      repair.noteTurn(ORG_A);
      repair.noteTurn(ORG_B);
      expect(calls).to.deep.equal([ORG_A]);
      now += 500;
      fail = false;
      repair.noteTurn(ORG_A);
      await repair.stop();
      expect(calls).to.deep.equal([ORG_A, ORG_A]);
      repair.noteTurn(ORG_A);
      await repair.stop();
      expect(calls).to.have.length(2);
    });

    it('a repair that throws is a failure like one that reports it, and never rejects its caller', async () => {
      const logger = new Logger();
      const migration: CapabilityRepair = {
        repair: async () => {
          throw Object.assign(new Error('down'), { reason: 'cp_unavailable' });
        },
      };
      const repair = new OrgCapabilityRepair({ migration, orgs: async () => [], harnessOn: async () => true, logger });
      expect(await repair.request(ORG_A)).to.equal(false);
      expect(logger.lines[0]![2]).to.deep.equal({ orgId: ORG_A, reasonCode: 'cp_unavailable' });
      const anonymous = new OrgCapabilityRepair({
        migration: { repair: () => Promise.reject(new Error('x')) },
        orgs: async () => [],
        harnessOn: async () => true,
        logger,
      });
      expect(await anonymous.request(ORG_A)).to.equal(false);
      expect(logger.lines[1]![2]).to.deep.equal({ orgId: ORG_A, reasonCode: 'capability_repair_failed' });
    });

    it('joins a repair under way instead of starting a second', async () => {
      let release!: () => void;
      let calls = 0;
      const migration: CapabilityRepair = {
        repair: (orgId) => {
          calls += 1;
          return new Promise((resolve) => {
            release = () => resolve(report(orgId));
          });
        },
      };
      const repair = new OrgCapabilityRepair({ migration, orgs: async () => [], harnessOn: async () => true });
      const first = repair.request(ORG_A);
      const second = repair.request(ORG_A);
      expect(second).to.equal(first);
      release();
      expect(await first).to.equal(true);
      expect(calls).to.equal(1);
    });

    it('logs what a repair caught up, and nothing when there was nothing to do', async () => {
      const logger = new Logger();
      const migration: CapabilityRepair = { repair: async (orgId) => (orgId === ORG_A ? { ...report(orgId), updated: 2 } : report(orgId)) };
      const repair = new OrgCapabilityRepair({ migration, orgs: async () => [], harnessOn: async () => true, logger });
      await repair.request(ORG_A);
      await repair.request(ORG_B);
      expect(logger.lines).to.deep.equal([['info', 'control_plane.capability_repaired', { orgId: ORG_A, created: 0, updated: 2, removed: 0 }]]);
    });
  });

  describe('the start', () => {
    it('repairs every org whose harness is on, one at a time, after its delay', async () => {
      const clock = sinon.useFakeTimers();
      try {
        const calls: string[] = [];
        const migration: CapabilityRepair = {
          repair: async (orgId) => {
            calls.push(orgId);
            return report(orgId);
          },
        };
        const repair = new OrgCapabilityRepair({
          migration,
          orgs: async () => [ORG_A, ORG_B, 'org-unreadable'],
          harnessOn: async (orgId) => {
            if (orgId === 'org-unreadable') throw new Error('settings down');
            return orgId === ORG_A;
          },
        });
        repair.start(10_000);
        repair.start(10_000);
        await clock.tickAsync(9_999);
        expect(calls).to.deep.equal([]);
        await clock.tickAsync(1);
        await repair.stop();
        expect(calls).to.deep.equal([ORG_A]);
      } finally {
        clock.restore();
      }
    });

    it('a stop before the delay runs nothing; an org list that cannot be read is logged', async () => {
      const calls: string[] = [];
      const logger = new Logger();
      const migration: CapabilityRepair = {
        repair: async (orgId) => {
          calls.push(orgId);
          return report(orgId);
        },
      };
      const stopped = new OrgCapabilityRepair({ migration, orgs: async () => [ORG_A], harnessOn: async () => true });
      stopped.start(60_000);
      await stopped.stop();
      expect(calls).to.deep.equal([]);

      const unreadable = new OrgCapabilityRepair({
        migration,
        orgs: async () => {
          throw new TypeError('mongo down');
        },
        harnessOn: async () => true,
        logger,
      });
      await unreadable.repairOrgsOn();
      expect(logger.lines).to.deep.equal([['warn', 'control_plane.capability_repair_failed', { reasonCode: 'orgs_unavailable', errorType: 'TypeError' }]]);
      expect(calls).to.deep.equal([]);
    });
  });

  describe('admission', () => {
    const submit = (service: RunAdmissionService) =>
      service.submit({
        orgId: ORG_A,
        actorId: 'u1',
        idempotencyKey: randomUUID(),
        deadlineMs: 9_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'internal_search' } },
      });

    it('tells the repair of every turn the harness takes, and of none it leaves to the classic path', async () => {
      const turns: string[] = [];
      const capabilityRepair = { noteTurn: (orgId: string) => turns.push(orgId) };
      const durable = await submit(
        new RunAdmissionService({
          store: new InMemoryExecutionStore(),
          rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
          capabilityRepair,
        }),
      );
      expect(durable.route).to.equal('durable');
      expect(turns).to.deep.equal([ORG_A]);
      const legacy = await submit(
        new RunAdmissionService({ store: new InMemoryExecutionStore(), rollout: fixedRollout(() => KILL_SWITCH), capabilityRepair }),
      );
      expect(legacy.route).to.equal('legacy');
      expect(turns).to.deep.equal([ORG_A]);
    });
  });
});
