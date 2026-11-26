/**
 * CPD-7 wiring (WP-8 binders): the agent authority admission asks is the
 * control plane's reviewed one; the sync is bound where the host binds its
 * configuration; `CONTROL_PLANE_PIN` is read once, and a bad value refuses
 * to start (SH-1).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { ControlPlaneReviewedAgentAccess } from '../../../src/modules/agent-harness/application/control-plane-agent-access';
import { bindControlPlaneCapabilities } from '../../../src/modules/agent-harness/binders/control-plane-capabilities';
import { AGENT_HARNESS_AGENT_ACCESS, CONTROL_PLANE_CAPABILITY_SYNC } from '../../../src/modules/agent-harness/binders/tokens';
import { CapabilityMigration, prometheusShadowMismatches } from '../../../src/modules/control-plane';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import { prometheusPinShadowMetrics } from '../../../src/modules/agent-harness/adapters/telemetry/pin-shadow-metrics';
import { AGENT_HARNESS_POLICY_PINNING, bindControlPlanePin } from '../../../src/modules/agent-harness/binders/control-plane-pin';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import sinon from 'sinon';
import type { Model } from 'mongoose';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { bindCredentialBindings } from '../../../src/modules/agent-harness/binders/credential-bindings';
import { CONTROL_PLANE_BINDINGS, MongoInstallationStore, type BindingService, type CapabilitySync } from '../../../src/modules/control-plane';
import {
  CpCapabilityLedgerModel,
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
} from '../../../src/libs/repositories/control-plane/installations/schema/installations.schema';
import { MemoryKv, platformCipher } from '../agent-harness/support/model-accounts';
import { CUSTOM_MCP, legacyFacts } from './support/capability-world';
import { ORG_A } from './support/cp-objects';
import { fakeCollections } from './support/fake-mongo-models';
import { InMemoryBindingStore } from './support/in-memory-stores';
import { testControlPlane } from './support/in-memory';

/** Restores `name` after `run`; unset (`undefined`) deletes it for the duration. */
function withEnv<T>(name: string, value: string | undefined, run: () => T): T {
  const previous = process.env[name];
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
  try {
    return run();
  } finally {
    if (previous === undefined) delete process.env[name];
    else process.env[name] = previous;
  }
}

// An unset CONTROL_PLANE_PIN follows the harness switch, so each bind names the switch rather than inheriting the
// runner's: the codegen job runs these files with `--no-config`, without the suite's harness-on helper.
function bind(env: string | undefined, appConfig: boolean, pinner = true, harness = 'true'): Container {
  const container = new Container();
  if (appConfig) container.bind('AppConfig').toConstantValue({ aiBackend: 'http://q', jwtSecret: 'j', scopedJwtSecret: 's' });
  const context = {
    container,
    dependencies: { agentAccess: { authorizeRun: async () => null }, rollout: {} },
    controlPlane: testControlPlane(),
    logger: undefined,
  } as unknown as HarnessBindingContext;
  withEnv('AGENT_HARNESS_WORKER_ENABLED', harness, () =>
    withEnv('CONTROL_PLANE_PIN', env, () => {
      if (pinner) bindControlPlanePin(context);
      bindControlPlaneCapabilities(context);
    }),
  );
  return container;
}

