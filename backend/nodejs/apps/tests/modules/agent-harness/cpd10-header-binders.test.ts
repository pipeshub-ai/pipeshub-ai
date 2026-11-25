/**
 * CPD-10 §3, §4: what the binders wire for the legacy header and
 * `/me/capabilities`. The header's signer exists only under
 * `CONTROL_PLANE_PIN=on`; it signs with the process's one gateway keyring;
 * `/me/capabilities` asks the decider that decides, without a shadow.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import { prometheusResolvedTurnMetrics } from '../../../src/modules/agent-harness/adapters/telemetry/resolved-turn-metrics';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { decodeAttemptToken } from '../../../src/modules/agent-harness/application/attempt-token-codec';
import type { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import type { LegacyTurnHeader } from '../../../src/modules/agent-harness/application/legacy-turn-header';
import type { ResolvedTurnAuthority } from '../../../src/modules/agent-harness/application/resolved-turn-authority';
import { OrgScopedRoutingDecider, ShadowRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import { bindRoutingCapabilities } from '../../../src/modules/agent-harness/binders/admission';
import { bindAuthority, bindBudgetLedger, bindGatewayTokens } from '../../../src/modules/agent-harness/binders/authority';
import {
  bindControlPlanePin,
  decidingRouter,
  routingDecider,
} from '../../../src/modules/agent-harness/binders/control-plane-pin';
import { AGENT_HARNESS_POLICY_BACKING } from '../../../src/modules/agent-harness/binders/policy-store';
import {
  AGENT_HARNESS_GATEWAY_AUTHORITY,
  AGENT_HARNESS_GATEWAY_KEYRING,
  AGENT_HARNESS_LEGACY_TURN_HEADER,
  AGENT_HARNESS_RESOLVED_TURNS,
} from '../../../src/modules/agent-harness/binders/tokens';
import type { AgentHarnessDependencies } from '../../../src/modules/agent-harness/composition';
import type { AttemptKeyring, PublishedAttemptKeys } from '../../../src/modules/agent-harness/domain/authority';
import { HarnessRunConfigUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import type { RoutingDecider } from '../../../src/modules/agent-harness/ports/routing-decider';
import { CONTROL_PLANE_ME_CAPABILITIES, type MeCapabilitiesSource } from '../../../src/modules/control-plane';
import { aSnapshot } from '../control-plane/support/resolution-fixtures';
import { RESOLVED_TURN_VECTOR as VECTOR } from './support/resolved-turn-world';
import { without } from './support/without';

const ORG = 'org-1';
const TURN = { orgId: ORG, actorId: 'user-1', agentKey: 'agent-a', chatMode: 'planExecute' };

const dependencies = async (approvalsDocument: unknown = { default: { writes: 'ask' } }): Promise<AgentHarnessDependencies> => {
  const approvals = new InMemoryConfigDocumentStore<unknown>();
  await approvals.replace(null, approvalsDocument);
  return {
    rollout: new HarnessRollout(
      { read: async () => ({ infrastructureReady: true, killSwitch: true, orgEnabled: true, modelFamilies: null }) },
      { resolve: async () => null },
    ),
    sessions: { placement: async () => null },
    approvalSettings: { document: approvals, identities: {} },
    gatewayTokens: {
      gatewaySigningKeys: new InMemoryConfigDocumentStore<AttemptKeyring>(),
      gatewayPublicKeys: new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
    },
  } as unknown as AgentHarnessDependencies;
};

/** A route the middleware resolved, on a snapshot this test states: the pin reads no store. */
const routed = () => ({
  facts: { infrastructureReady: true, workerLive: true, scheduled: false, placement: null, modelKey: null },
  basis: { ...aSnapshot({ agentKey: 'agent-a', actorId: 'user-1' }), orgId: ORG },
  config: { schemaVersion: 1 as const, dimensions: {} },
});

