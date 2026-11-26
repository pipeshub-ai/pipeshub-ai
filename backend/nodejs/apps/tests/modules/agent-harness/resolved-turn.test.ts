/**
 * CPD-10 §3, §6.2 (Node half): `X-PipesHub-Resolved` as Node signs it.
 * RTE-22a/22d on the shared vector `contracts/control-plane/vectors/resolved-turn.json`
 * (the query service verifies the same bytes), the projection of a pin
 * (CPD10-22, CPD10-23), the flow rules against today's read path
 * (`contracts/harness/vectors/legacy-flow-rules.json`, LEG-P), and a header
 * projected from a pin that Python's strict parser reads
 * (`contracts/control-plane/vectors/resolved-turn-node.json`).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Request, RequestHandler, Response } from 'express';
import { Container } from 'inversify';
import { Types } from 'mongoose';
import { HttpError } from '../../../src/libs/errors/http.errors';
import type { LegacyTurn } from '../../../src/modules/agent-harness/application/legacy-turn-resolver';
import { AGENT_HARNESS_LEGACY_TURN_HEADER } from '../../../src/modules/agent-harness/binders/tokens';
import {
  createResolvedTurnGuard,
  prepareResolvedTurn,
  resolvedProxyHeaders,
  resolvedTurnGuard,
  signsResolvedTurn,
} from '../../../src/modules/agent-harness/transport/resolved-turn-header';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { LegacyTurnHeader } from '../../../src/modules/agent-harness/application/legacy-turn-header';
import { LegacyTurnResolver, NOT_SERVED_DURABLY } from '../../../src/modules/agent-harness/application/legacy-turn-resolver';
import {
  ResolvedTurnAuthority,
  ResolvedTurnRefusedError,
} from '../../../src/modules/agent-harness/application/resolved-turn-authority';
import { decodeAttemptToken } from '../../../src/modules/agent-harness/application/attempt-token-codec';
import { RETIRED_KEY_RETENTION_MS } from '../../../src/modules/agent-harness/domain/authority';
import { deploymentFlow, type FlowSettings } from '../../../src/modules/agent-harness/domain/deployment-policy';
import { HarnessRunConfigUnavailableError, HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import { GATEWAY_TOKEN_AUDIENCE, GATEWAY_TOKEN_ISSUER } from '../../../src/modules/agent-harness/domain/gateway-token';
import { strictestFlows, type FlowRule } from '../../../src/modules/agent-harness/domain/policy/approval-rules';
import {
  holdsUnreadableLayer,
  MAX_RESOLVED_TURN_BYTES,
  MAX_RESOLVED_TURN_LIFETIME_S,
  RESOLVED_TURN_AUDIENCE,
  RESOLVED_TURN_HEADER,
  RESOLVED_TURN_ISSUER,
  RESOLVED_TURN_TTL_S,
  resolvedTurnClaims,
  UNREADABLE_FLOW,
  withoutBindings,
  type ResolvedTurnSubject,
} from '../../../src/modules/agent-harness/domain/resolved-turn';
import {
  resolvedTurnSchema,
  sealPin,
  type PinResult,
  type ResolvedRunConfig,
  type ResolvedTurn,
} from '../../../src/modules/control-plane';
import { aBinding, aGrant, anEntry, anInstallation, bindingId, digest, instId, ORG_A } from '../control-plane/support/cp-objects';
import { aLayer, aSnapshot } from '../control-plane/support/resolution-fixtures';
import { aPin, pinnerOf } from './support/a-pin';
import { findCanaries, VECTOR_EXAMPLES } from './support/canary-scan';
import {
  legacyWorld,
  manualClock,
  RESOLVED_TURN_VECTOR as VECTOR,
  VECTOR_NOW_MS,
  vectorKeyring,
} from './support/resolved-turn-world';

const CONTRACTS = join(__dirname, '../../../../../../contracts');
const NODE_GOLDEN = join(CONTRACTS, 'control-plane/vectors/resolved-turn-node.json');

interface FlowRulesVector {
  deployments: Array<{ id: string; settings: FlowSettings; rule: FlowRule | null }>;
  cases: Array<{ id: string; unreadable: boolean; rules: Record<string, FlowRule[]> }>;
}
const FLOW_RULES = JSON.parse(
  readFileSync(join(CONTRACTS, 'harness/vectors/legacy-flow-rules.json'), 'utf8'),
) as FlowRulesVector;
const APPROVALS = (
  JSON.parse(readFileSync(join(CONTRACTS, 'harness/vectors/approvals.json'), 'utf8')) as {
    documents: Array<{ id: string; document: unknown; orgId: string; agentKey: string | null }>;
  }
).documents;

const refusal = async (work: Promise<unknown>): Promise<string> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(ResolvedTurnRefusedError);
    return (error as ResolvedTurnRefusedError).reason;
  }
  return expect.fail('expected a ResolvedTurnRefusedError');
};

const unavailable = async (work: Promise<unknown>): Promise<void> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessRunConfigUnavailableError);
    expect((error as HarnessRunConfigUnavailableError).code).to.equal('RUN_CONFIG_UNAVAILABLE');
    return;
  }
  expect.fail('expected RUN_CONFIG_UNAVAILABLE');
};

const row = (value: unknown) => ({ value, setBy: 'org', ceilings: [] }) as never;

/** A pin whose content this file states whole: no release row moves it. */
function statedPin(overrides: Partial<ResolvedRunConfig> = {}): ResolvedRunConfig {
  return sealPin({
    schemaVersion: 1,
    headRevision: 7,
    routing: { path: 'legacy', reason: 'chat_mode_not_supported' },
    scope: { agentKey: 'agent-a', projectId: null },
    effectiveSet: {
      items: [
        item(instId(1), 'ready', 'user:user-1', 1),
        item(instId(2), 'ready', 'agent:agent-a', 2),
        item(instId(3), 'ready', 'org', 3),
        item(instId(4), 'unavailable', 'user:user-1', 4),
        item(instId(5), 'ready', null, 5),
      ],
      digest: canonicalDigest([]),
    },
    effectiveConfig: {
      schemaVersion: 1,
      dimensions: {
        'budgets.maxTurns': row(10),
        'rollout.ENABLE_AGENT_HARNESS': row(true),
        'rollout.AGENT_HARNESS_KILL_SWITCH': row(false),
        'rollout.ENABLE_MCP': row(true),
        'rollout.ENABLE_USER_CONTEXT': row(false),
        'rollout.durable.modes': row(['auto', 'quick']),
        'rollout.durable.modelFamilies': row(null),
        approvals: row({
          classes: { read: 'allow', workspace: 'allow', write: 'ask', execute: 'ask' },
          tools: {},
          flow: [{ level: 'deny', above: 'private' }],
        }),
      },
    },
    llm: null,
    deploymentDigest: canonicalDigest({}),
    ...overrides,
  });
}