describe('CPD-7 control-plane binder', () => {
  it('binds the reviewed agent authority over the pinner\'s snapshots and, with the host configuration, the capability sync', () => {
    const container = bind(undefined, true);
    expect(container.get(AGENT_HARNESS_AGENT_ACCESS)).to.be.instanceOf(ControlPlaneReviewedAgentAccess);
    expect(container.get(CONTROL_PLANE_CAPABILITY_SYNC)).to.be.instanceOf(CapabilityMigration);
    expect(bind('shadow', false).isBound(CONTROL_PLANE_CAPABILITY_SYNC)).to.equal(false);
  });

  it('off, harness off with the pin unset, or no pinner bound: the agent authority is left as it is (nothing to compare with); the sync is bound all the same', () => {
    for (const container of [bind('off', true), bind('shadow', true, false), bind(undefined, true, true, 'false')]) {
      expect(container.isBound(AGENT_HARNESS_AGENT_ACCESS)).to.equal(false);
      expect(container.isBound(CONTROL_PLANE_CAPABILITY_SYNC)).to.equal(true);
    }
  });

  it('SH-1: a CONTROL_PLANE_PIN it does not know refuses to start', () => {
    expect(() => bind('maybe', true)).to.throw(/CONTROL_PLANE_PIN/);
  });

  it('boots with the pinner (CPD-5) and the effective-set shadow both bound, counting on one metric', async () => {
    const container = bind(undefined, false);
    expect(container.isBound(AGENT_HARNESS_POLICY_PINNING)).to.equal(true);
    expect(container.get(AGENT_HARNESS_AGENT_ACCESS)).to.be.instanceOf(ControlPlaneReviewedAgentAccess);
    const count = async (dimension: string): Promise<number> =>
      Number(
        new RegExp(`pipeshub_cp_shadow_mismatch_total\\{dimension="${dimension}"\\} (\\d+)`).exec(await metricsBackend.serialize())?.[1] ?? 0,
      );
    const before = [await count('budgets'), await count('visibility')];
    prometheusPinShadowMetrics.mismatch('budgets');
    prometheusShadowMismatches.inc('visibility');
    expect([await count('budgets'), await count('visibility')]).to.deep.equal([before[0]! + 1, before[1]! + 1]);
    expect((await metricsBackend.serialize()).match(/# TYPE pipeshub_cp_shadow_mismatch_total /g)).to.have.length(1);
  });

  it("MG-11: the bound sync revokes a removed installation's bindings through the binding service the credentials binder made", async () => {
    fakeCollections({
      installations: CpInstallationModel,
      history: CpInstallationHistoryModel,
      grants: CpGrantModel,
      ledger: CpCapabilityLedgerModel,
    } as unknown as Record<string, Model<never>>);
    const container = new Container();
    container.bind('AppConfig').toConstantValue({ aiBackend: 'http://q', jwtSecret: 'j'.repeat(40), scopedJwtSecret: 's'.repeat(40) });
    const kv = new MemoryKv();
    const bindings = new InMemoryBindingStore();
    const context = {
      container,
      dependencies: {
        agentAccess: { authorizeRun: async () => null },
        rollout: {},
        credentialBindings: { kv, cipher: () => platformCipher, bindings },
      },
      controlPlane: testControlPlane(),
      logger: undefined,
    } as unknown as HarnessBindingContext;
    bindCredentialBindings(context);
    bindControlPlaneCapabilities(context);
    const facts = (mcpInstances: unknown[]) => ({ schemaVersion: 1, orgId: ORG_A, toolsetInstances: [], mcpInstances, agents: [], nextAgent: null });
    const custom = legacyFacts().mcpInstances.filter((mcp) => mcp.instanceId === CUSTOM_MCP);
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute');
    try {
      execute.resolves({ statusCode: 200, data: facts(custom) } as never);
      const sync = container.get<CapabilitySync>(CONTROL_PLANE_CAPABILITY_SYNC);
      expect(await sync.syncInstallations(ORG_A)).to.include({ created: 1, failed: null });
      const installation = (await new MongoInstallationStore().get(ORG_A, CUSTOM_MCP))!;
      const service = container.get<BindingService>(CONTROL_PLANE_BINDINGS);
      const imported = await service.importRecord(
        { orgId: ORG_A, actor: 'system' },
        { installation: installation.id, owner: 'user:u3', mode: 'api_key', kind: 'mcp', provider: null, material: { kind: 'api_key', value: { apiKey: 'k1' } }, source: null },
      );
      expect(imported.outcome).to.equal('created');
      expect((await bindings.listByInstallation(ORG_A, CUSTOM_MCP, { after: null, limit: 10 })).items.map((b) => b.status)).to.deep.equal(['active']);

      execute.resolves({ statusCode: 200, data: facts([]) } as never);
      expect(await sync.syncInstallations(ORG_A)).to.include({ removed: 1, failed: null });
      expect((await bindings.listByInstallation(ORG_A, CUSTOM_MCP, { after: null, limit: 10 })).items.map((b) => b.status)).to.deep.equal(['revoked']);
    } finally {
      sinon.restore();
    }
  });
});