describe('CPD-10: the binders of the legacy header and /me/capabilities', () => {
  const saved = process.env.CONTROL_PLANE_PIN;
  afterEach(() => {
    if (saved === undefined) delete process.env.CONTROL_PLANE_PIN;
    else process.env.CONTROL_PLANE_PIN = saved;
  });

  const bound = async (mode: string, deps?: AgentHarnessDependencies, prepare?: (container: Container) => void) => {
    const container = new Container();
    prepare?.(container);
    process.env.CONTROL_PLANE_PIN = mode;
    const context = { container, dependencies: deps ?? (await dependencies()) } as never;
    bindControlPlanePin(context);
    bindAuthority(context);
    bindGatewayTokens(context);
    bindBudgetLedger(context);
    bindRoutingCapabilities(context);
    return container;
  };

  it('CPD10-21: the header’s signer is bound under `on` only: `off` and `shadow` have nothing to ask', async () => {
    expect((await bound('off')).isBound(AGENT_HARNESS_LEGACY_TURN_HEADER)).to.equal(false);
    expect((await bound('shadow')).isBound(AGENT_HARNESS_LEGACY_TURN_HEADER)).to.equal(false);
    expect((await bound('on')).isBound(AGENT_HARNESS_LEGACY_TURN_HEADER)).to.equal(true);
  });

  it('on: the bound signer pins on the production pinner and signs with the gateway keyring; the published key verifies it', async () => {
    const deps = await dependencies();
    const container = await bound('on', deps);
    const header = container.get<LegacyTurnHeader>(AGENT_HARNESS_LEGACY_TURN_HEADER);
    const prepared = await header.prepare(TURN, routed());
    expect(prepared?.resolution.unreadableApprovals).to.equal(false);
    const token = await header.sign(prepared!, 'conv-1');
    const turn = await container
      .get<ResolvedTurnAuthority>(AGENT_HARNESS_RESOLVED_TURNS)
      .verify(token, { orgId: ORG, userId: 'user-1', agentKey: 'agent-a', conversationId: 'conv-1' });
    expect(turn.pin.routing.path).to.equal('legacy');
    const published = (await deps.gatewayTokens!.gatewayPublicKeys.read()).value!;
    expect(published.keys.map((key) => key.kid)).to.deep.equal([(decodeAttemptToken(token).header as { kid: string }).kid]);
  });

  it('on: an approvals document today cannot read reaches the header as ask above public, with the policy store as the record or not', async () => {
    const unreadable = { default: { writes: 'ask' }, notASection: {} };
    const flowOf = async (container: Container) => {
      const header = container.get<LegacyTurnHeader>(AGENT_HARNESS_LEGACY_TURN_HEADER);
      const token = await header.sign((await header.prepare(TURN, routed()))!, 'conv-1');
      return (await container.get<ResolvedTurnAuthority>(AGENT_HARNESS_RESOLVED_TURNS).verify(token)).flow;
    };
    const ASK = [{ level: 'ask', above: 'public' }];
    expect(await flowOf(await bound('on', await dependencies(unreadable)))).to.deep.equal(ASK);
    const storeIsRecord = (container: Container) =>
      void container.bind(AGENT_HARNESS_POLICY_BACKING).toConstantValue({ mode: 'on', store: {}, layers: {} });
    expect(await flowOf(await bound('on', await dependencies(unreadable), storeIsRecord))).to.deep.equal(ASK);
    expect(await flowOf(await bound('on', await dependencies({ default: { writes: 'maybe' } }), storeIsRecord))).to.deep.equal(ASK);
    expect(await flowOf(await bound('on', await dependencies({ default: { writes: 'ask' } }), storeIsRecord))).to.deep.equal([]);
  });

  it('on, a deployment without a gateway keyring: the turn is refused, never proxied without a header', async () => {
    const deps = without(await dependencies(), 'gatewayTokens');
    const container = await bound('on', deps as AgentHarnessDependencies);
    expect(container.isBound(AGENT_HARNESS_RESOLVED_TURNS)).to.equal(false);
    let thrown: unknown;
    await container
      .get<LegacyTurnHeader>(AGENT_HARNESS_LEGACY_TURN_HEADER)
      .prepare(TURN, routed())
      .catch((error: unknown) => {
        thrown = error;
      });
    expect(thrown).to.be.instanceOf(HarnessRunConfigUnavailableError);
  });

  it('one gateway keyring per process: a rotation by the gateway authority is the key the next resolved turn is signed with', async () => {
    const container = await bound('on');
    const gateway = container.get<GatewayTokenAuthority>(AGENT_HARNESS_GATEWAY_AUTHORITY);
    const resolved = container.get<ResolvedTurnAuthority>(AGENT_HARNESS_RESOLVED_TURNS);
    const claims = { ...VECTOR.valid.payload, iat: resolved.issuedAt(), exp: resolved.issuedAt() + 60 };
    const kid = (token: string): string => (decodeAttemptToken(token).header as { kid: string }).kid;
    const before = kid(await resolved.sign(claims));
    await gateway.rotate();
    const after = kid(await resolved.sign(claims));
    expect(after, 'no second keyring serving a cached ring').to.not.equal(before);
    const run = await gateway.issue({
      binding: 'run', orgId: ORG, sessionId: 's', runId: 'r', parentRunId: null, attemptId: 'r.a1', fence: 1, toolIdsDigest: 'd', routesDigest: 'd',
    });
    expect(kid(run.token)).to.equal(after);
    expect(container.get(AGENT_HARNESS_GATEWAY_KEYRING), 'a singleton').to.equal(container.get(AGENT_HARNESS_GATEWAY_KEYRING));
  });

  it('CPD10-22: a trimmed header is counted in pipeshub_cp_resolved_turn_trimmed_total', async () => {
    const value = async (): Promise<number> =>
      Number(/^pipeshub_cp_resolved_turn_trimmed_total (\d+)/m.exec(await metricsBackend.serialize())?.[1] ?? 0);
    const before = await value();
    prometheusResolvedTurnMetrics.trimmed();
    expect((await value()) - before).to.equal(1);
  });

  it('CPD-10 §4: who decides: today’s decider in off and shadow (no shadow comparison for a question), the resolved one in on', async () => {
    const today: RoutingDecider = { decide: async () => ({ routing: { useHarness: true, harnessMajor: 2, reason: 'enabled' }, scope: {} }) };
    expect(decidingRouter(await bound('off'), today)).to.equal(today);
    const shadow = await bound('shadow');
    expect(decidingRouter(shadow, today)).to.equal(today);
    expect(routingDecider(shadow, today)).to.be.instanceOf(ShadowRoutingDecider);
    const on = await bound('on');
    // ADR-OPS-08: in on, the resolved decider for an org with the harness on, today's for any other.
    expect(decidingRouter(on, today)).to.be.instanceOf(OrgScopedRoutingDecider);
    expect(routingDecider(on, today)).to.be.instanceOf(OrgScopedRoutingDecider);
  });

  it('/me/capabilities is bound in every mode; in off it answers from today’s rollout; a second bind keeps the first', async () => {
    const deps = await dependencies();
    const container = await bound('off', deps);
    const source = container.get<MeCapabilitiesSource>(CONTROL_PLANE_ME_CAPABILITIES);
    const routing = await source.routing(ORG, 'user-1');
    expect(routing.agent['auto']).to.deep.equal({ path: 'legacy', reason: 'kill_switch' });
    expect(routing.assistant['internal_search']).to.deep.equal({ path: 'legacy', reason: 'kill_switch' });
    bindRoutingCapabilities({ container, dependencies: deps } as never);
    expect(container.get(CONTROL_PLANE_ME_CAPABILITIES)).to.equal(source);
    expect((await bound('shadow')).isBound(CONTROL_PLANE_ME_CAPABILITIES)).to.equal(true);
  });
});