function item(
  installation: string,
  state: 'ready' | 'unavailable',
  owner: string | null,
  n: number,
): ResolvedRunConfig['effectiveSet']['items'][number] {
  return {
    installation,
    item: `toolset:pipeshub/t${String(n)}@1`,
    kind: 'toolset',
    state,
    reason: state === 'ready' ? null : 'credential_expired',
    tools: [],
    binding: owner === null ? null : { ref: bindingId(n), owner, versionDigest: digest('4') },
    provenance: { grantedBy: 'agent:agent-a', visibleVia: 'org', approvedBy: null },
  };
}

const SUBJECT: ResolvedTurnSubject = {
  orgId: 'org-1',
  actorId: 'user-1',
  agentKey: 'agent-a',
  conversationId: 'conv-1',
  issuedAt: VECTOR.nowSeconds - 1,
  routing: { path: 'legacy', reason: 'chat_mode_not_supported' },
};

const TURN = { orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-a', chatMode: 'planExecute' };

describe('CPD-10 §3: the resolved-turn header, signed by Node', () => {
  describe('RTE-22a: the shared vector', () => {
    it("the vector's claims signed with the vector's key are exactly the vector's token", async () => {
      const { keys, clock } = await vectorKeyring();
      const authority = new ResolvedTurnAuthority({ keys, clock });
      expect(await authority.sign(VECTOR.valid.payload)).to.equal(VECTOR.valid.token);
      expect(await authority.header(VECTOR.valid.payload), 'under the size limit nothing is trimmed').to.equal(VECTOR.valid.token);
    });

    it('Node verifies it for the request it was signed for and reads the claims back', async () => {
      const { keys, clock } = await vectorKeyring();
      const authority = new ResolvedTurnAuthority({ keys, clock });
      expect(await authority.verify(VECTOR.valid.token, VECTOR.binding.request)).to.deep.equal(VECTOR.valid.payload);
      expect(await authority.verify(VECTOR.valid.token)).to.deep.equal(VECTOR.valid.payload);
    });

    it("the vector's constants are Node's", () => {
      expect([VECTOR.issuer, VECTOR.audience]).to.deep.equal([RESOLVED_TURN_ISSUER, RESOLVED_TURN_AUDIENCE]);
      expect([VECTOR.ttlSeconds, VECTOR.maxLifetimeSeconds]).to.deep.equal([RESOLVED_TURN_TTL_S, MAX_RESOLVED_TURN_LIFETIME_S]);
      expect(VECTOR.retentionMs).to.equal(RETIRED_KEY_RETENTION_MS);
    });

    for (const refused of VECTOR.refused) {
      it(`refused as the query service refuses it: ${refused.id} (${refused.reason})`, async () => {
        const { keys, clock } = await vectorKeyring();
        const authority = new ResolvedTurnAuthority({ keys, clock });
        expect(await refusal(authority.verify(refused.token, VECTOR.binding.request))).to.equal(refused.reason);
      });
    }

    for (const bound of VECTOR.binding.refused) {
      it(`RTE-22c: the valid token for another request is refused: ${bound.id} (${bound.reason})`, async () => {
        const { keys, clock } = await vectorKeyring();
        const authority = new ResolvedTurnAuthority({ keys, clock });
        expect(await refusal(authority.verify(VECTOR.valid.token, bound.request))).to.equal(bound.reason);
      });
    }

    it('no token, and one longer than the query service reads, are malformed', async () => {
      const { keys, clock } = await vectorKeyring();
      const authority = new ResolvedTurnAuthority({ keys, clock });
      expect(await refusal(authority.verify(undefined))).to.equal('resolved_malformed');
      expect(await refusal(authority.verify(`${VECTOR.valid.token}${'A'.repeat(16 * 1024)}`))).to.equal('resolved_malformed');
    });

    it('a keyring that cannot be read is not a refusal of the token', async () => {
      const { keys, clock, signing } = await vectorKeyring();
      const authority = new ResolvedTurnAuthority({ keys, clock });
      const down = new Error('configuration store down');
      signing.read = async () => {
        throw down;
      };
      clock.advance(31 * 24 * 60 * 60 * 1000);
      let thrown: unknown;
      await authority.sign(VECTOR.valid.payload).catch((error: unknown) => {
        thrown = error;
      });
      expect(thrown).to.equal(down);
    });
  });

  describe('RTE-22d: two live keys', () => {
    it("the retired key's token verifies inside its retention and the active key's does; after retention the retired one is refused", async () => {
      const { keys, clock } = await vectorKeyring();
      const authority = new ResolvedTurnAuthority({ keys, clock });
      expect(await authority.verify(VECTOR.retired.token, VECTOR.binding.request)).to.deep.equal(VECTOR.retired.payload);
      expect(await authority.verify(VECTOR.valid.token, VECTOR.binding.request)).to.deep.equal(VECTOR.valid.payload);
      const retiredAt = VECTOR.keys.retired.retiredAtMs as number;
      const late = manualClock(retiredAt + VECTOR.retentionMs);
      const after = new ResolvedTurnAuthority({ keys: (await vectorKeyring(late)).keys, clock: late });
      expect(await refusal(after.verify(VECTOR.retired.token, VECTOR.binding.request))).to.equal('resolved_unknown_key');
    });

    it('a rotation: tokens of both live keys verify, and the published document lists both for the query service', async () => {
      const { keys, clock, published } = await vectorKeyring();
      const authority = new ResolvedTurnAuthority({ keys, clock });
      const claims: ResolvedTurn = { ...VECTOR.valid.payload, iat: VECTOR.nowSeconds, exp: VECTOR.nowSeconds + RESOLVED_TURN_TTL_S };
      const before = await authority.sign(claims);
      await keys.rotate();
      const after = await authority.sign(claims);
      const kid = (token: string): unknown => (decodeAttemptToken(token).header as { kid: unknown }).kid;
      expect(kid(before)).to.equal(VECTOR.keys.active.kid);
      expect(kid(after)).to.not.equal(kid(before));
      expect(await authority.verify(before, VECTOR.binding.request)).to.deep.equal(claims);
      expect(await authority.verify(after, VECTOR.binding.request)).to.deep.equal(claims);
      const document = (await published.read()).value!;
      // The gateway profile: the query service reads the keys, and holds each token to its own audience.
      expect([document.issuer, document.audience]).to.deep.equal([GATEWAY_TOKEN_ISSUER, GATEWAY_TOKEN_AUDIENCE]);
      const listed = new Map(document.keys.map((key) => [key.kid, key]));
      expect(listed.get(kid(before) as string)).to.include({ alg: 'EdDSA' }).and.to.have.property('retiredAt', clock.now());
      expect(listed.get(kid(after) as string)).to.include({ alg: 'EdDSA', retiredAt: null });
      expect(listed.get(kid(after) as string)?.publicKey).to.match(/^-----BEGIN PUBLIC KEY-----/);
    });

    it('one keyring, two audiences: a resolved turn is no gateway token, and the reverse', async () => {
      const { keys, clock, signing, published } = await vectorKeyring();
      const resolved = new ResolvedTurnAuthority({ keys, clock });
      const gateway = new GatewayTokenAuthority({ signingKeys: signing, publishedKeys: published, clock }, keys);
      let reason: string | undefined;
      await gateway.verify(VECTOR.valid.token).catch((error: unknown) => {
        reason = (error as HarnessUnauthorizedError).reason;
      });
      expect(reason).to.equal('audience');
      const run = await gateway.issue({
        binding: 'run',
        orgId: 'org-1',
        sessionId: 'conv-1',
        runId: 'run-1',
        parentRunId: null,
        attemptId: 'run-1.a1',
        fence: 1,
        toolIdsDigest: 'd',
        routesDigest: 'd',
      });
      expect((decodeAttemptToken(run.token).header as { kid: string }).kid, 'the same active key signs both').to.equal(VECTOR.keys.active.kid);
      expect(await refusal(resolved.verify(run.token))).to.equal('resolved_audience');
    });
  });

  describe('the projection of a pin', () => {
    it("claims in the vector's key order, bound to the turn, living 60 s", () => {
      const claims = resolvedTurnClaims(statedPin(), { ...SUBJECT, mayRun: true });
      expect(Object.keys(claims)).to.deep.equal(Object.keys(VECTOR.valid.payload));
      expect(Object.keys(claims.pin)).to.deep.equal(Object.keys(VECTOR.valid.payload.pin));
      expect(claims).to.include({ org: 'org-1', sub: 'user-1', agent: 'agent-a', cid: 'conv-1', ver: 1 });
      expect(claims.exp - claims.iat).to.equal(RESOLVED_TURN_TTL_S);
      expect(resolvedTurnSchema.safeParse(claims).success, 'the generated contract accepts it').to.equal(true);
    });

    it('`pin` names the digest, the head, the route the turn took and the scope; a legacy pin has no agent owner', () => {
      const pin = statedPin();
      const claims = resolvedTurnClaims(pin, { ...SUBJECT, routing: { path: 'legacy', reason: NOT_SERVED_DURABLY } });
      expect(claims.pin).to.deep.equal({
        digest: pin.digest,
        head: 7,
        routing: { path: 'legacy', reason: NOT_SERVED_DURABLY },
        scope: { agentKey: 'agent-a', projectId: null },
      });
      const owned = resolvedTurnClaims(statedPin({ scope: { agentKey: 'agent-a', projectId: 'p1', agentOwner: 'u-owner' } }), SUBJECT);
      expect(claims.pin.scope).to.not.have.property('agentOwner');
      expect(owned.pin.scope).to.deep.equal({ agentKey: 'agent-a', projectId: 'p1', agentOwner: 'u-owner' });
    });

    it('`stages` holds every boolean rollout row by flag name, and never a routing list or another dimension', () => {
      expect(resolvedTurnClaims(statedPin(), SUBJECT).stages).to.deep.equal({
        ENABLE_AGENT_HARNESS: true,
        AGENT_HARNESS_KILL_SWITCH: false,
        ENABLE_MCP: true,
        ENABLE_USER_CONTEXT: false,
      });
    });

    it("`flow` is the resolved approvals' flow rules; a pin without approvals has none", () => {
      expect(resolvedTurnClaims(statedPin(), SUBJECT).flow).to.deep.equal([{ level: 'deny', above: 'private' }]);
      const none = statedPin({ effectiveConfig: { schemaVersion: 1, dimensions: {} } });
      expect(resolvedTurnClaims(none, SUBJECT).flow).to.deep.equal([]);
      expect(resolvedTurnClaims(none, SUBJECT).stages).to.deep.equal({});
    });

    it('QA10-F1: a layer today cannot read adds ask above public, met with what the pin holds', () => {
      expect(resolvedTurnClaims(statedPin(), { ...SUBJECT, unreadableApprovals: true }).flow).to.deep.equal([
        UNREADABLE_FLOW,
        { level: 'deny', above: 'private' },
      ]);
      const stricter = statedPin({
        effectiveConfig: { schemaVersion: 1, dimensions: { approvals: row({ classes: {}, tools: {}, flow: [{ level: 'deny', above: 'public' }] }) } },
      });
      expect(resolvedTurnClaims(stricter, { ...SUBJECT, unreadableApprovals: true }).flow).to.deep.equal([{ level: 'deny', above: 'public' }]);
    });

    it('`bindings` names the ready items a user or an agent holds a credential for, by reference; never the org’s, never an unavailable one', () => {
      expect(resolvedTurnClaims(statedPin(), SUBJECT).bindings).to.deep.equal([
        { installation: instId(1), owner: 'user:user-1', ref: bindingId(1) },
        { installation: instId(2), owner: 'agent:agent-a', ref: bindingId(2) },
      ]);
      const unbound = statedPin({ effectiveSet: { items: [item(instId(5), 'ready', null, 5)], digest: canonicalDigest([]) } });
      expect(resolvedTurnClaims(unbound, SUBJECT)).to.not.have.property('bindings');
    });

    it('`access` is present only when the directory said the actor may run the agent', () => {
      expect(resolvedTurnClaims(statedPin(), { ...SUBJECT, mayRun: true }).access).to.deep.equal({ run: true });
      expect(resolvedTurnClaims(statedPin(), { ...SUBJECT, mayRun: false })).to.not.have.property('access');
      expect(resolvedTurnClaims(statedPin(), SUBJECT)).to.not.have.property('access');
    });

    it('withoutBindings drops the bindings and nothing else', () => {
      const claims = resolvedTurnClaims(statedPin(), { ...SUBJECT, mayRun: true });
      const { bindings, ...rest } = claims;
      expect(bindings).to.have.length(2);
      expect(withoutBindings(claims)).to.deep.equal(rest);
    });
  });

  describe('CPD10-22: size', () => {
    const boundItems = (count: number) =>
      Array.from({ length: count }, (_, i) => item(instId(i + 1), 'ready', 'user:user-1', i + 1));

    it('a turn with 400 bound items is sent under 8 KB without its bindings, counted and logged by ids only', async () => {
      const { keys, clock } = await vectorKeyring();
      let trimmed = 0;
      const lines: Array<[string, Record<string, unknown>]> = [];
      const authority = new ResolvedTurnAuthority({
        keys,
        clock,
        metrics: { trimmed: () => (trimmed += 1) },
        logger: {
          debug: () => undefined,
          info: () => undefined,
          error: () => undefined,
          warn: (message, meta = {}) => lines.push([message, meta]),
        },
      });
      const pin = statedPin({ effectiveSet: { items: boundItems(400), digest: canonicalDigest([]) } });
      const claims = resolvedTurnClaims(pin, { ...SUBJECT, mayRun: true });
      expect(claims.bindings).to.have.length(400);
      expect(Buffer.byteLength(await authority.sign(claims)), 'the whole header would not fit').to.be.greaterThan(MAX_RESOLVED_TURN_BYTES);
      const token = await authority.header(claims);
      expect(Buffer.byteLength(token)).to.be.at.most(MAX_RESOLVED_TURN_BYTES);
      expect(Buffer.byteLength(token), 'the rest is under 1 KB of claims').to.be.lessThan(1400);
      const sent = await authority.verify(token, VECTOR.binding.request);
      expect(sent).to.not.have.property('bindings');
      expect(sent).to.deep.equal(withoutBindings(claims));
      expect(trimmed).to.equal(1);
      expect(lines.map(([message]) => message)).to.deep.equal(['cp.resolved_turn_trimmed']);
      expect(lines[0]![1]).to.include({ orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-a', sessionId: 'conv-1', bindings: 400 });
      expect(JSON.stringify(lines[0]![1])).to.not.include(token).and.not.include(bindingId(1));
    });

    it('a header that fits keeps its bindings and counts nothing', async () => {
      const { keys, clock } = await vectorKeyring();
      let trimmed = 0;
      const authority = new ResolvedTurnAuthority({ keys, clock, metrics: { trimmed: () => (trimmed += 1) } });
      const pin = statedPin({ effectiveSet: { items: boundItems(40), digest: canonicalDigest([]) } });
      const sent = await authority.verify(await authority.header(resolvedTurnClaims(pin, SUBJECT)), VECTOR.binding.request);
      expect(sent.bindings).to.have.length(40);
      expect(trimmed).to.equal(0);
    });
  });

  describe('CPD10-23: no secret in the claims', () => {
    it('the vector payloads and a pin with bindings hold nothing of a token shape', async () => {
      const world = await legacyWorld({ snapshot: boundSnapshot() });
      const resolution = await world.resolver.resolve({ orgId: ORG_A, actorId: 'u3', agentKey: 'a7', chatMode: 'planExecute' });
      const projected = resolvedTurnClaims(resolution.pin, { ...SUBJECT, orgId: ORG_A, actorId: 'u3', agentKey: 'a7', mayRun: true });
      expect(projected.bindings).to.have.length(2);
      for (const claims of [VECTOR.valid.payload, VECTOR.retired.payload, projected, resolvedTurnClaims(statedPin(), SUBJECT)]) {
        expect(findCanaries(claims, { shapes: 'all' })).to.deep.equal([]);
      }
    });

    it('the scan would find a credential a claim carried', () => {
      const secret = VECTOR_EXAMPLES[0]!.secret;
      const leaking = { ...VECTOR.valid.payload, bindings: [{ installation: 'inst-1', owner: 'user:u1', ref: secret }] };
      expect(findCanaries(leaking, { shapes: 'all' })).to.not.deep.equal([]);
    });

    it("a binding's secret path and version never reach the claims", async () => {
      const world = await legacyWorld({ snapshot: boundSnapshot() });
      const { pin } = await world.resolver.resolve({ orgId: ORG_A, actorId: 'u3', agentKey: 'a7', chatMode: 'planExecute' });
      const text = JSON.stringify(resolvedTurnClaims(pin, SUBJECT));
      expect(text).to.not.include('/services/controlPlane/credentials').and.not.include(digest('4'));
    });
  });

  describe('LEG-P: the flow rules against today’s read path (legacy-flow-rules.json)', () => {
    it('the vector covers every approvals document and names the unreadable ones', () => {
      expect(FLOW_RULES.cases.map((entry) => entry.id)).to.deep.equal(APPROVALS.map((entry) => entry.id));
      expect(FLOW_RULES.cases.filter((entry) => entry.unreadable).map((entry) => entry.id)).to.deep.equal([
        'D-7', 'D-8', 'D-9', 'D-10', 'D-15', 'D-F6',
      ]);
    });

    it("Node reads the deployment's flow settings as the query service does", () => {
      for (const deployment of FLOW_RULES.deployments) {
        expect(deploymentFlow(deployment.settings).rule, deployment.id).to.deep.equal(deployment.rule);
      }
    });

    for (const deployment of FLOW_RULES.deployments) {
      // Node's own deployment rule is in the pin; a Node with the guard off must give the same answer once the worker meets its own.
      for (const nodeFlow of [deployment.rule, null]) {
        it(`${deployment.id}, Node's guard ${nodeFlow === null ? 'off' : 'as the worker’s'}: header flow met with the deployment's rule is today's, for every document`, async () => {
          for (const source of APPROVALS) {
            const expected = FLOW_RULES.cases.find((entry) => entry.id === source.id)!;
            const world = await legacyWorld({ approvals: source.document, flow: nodeFlow });
            const resolution = await world.resolver.resolve({
              orgId: source.orgId,
              actorId: 'user-1',
              agentKey: source.agentKey,
              chatMode: 'planExecute',
            });
            expect(resolution.unreadableApprovals, `${source.id}: unreadable`).to.equal(expected.unreadable);
            const { flow } = resolvedTurnClaims(resolution.pin, {
              ...SUBJECT,
              orgId: source.orgId,
              agentKey: source.agentKey,
              routing: resolution.routing,
              unreadableApprovals: resolution.unreadableApprovals,
            });
            const applied = strictestFlows([...(deployment.rule === null ? [] : [deployment.rule]), ...flow]);
            expect(applied, source.id).to.deep.equal(expected.rules[deployment.id]);
          }
        });
      }
    }

    it('the pin alone cannot say it: an operator’s `writes: ask` (D-3) and an unreadable document (D-7) resolve the same approvals', async () => {
      const approvalsOf = async (id: string): Promise<unknown> => {
        const source = APPROVALS.find((entry) => entry.id === id)!;
        const world = await legacyWorld({ approvals: source.document });
        const { pin } = await world.resolver.resolve({ orgId: source.orgId, actorId: 'user-1', agentKey: source.agentKey, chatMode: 'auto' });
        return pin.effectiveConfig.dimensions['approvals']?.value;
      };
      expect(await approvalsOf('D-7')).to.deep.equal(await approvalsOf('D-3'));
    });

    it("holdsUnreadableLayer: an edition's own layers count only where it is given them", () => {
      const broken = { agents: { 'agent-a': { writes: 'maybe' } } };
      expect(holdsUnreadableLayer(broken, 'org-1', 'agent-a')).to.equal(true);
      expect(holdsUnreadableLayer(broken, 'org-1', 'agent-b')).to.equal(false);
      const edition = { org: () => [{ writes: 'maybe' }], agent: () => [] };
      expect(holdsUnreadableLayer({}, 'org-1', 'agent-b', edition)).to.equal(true);
      expect(holdsUnreadableLayer({}, 'org-1', 'agent-b')).to.equal(false);
    });
  });

  describe('LegacyTurnResolver', () => {
    it('pins the turn on the route the middleware resolved: the heads are read once for route and pin', async () => {
      const world = await legacyWorld({ stages: { ENABLE_MCP: true } });
      const route = await world.pinner.route({ orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-a', projectId: null, chatMode: 'planExecute' });
      const reads = world.heads.reads;
      const facts = { infrastructureReady: true, workerLive: true, scheduled: false, placement: { durable: false, projectId: 'p-9' }, modelKey: null };
      const resolution = await world.resolver.resolve(TURN, { facts, basis: route.basis, config: route.config });
      expect(world.heads.reads - reads, 'no second head read').to.equal(0);
      expect(resolution.pin.routing).to.deep.equal({ path: 'legacy', reason: 'chat_mode_not_supported' });
      expect(resolution.routing).to.deep.equal(resolution.pin.routing);
      expect(resolution.pin.scope, 'the project is the conversation record’s').to.deep.equal({ agentKey: 'agent-a', projectId: 'p-9' });
    });

    it('without a route it resolves one itself, on one head read once the snapshot is warm', async () => {
      const world = await legacyWorld();
      await world.resolver.resolve(TURN);
      const reads = world.heads.reads;
      const resolution = await world.resolver.resolve({ ...TURN, projectId: 'p-1' });
      expect(world.heads.reads - reads).to.equal(1);
      expect(resolution.pin.scope).to.deep.equal({ agentKey: 'agent-a', projectId: 'p-1' });
      expect(resolution.pin.headRevision).to.equal(0);
    });

    it('a route resolved for another agent is not pinned on (Universal Agent Mode on an agent’s conversation)', async () => {
      const world = await legacyWorld();
      const route = await world.pinner.route({ orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-a', projectId: null, chatMode: 'auto' });
      const facts = { infrastructureReady: true, workerLive: true, scheduled: false, placement: { durable: false, projectId: 'p-9' }, modelKey: null };
      const resolution = await world.resolver.resolve({ ...TURN, agentKey: null, chatMode: 'agent' }, { facts, basis: route.basis, config: route.config });
      expect(resolution.pin.scope).to.deep.equal({ agentKey: null, projectId: null });
    });

    it('a turn policy would route durable is still a legacy turn on this header, and says why', async () => {
      const world = await legacyWorld();
      const resolution = await world.resolver.resolve({ ...TURN, chatMode: 'auto' });
      expect(resolution.pin.routing.path).to.equal('durable');
      expect(resolution.routing).to.deep.equal({ path: 'legacy', reason: NOT_SERVED_DURABLY });
    });

    it('the subject carries no definition, owner or model: the pin has no agent owner and calls no model the request named', async () => {
      const subjects: unknown[] = [];
      const resolver = new LegacyTurnResolver({
        pinner: pinnerOf(async (subject) => {
          subjects.push(subject);
          return aPin();
        }),
      });
      await resolver.resolve(TURN);
      expect(subjects[0]).to.deep.equal({
        orgId: 'org-1',
        actorId: 'user-1',
        agentKey: 'agent-a',
        projectId: null,
        chatMode: 'planExecute',
        surface: 'web',
        request: { schemaVersion: 1, scope: { kind: 'request' } },
      });
    });

    it('a pin the control plane refuses is RUN_CONFIG_UNAVAILABLE', async () => {
      const refused: PinResult = { type: 'x', title: 'refused', status: 403, code: 'CP_OUTSIDE_CEILING' } as never;
      await unavailable(new LegacyTurnResolver({ pinner: pinnerOf(async () => refused) }).resolve(TURN));
    });

    it('a pin that cannot be made, a route that cannot be resolved and a document that cannot be read are RUN_CONFIG_UNAVAILABLE', async () => {
      const failing = pinnerOf(async () => {
        throw new Error('snapshot unreadable');
      });
      await unavailable(new LegacyTurnResolver({ pinner: failing }).resolve(TURN));
      const world = await legacyWorld();
      world.heads.unreadable = true;
      await unavailable(world.resolver.resolve(TURN));
      const unread = new LegacyTurnResolver({
        pinner: pinnerOf(async () => aPin()),
        unreadableApprovals: async () => {
          throw new Error('configuration store down');
        },
      });
      await unavailable(unread.resolve(TURN));
    });

    it('what the approvals document says of a snapshot is asked once; a failed read is asked again', async () => {
      const world = await legacyWorld({ approvals: { orgs: ['org-1'] } });
      expect((await world.resolver.resolve(TURN)).unreadableApprovals).to.equal(true);
      await world.resolver.resolve(TURN);
      await world.resolver.resolve({ ...TURN, chatMode: 'auto' });
      expect(world.readabilityReads(), 'one snapshot, one read').to.equal(1);
      world.heads.revisions.set('org-1', 1);
      await world.resolver.resolve(TURN);
      expect(world.readabilityReads(), 'a new head, a new snapshot').to.equal(2);

      let calls = 0;
      const flaky = new LegacyTurnResolver({
        pinner: pinnerOf(async () => aPin(), { org: 3, deployment: 0 }),
        unreadableApprovals: async () => {
          calls += 1;
          if (calls === 1) throw new Error('once');
          return true;
        },
      });
      const route = await pinnerOf(async () => aPin()).route({ orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-a', projectId: null, chatMode: 'auto' });
      const routed = { facts: { infrastructureReady: true, workerLive: true, scheduled: false, placement: null, modelKey: null }, basis: route.basis, config: route.config };
      await unavailable(flaky.resolve(TURN, routed));
      expect((await flaky.resolve(TURN, routed)).unreadableApprovals).to.equal(true);
      expect(calls).to.equal(2);
    });

    it('no approvals document wired: nothing is unreadable', async () => {
      const resolver = new LegacyTurnResolver({ pinner: pinnerOf(async () => aPin()) });
      expect((await resolver.resolve(TURN)).unreadableApprovals).to.equal(false);
    });
  });

  describe('LegacyTurnHeader', () => {
    const asked: Array<[string, string, { orgId: string; userId: string }]> = [];
    const directory = (answer: 'granted' | 'denied' | 'down') => ({
      authorizeRun: async (orgId: string, agentKey: string, actor: { orgId: string; userId: string }) => {
        asked.push([orgId, agentKey, actor]);
        if (answer === 'down') throw new Error('graph unreachable');
        return answer === 'granted' ? { agentKey, definitionVersion: 1 } : null;
      },
    });

    beforeEach(() => {
      asked.length = 0;
    });

    it('a header that verifies for the turn’s org, user, agent and conversation, signed at the clock’s time', async () => {
      const world = await legacyWorld({ approvals: { default: { flow: { level: 'ask' } } }, stages: { ENABLE_MCP: false } });
      const prepared = await world.header.prepare(TURN);
      const token = await world.header.sign(prepared!, 'conv-9');
      const turn = await world.authority.verify(token, { orgId: 'org-1', userId: 'user-1', agentKey: 'agent-a', conversationId: 'conv-9' });
      expect(turn).to.include({ iat: VECTOR.nowSeconds, exp: VECTOR.nowSeconds + 60, cid: 'conv-9' });
      expect(turn.stages).to.include({ ENABLE_MCP: false, ENABLE_AGENT_HARNESS: true });
      expect(turn.flow).to.deep.equal([{ level: 'ask', above: 'public' }]);
      expect(turn.pin.routing).to.deep.equal({ path: 'legacy', reason: 'chat_mode_not_supported' });
      expect(turn).to.not.have.property('access');
      expect(await refusal(world.authority.verify(token, { orgId: 'org-1', userId: 'user-1', agentKey: 'agent-a', conversationId: 'conv-1' }))).to.equal(
        'resolved_conversation_mismatch',
      );
    });

    it('`access` when the bound directory says the actor may run the agent, asked as that actor in that org', async () => {
      const world = await legacyWorld({ directory: directory('granted') });
      const prepared = await world.header.prepare(TURN);
      expect(prepared?.mayRun).to.equal(true);
      const turn = await world.authority.verify(await world.header.sign(prepared!, 'conv-1'));
      expect(turn.access).to.deep.equal({ run: true });
      expect(asked).to.deep.equal([['org-1', 'agent-a', { orgId: 'org-1', userId: 'user-1' }]]);
    });

    it('a denial: no header at all, and nothing is pinned', async () => {
      const world = await legacyWorld({ directory: directory('denied') });
      expect(await world.header.prepare(TURN)).to.equal(null);
      expect(world.heads.reads).to.equal(0);
    });

    it('a directory that cannot be reached: a header without `access`', async () => {
      const world = await legacyWorld({ directory: directory('down') });
      const prepared = await world.header.prepare(TURN);
      expect(prepared?.mayRun).to.equal(false);
      expect(await world.authority.verify(await world.header.sign(prepared!, 'conv-1'))).to.not.have.property('access');
    });

    it('never for the assistant: the directory is not asked', async () => {
      const world = await legacyWorld({ directory: directory('granted') });
      const prepared = await world.header.prepare({ ...TURN, agentKey: null, chatMode: 'agent' });
      expect(asked).to.deep.equal([]);
      const turn = await world.authority.verify(await world.header.sign(prepared!, 'conv-1'));
      expect(turn).to.include({ agent: null }).and.not.have.property('access');
    });

    it('no gateway keyring in this deployment: RUN_CONFIG_UNAVAILABLE before anything is pinned', async () => {
      const world = await legacyWorld();
      const header = new LegacyTurnHeader({ resolver: world.resolver, authority: () => null });
      await unavailable(header.prepare(TURN));
      expect(world.heads.reads).to.equal(0);
    });

    it('a signature that cannot be made is RUN_CONFIG_UNAVAILABLE', async () => {
      const world = await legacyWorld();
      const prepared = await world.header.prepare(TURN);
      world.ring.signing.read = async () => {
        throw new Error('configuration store down');
      };
      world.ring.clock.advance(31 * 24 * 60 * 60 * 1000);
      await unavailable(world.header.sign(prepared!, 'conv-1'));
    });
  });

  describe('the proxy sites’ helper', () => {
    const request = (headers: Record<string, string>, user?: Record<string, unknown>) =>
      ({ headers, params: {}, body: { chatMode: 'planExecute' }, ...(user !== undefined ? { user } : {}) }) as unknown as Request;
    const through = (guard: RequestHandler, req: Request): Request => {
      void guard(req, {} as Response, () => undefined);
      return req;
    };

    it('a handler called without the guard still never forwards a header the request carried', async () => {
      const headers = await resolvedProxyHeaders(request({ authorization: 'Bearer t', [RESOLVED_TURN_HEADER]: 'client' }), { conversationId: 'c1' });
      expect(headers).to.deep.equal({ authorization: 'Bearer t' });
    });

    it('the guard drops the inbound header from the request itself, whatever is bound', async () => {
      const req = through(resolvedTurnGuard(() => null), request({ authorization: 'Bearer t', [RESOLVED_TURN_HEADER]: 'client' }));
      expect(req.headers).to.deep.equal({ authorization: 'Bearer t' });
      expect(signsResolvedTurn(req)).to.equal(false);
      await prepareResolvedTurn(req, 'agent-a');
      expect(await resolvedProxyHeaders(req, { agentKey: 'agent-a', conversationId: 'c1' })).to.deep.equal({ authorization: 'Bearer t' });
    });

    it('createResolvedTurnGuard reads the signer from the container once it is bound, and none before', async () => {
      const container = new Container();
      const slot: { built?: Container } = {};
      const guard = createResolvedTurnGuard(() => slot.built);
      expect(signsResolvedTurn(through(guard, request({})))).to.equal(false);
      slot.built = container;
      expect(signsResolvedTurn(through(guard, request({})))).to.equal(false);
      const world = await legacyWorld();
      container.bind(AGENT_HARNESS_LEGACY_TURN_HEADER).toConstantValue(world.header);
      expect(signsResolvedTurn(through(guard, request({})))).to.equal(true);
    });

    it('with a signer: Node’s header for the site’s agent and conversation; a prepared turn is not pinned again', async () => {
      const world = await legacyWorld();
      const req = through(resolvedTurnGuard(() => world.header), request({ authorization: 'Bearer t' }, { orgId: 'org-1', userId: 'user-1' }));
      await prepareResolvedTurn(req, 'agent-a');
      const reads = world.heads.reads;
      const headers = await resolvedProxyHeaders(req, { agentKey: 'agent-a', conversationId: 'conv-4' });
      expect(world.heads.reads - reads, 'signed from what the middleware pinned').to.equal(0);
      expect(headers['authorization']).to.equal('Bearer t');
      const turn = await world.authority.verify(headers[RESOLVED_TURN_HEADER], { orgId: 'org-1', userId: 'user-1', agentKey: 'agent-a', conversationId: 'conv-4' });
      expect(turn.pin.routing.reason).to.equal('chat_mode_not_supported');
    });

    it('a site whose route is another agent’s than the one prepared is pinned for its own', async () => {
      const world = await legacyWorld();
      const req = through(resolvedTurnGuard(() => world.header), request({}, { orgId: 'org-1', userId: 'user-1' }));
      await prepareResolvedTurn(req, 'agent-a');
      const headers = await resolvedProxyHeaders(req, { conversationId: 'conv-4' });
      expect(await world.authority.verify(headers[RESOLVED_TURN_HEADER])).to.include({ agent: null });
    });

    it('a body without a chat mode is today’s loop’s default: quick for the assistant, auto for an agent; a new conversation’s project is the body’s', async () => {
      const turns: LegacyTurn[] = [];
      const recording = {
        prepare: async (turn: LegacyTurn) => {
          turns.push(turn);
          return null;
        },
        sign: async () => expect.fail('nothing was prepared'),
      };
      const prepare = async (agentKey: string | null, body: unknown, params: Record<string, string> = {}) => {
        const req = through(resolvedTurnGuard(() => recording), {
          headers: {},
          params,
          body,
          user: { orgId: new Types.ObjectId('64b7f0c2a1b2c3d4e5f60711'), userId: 'user-1' },
        } as unknown as Request);
        await prepareResolvedTurn(req, agentKey);
      };
      await prepare(null, {});
      await prepare('agent-a', undefined);
      await prepare('agent-a', { chatMode: 'deep', projectId: 'p-1' });
      await prepare('agent-a', { chatMode: 'deep', projectId: 'p-1' }, { conversationId: 'c-1' });
      expect(turns).to.deep.equal([
        { orgId: '64b7f0c2a1b2c3d4e5f60711', actorId: 'user-1', agentKey: null, chatMode: 'quick' },
        { orgId: '64b7f0c2a1b2c3d4e5f60711', actorId: 'user-1', agentKey: 'agent-a', chatMode: 'auto' },
        { orgId: '64b7f0c2a1b2c3d4e5f60711', actorId: 'user-1', agentKey: 'agent-a', chatMode: 'deep', projectId: 'p-1' },
        { orgId: '64b7f0c2a1b2c3d4e5f60711', actorId: 'user-1', agentKey: 'agent-a', chatMode: 'deep' },
      ]);
    });

    it('a request that authenticates as nobody gets no header: its handler answers that', async () => {
      const world = await legacyWorld();
      const req = through(resolvedTurnGuard(() => world.header), request({ authorization: 'Bearer t' }));
      expect(await resolvedProxyHeaders(req, { conversationId: 'c1' })).to.deep.equal({ authorization: 'Bearer t' });
      expect(world.heads.reads).to.equal(0);
    });

    it('a turn that cannot be pinned at the site is the transport’s 503 RUN_CONFIG_UNAVAILABLE', async () => {
      const world = await legacyWorld();
      world.heads.unreadable = true;
      const req = through(resolvedTurnGuard(() => world.header), request({}, { orgId: 'org-1', userId: 'user-1' }));
      let thrown: unknown;
      await resolvedProxyHeaders(req, { conversationId: 'c1' }).catch((error: unknown) => {
        thrown = error;
      });
      expect(thrown).to.be.instanceOf(HttpError);
      expect(thrown).to.include({ statusCode: 503, code: 'HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE' });
    });
  });

  describe('a header projected from a pin, as the query service reads it (resolved-turn-node.json)', () => {
    it('is the committed vector, which Python’s verifier accepts for its request', async () => {
      const { keys, clock } = await vectorKeyring();
      const authority = new ResolvedTurnAuthority({ keys, clock });
      const request = { orgId: 'org-1', userId: 'user-1', agentKey: 'agent-a', conversationId: 'conv-1' };
      const claims = resolvedTurnClaims(statedPin(), {
        ...SUBJECT,
        issuedAt: authority.issuedAt(),
        mayRun: true,
        unreadableApprovals: true,
      });
      const assistant = resolvedTurnClaims(
        statedPin({ scope: { agentKey: null, projectId: 'project-1' }, effectiveSet: { items: [], digest: canonicalDigest([]) } }),
        { ...SUBJECT, agentKey: null, issuedAt: authority.issuedAt(), routing: { path: 'legacy', reason: NOT_SERVED_DURABLY } },
      );
      const built = {
        description:
          "CPD-10 cross-language vector: X-PipesHub-Resolved headers as Node's signer makes them from a pin (resolvedTurnClaims, ResolvedTurnAuthority.header) with resolved-turn.json's active key at its nowSeconds. Each token must verify in the query service for `request` and read back as `claims`. Written by backend/nodejs/apps/tests/modules/agent-harness/resolved-turn.test.ts (UPDATE_GOLDENS=1); read by backend/python/tests/unit/harness/legacy/test_resolved_turn_vectors.py.",
        nowSeconds: VECTOR.nowSeconds,
        cases: [
          { id: 'agent-turn', request, claims, token: await authority.header(claims) },
          { id: 'assistant-turn', request: { ...request, agentKey: null }, claims: assistant, token: await authority.header(assistant) },
        ],
      };
      if (process.env.UPDATE_GOLDENS === '1') writeFileSync(NODE_GOLDEN, `${JSON.stringify(built, null, 2)}\n`);
      expect(built).to.deep.equal(JSON.parse(readFileSync(NODE_GOLDEN, 'utf8')));
      expect(claims.iat).to.equal(Math.floor(VECTOR_NOW_MS / 1000));
    });
  });
});

/** A snapshot whose effective set holds a per-user and an agent credential ready, an org one, and one without a credential. */
function boundSnapshot() {
  const modes = ['per_user', 'agent', 'org', 'none'] as const;
  const installations = modes.map((credentialMode, i) =>
    anInstallation({ id: instId(i + 1), kind: 'toolset', item: `toolset:pipeshub/t${String(i)}@1`, credentialMode }),
  );
  const owners = ['user:u3', 'agent:a7', 'org'] as const;
  return aSnapshot({
    installations,
    grants: [aGrant({ entries: installations.map((inst) => anEntry(inst.id)) })],
    bindings: owners.map((owner, i) => aBinding({ id: bindingId(i + 1), installation: instId(i + 1), owner })),
    layers: { [`org:${ORG_A}`]: aLayer('org', { rollout: { ENABLE_AGENT_HARNESS: true } }) },
  });
}
