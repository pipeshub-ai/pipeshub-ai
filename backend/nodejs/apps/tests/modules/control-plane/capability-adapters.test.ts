/**
 * CPD-7's edges: the legacy capability source's request (a `capabilities:read`
 * service token in the org asked about; every answer held to the contract),
 * the harness mount of the sync route (the host's auth, 503 where no sync is
 * bound), the bound sync's source, the shadow counter and the org listing a
 * whole migration run covers.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { Container } from 'inversify';
import sinon from 'sinon';
import type { Model } from 'mongoose';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { Logger } from '../../../src/libs/services/logger.service';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { activeOrgIds } from '../../../src/libs/repositories/agent-harness/active-orgs/mongo';
import { bindControlPlaneCapabilities } from '../../../src/modules/agent-harness/binders/control-plane-capabilities';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import { CONTROL_PLANE_CAPABILITY_SYNC } from '../../../src/modules/agent-harness/binders/tokens';
import {
  CAPABILITY_SYNC_PATH,
  createHarnessCapabilitySyncRouter,
} from '../../../src/modules/agent-harness/transport/capability-sync-routes';
import {
  CanonicalEffectiveSetResolver,
  releaseItemsOf,
  ResolvedSnapshotCache,
  type CapabilitySync,
  type PinSubject,
  type PolicyLayer,
} from '../../../src/modules/control-plane';
import {
  HttpLegacyCapabilitySource,
  LEGACY_CAPABILITIES_PATH,
  LegacySourceUnavailableError,
} from '../../../src/modules/control-plane/adapters/http/legacy-capability-source';
import {
  CpCapabilityLedgerModel,
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
} from '../../../src/libs/repositories/control-plane/installations/schema/installations.schema';
import { prometheusShadowMismatches } from '../../../src/modules/control-plane/adapters/telemetry/shadow-metrics';
import type { OAuthTokenService } from '../../../src/modules/oauth_provider/services/oauth_token.service';
import { Org } from '../../../src/modules/user_management/schema/org.schema';
import { aBinding, aGrant, anEntry, anInstallation, bindingId, directTx, instId, ORG_A, rejection } from './support/cp-objects';
import { InMemoryBindingStore, InMemoryCapabilityLedger, InMemoryGrantStore, InMemoryInstallationStore } from './support/in-memory-stores';
import { fakeCollections } from './support/fake-mongo-models';
import { testControlPlane } from './support/in-memory';

const JWT_SECRET = 'cpd7-jwt-secret-0123456789abcdef0123456789';
const SCOPED_SECRET = 'cpd7-scoped-secret-0123456789abcdef01234567';
const CONFIG = { aiBackend: 'http://query.test', jwtSecret: JWT_SECRET, scopedJwtSecret: SCOPED_SECRET };
const tokens = new AuthTokenService(JWT_SECRET, SCOPED_SECRET);
const emptyFacts = { schemaVersion: 1, orgId: ORG_A, toolsetInstances: [], mcpInstances: [], agents: [], nextAgent: null };

interface SentCommand {
  uri: string;
  headers: Record<string, string>;
}

function answering(statusCode: number, data: unknown): sinon.SinonStub {
  return sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode, data } as never);
}
const sent = (stub: sinon.SinonStub, call = 0): SentCommand => stub.thisValues[call] as SentCommand;

const HEADS = { head: async () => 4, heads: async () => ({ org: 4, deployment: 0 }) };

describe('CPD-7 capability adapters', () => {
  afterEach(() => sinon.restore());

  describe('HttpLegacyCapabilitySource', () => {
    const source = new HttpLegacyCapabilitySource(() => CONFIG);

    it("asks the query service's driver with a capabilities:read service token in the org asked about", async () => {
      const execute = answering(200, emptyFacts);
      expect(await source.page(ORG_A, { after: null })).to.deep.equal(emptyFacts);
      const command = sent(execute);
      expect(command.uri).to.equal(`${CONFIG.aiBackend}${LEGACY_CAPABILITIES_PATH}`);
      const bearer = command.headers.authorization!.replace(/^Bearer /, '');
      const payload = await tokens.verifyScopedToken(bearer, TokenScopes.CAPABILITIES_READ);
      expect(payload.orgId).to.equal(ORG_A);
      expect(await rejection(tokens.verifyScopedToken(bearer, TokenScopes.CAPABILITIES_SYNC))).to.be.instanceOf(Error);
    });

    it('names the page, the one agent and an instances-only read in the query', async () => {
      const execute = answering(200, emptyFacts);
      await source.page(ORG_A, { after: 'a7' });
      await source.page(ORG_A, { after: null, agentKey: 'a8' });
      await source.page(ORG_A, { after: null, agents: false });
      expect([0, 1, 2].map((call) => sent(execute, call).uri.split('?')[1])).to.deep.equal([
        'after=a7',
        'agentKey=a8',
        'agents=false',
      ]);
    });

    it('a status other than 200, or an answer the contract refuses, is the source unavailable: never empty facts', async () => {
      answering(503, { detail: 'down' });
      const down = await rejection(source.page(ORG_A, { after: null }));
      expect(down).to.be.instanceOf(LegacySourceUnavailableError);
      expect(down).to.include({ statusCode: 503, reason: 'legacy_source_unavailable' });
      sinon.restore();
      answering(200, { ...emptyFacts, toolsetInstances: 'none' });
      expect(await rejection(source.page(ORG_A, { after: null }))).to.include({ statusCode: 200, reason: 'legacy_source_unavailable' });
    });
  });

  describe('the harness mount of the sync route', () => {
    let server: http.Server;
    let url: string;
    let container: Container;

    beforeEach(async () => {
      container = new Container();
      const app = express();
      app.use(express.json());
      // Built before the host binds its auth, as in the processes that build routers first.
      app.use(CAPABILITY_SYNC_PATH, createHarnessCapabilitySyncRouter(container));
      app.use(ErrorMiddleware.handleError());
      container
        .bind('AuthMiddleware')
        .toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens, () => ({}) as unknown as OAuthTokenService));
      server = http.createServer((req, res) => {
        app(req, res);
      });
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
      url = `http://127.0.0.1:${(server.address() as AddressInfo).port}${CAPABILITY_SYNC_PATH}`;
    });
    afterEach(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    });

    const post = (scope: string | null) =>
      fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(scope === null ? {} : { Authorization: `Bearer ${tokens.generateScopedToken({ orgId: ORG_A, scopes: [scope] }, '5m')}` }),
        },
        body: JSON.stringify({ installations: true }),
      });

    it('takes the capabilities:sync service token only, and answers 503 where no sync is bound', async () => {
      expect((await post(null)).status).to.equal(401);
      expect((await post(TokenScopes.CAPABILITIES_READ)).status).to.equal(401);
      expect((await post(TokenScopes.CAPABILITIES_SYNC)).status).to.equal(503);
    });

    it("runs the bound sync in the token's org", async () => {
      const synced: string[] = [];
      const sync: Pick<CapabilitySync, 'syncInstallations'> = {
        syncInstallations: async (orgId) => {
          synced.push(orgId);
          return { orgId, mode: 'apply', created: 0, updated: 0, unchanged: 0, removed: 0, holds: [], failed: null } as never;
        },
      };
      container.bind(CONTROL_PLANE_CAPABILITY_SYNC).toConstantValue(sync);
      expect((await post(TokenScopes.CAPABILITIES_SYNC)).status).to.equal(200);
      expect(synced).to.deep.equal([ORG_A]);
    });
  });

  it("the bound sync reads legacy facts from the host configuration's query service", async () => {
    fakeCollections({
      installations: CpInstallationModel,
      history: CpInstallationHistoryModel,
      grants: CpGrantModel,
      ledger: CpCapabilityLedgerModel,
    } as unknown as Record<string, Model<never>>);
    const execute = answering(200, emptyFacts);
    const container = new Container();
    container.bind('AppConfig').toConstantValue(CONFIG);
    bindControlPlaneCapabilities({
      container,
      dependencies: { agentAccess: { authorizeRun: async () => null } },
      controlPlane: testControlPlane(),
      logger: undefined,
    } as unknown as HarnessBindingContext);
    const report = await container.get<CapabilitySync>(CONTROL_PLANE_CAPABILITY_SYNC).syncInstallations(ORG_A);
    expect(report).to.include({ orgId: ORG_A, failed: null, created: 0 });
    expect(sent(execute).uri).to.equal(`${CONFIG.aiBackend}${LEGACY_CAPABILITIES_PATH}?agents=false`);
  });

  it('the shadow counter counts a mismatch under its dimension', async () => {
    const count = async (): Promise<number> =>
      Number(/pipeshub_cp_shadow_mismatch_total\{dimension="visibility"\} (\d+)/.exec(await metricsBackend.serialize())?.[1] ?? 0);
    const before = await count();
    prometheusShadowMismatches.inc('visibility');
    expect((await count()) - before).to.equal(1);
  });

  it('a whole migration run covers every org that is not deleted', async () => {
    const find = sinon.stub(Org, 'find').returns({
      lean: () => ({ exec: async () => [{ _id: 'org-1' }, { _id: 'org-2' }] }),
    } as never);
    expect(await activeOrgIds()).to.deep.equal(['org-1', 'org-2']);
    expect((find.firstCall.args as unknown[])[0]).to.deep.equal({ isDeleted: { $ne: true } });
  });

  it("the pinner's snapshot carries the org's installations, the agent's grant and the release tools' effects, so a pin's effective set is the resolver's", async () => {
    const jira = releaseItemsOf('toolset').find((item) => item.name === 'jira')!;
    const tools = jira.descriptors.kind === 'tool' ? jira.descriptors.items : [];
    const installations = new InMemoryInstallationStore();
    const grants = new InMemoryGrantStore();
    const tx = directTx(ORG_A);
    await installations.create(
      tx,
      anInstallation({
        id: 'jira-1',
        kind: 'toolset',
        item: jira.ref,
        approved: {
          descriptorSetDigest: jira.descriptorSetDigest,
          tools: Object.fromEntries(tools.map((tool) => [tool.name, tool.digest])),
          approvalRef: null,
          at: 1,
          by: 'system',
        },
      }),
    );
    await installations.create(
      tx,
      anInstallation({ id: 'skill_notes', kind: 'skill', item: 'skill:user/notes@1.0.0', approved: { ...anInstallation().approved!, tools: { notes: jira.descriptorSetDigest } } }),
    );
    await grants.put(tx, aGrant({ entries: [anEntry('jira-1')] }), 0);
    const load = policySnapshotLoader({ heads: HEADS, capabilities: { installations, grants } });
    const snapshot = await load(ORG_A, 'a7', 'u3');
    expect([...snapshot.installations.keys()]).to.deep.equal(['jira-1', 'skill_notes']);
    expect([...snapshot.toolEffects.get('skill_notes')!]).to.deep.equal([['notes', 'read']]);
    expect([...snapshot.grants.keys()]).to.deep.equal(['agent:a7']);
    expect([...snapshot.toolEffects.get('jira-1')!.keys()].sort()).to.deep.equal(tools.map((tool) => tool.name).sort());
    const subject = { orgId: ORG_A, actorId: 'u3', agentKey: 'a7' } as PinSubject;
    const set = new CanonicalEffectiveSetResolver().resolve(subject, { schemaVersion: 1, dimensions: {} }, snapshot);
    expect('items' in set && set.items.map((item) => [item.installation, item.reason])).to.deep.equal([['jira-1', null]]);
    expect(snapshot.bindings).to.deep.equal([]);
    const none = await policySnapshotLoader({ heads: HEADS })(ORG_A, 'a7', 'u3');
    expect([none.installations.size, none.grants.size, none.toolEffects.size]).to.deep.equal([0, 0, 0]);
  });

  it("the pinner's snapshot carries the bindings a credentialed item can use: the org's, the agent's and the actor's, nobody else's", async () => {
    const installations = new InMemoryInstallationStore();
    const grants = new InMemoryGrantStore();
    const bindings = new InMemoryBindingStore();
    const tx = directTx(ORG_A);
    await installations.create(tx, anInstallation({ credentialMode: 'per_user' }));
    await grants.put(tx, aGrant({ entries: [anEntry(instId(1))] }), 0);
    await bindings.upsert(tx, aBinding({ id: bindingId(1), owner: 'user:u3' }), null);
    await bindings.upsert(tx, aBinding({ id: bindingId(2), owner: 'user:u9' }), null);
    await bindings.upsert(tx, aBinding({ id: bindingId(3), owner: 'agent:a7', installation: instId(2) }), null);
    await bindings.upsert(tx, aBinding({ id: bindingId(4), owner: 'org', installation: instId(3) }), null);
    const load = policySnapshotLoader({ heads: HEADS, capabilities: { installations, grants, bindings } });
    const snapshot = await load(ORG_A, 'a7', 'u3');
    expect(snapshot.bindings.map((b) => b.id).sort()).to.deep.equal([bindingId(1), bindingId(3), bindingId(4)]);
    const resolve = (actorId: string, from: typeof snapshot) =>
      new CanonicalEffectiveSetResolver().resolve({ orgId: ORG_A, actorId, agentKey: 'a7' } as PinSubject, { schemaVersion: 1, dimensions: {} }, from);
    const mine = resolve('u3', snapshot);
    expect('items' in mine && mine.items.map((item) => [item.reason, item.binding?.ref])).to.deep.equal([[null, bindingId(1)]]);
    const theirs = resolve('u5', await load(ORG_A, 'a7', 'u5'));
    expect('items' in theirs && theirs.items.map((item) => item.reason)).to.deep.equal(['credential_missing']);
  });

  describe('CPD10-57: whether the org is migrated, in the snapshot', () => {
    const world = () => {
      const installations = new InMemoryInstallationStore();
      const grants = new InMemoryGrantStore();
      const bindings = new InMemoryBindingStore();
      const ledger = new InMemoryCapabilityLedger(installations, grants);
      const reads = () => installations.queries + grants.queries + bindings.queries + ledger.markerReads;
      return { installations, grants, bindings, ledger, reads };
    };
    const orgRollout = (layers: ReadonlyMap<string, PolicyLayer>) => layers.get(`org:${ORG_A}`)?.rollout;

    it('a snapshot miss costs one point read more than the facts it already read, and a hit none', async () => {
      const without = world();
      await policySnapshotLoader({ heads: HEADS, capabilities: { installations: without.installations, grants: without.grants, bindings: without.bindings } })(ORG_A, 'a7', 'u3');
      const before = without.reads();
      const w = world();
      const cache = new ResolvedSnapshotCache({ load: policySnapshotLoader({ heads: HEADS, capabilities: w }), heads: HEADS });
      await cache.snapshot(ORG_A, 'a7', 'u3');
      expect([w.reads() - before, w.ledger.markerReads]).to.deep.equal([1, 1]);
      await cache.snapshot(ORG_A, 'a7', 'u3');
      expect([w.reads() - before, w.ledger.markerReads]).to.deep.equal([1, 1]);
    });

    it("a migrated org's layer carries rollout.CONTROL_PLANE_CAPABILITIES; an org that is not has no row and no layer of its own", async () => {
      const w = world();
      const load = policySnapshotLoader({ heads: HEADS, capabilities: w });
      expect((await load(ORG_A, 'a7', 'u3')).layers.has(`org:${ORG_A}`)).to.equal(false);
      await w.ledger.markMigrated(directTx(ORG_A));
      expect(orgRollout((await load(ORG_A, 'a7', 'u3')).layers)).to.deep.equal({ CONTROL_PLANE_CAPABILITIES: true });
      expect((await load('org-b', 'a7', 'u3')).layers.has('org:org-b')).to.equal(false);
    });

    it('the row is the ledger\'s alone: a stored org layer that sets it is not read, and its other rows stay', async () => {
      const w = world();
      const stored = new Map<string, PolicyLayer>([
        [`org:${ORG_A}`, { schemaVersion: 1, scope: { kind: 'org', id: ORG_A }, rollout: { CONTROL_PLANE_CAPABILITIES: true, ENABLE_SKILLS: false } } as PolicyLayer],
      ]);
      const load = policySnapshotLoader({ heads: HEADS, capabilities: w, layers: async () => stored });
      expect(orgRollout((await load(ORG_A, 'a7', 'u3')).layers)).to.deep.equal({ ENABLE_SKILLS: false });
      await w.ledger.markMigrated(directTx(ORG_A));
      expect(orgRollout((await load(ORG_A, 'a7', 'u3')).layers)).to.deep.equal({ ENABLE_SKILLS: false, CONTROL_PLANE_CAPABILITIES: true });
    });
  });
});
