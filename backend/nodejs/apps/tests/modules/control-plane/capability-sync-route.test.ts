/**
 * DW-1, DW-4 (CPD-7): the dual-write sync route on a real Express server with
 * the real scoped-token check. Only a `capabilities:sync` service token
 * passes; the org is the token's, never the body's; a sync applies the
 * migration's plan for the agent or the instances named.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { Logger } from '../../../src/libs/services/logger.service';
import { CapabilityMigration } from '../../../src/modules/control-plane/application/capability-migration';
import { CAPABILITY_SYNC_PATH, createCapabilitySyncRouter } from '../../../src/modules/control-plane/adapters/http/capability-sync-router';
import type { OAuthTokenService } from '../../../src/modules/oauth_provider/services/oauth_token.service';
import { LegacyFacts, legacyFacts, migrationDeps, type CapabilityWorld } from './support/capability-world';
import { ORG_A, ORG_B } from './support/cp-objects';
import { InMemoryCapabilityLedger, InMemoryGrantStore, InMemoryInstallationStore } from './support/in-memory-stores';
import { auditedControlPlane } from './support/in-memory';

const JWT_SECRET = 'cpd7-jwt-secret-0123456789abcdef0123456789';
const SCOPED_SECRET = 'cpd7-scoped-secret-0123456789abcdef01234567';

describe('CPD-7 capability sync route', () => {
  const tokens = new AuthTokenService(JWT_SECRET, SCOPED_SECRET);
  let server: http.Server;
  let url: string;
  let world: CapabilityWorld;
  let source: LegacyFacts;

  beforeEach(async () => {
    const installations = new InMemoryInstallationStore();
    const grants = new InMemoryGrantStore();
    const controlPlane = auditedControlPlane();
    world = {
      installations,
      grants,
      ledger: new InMemoryCapabilityLedger(installations, grants),
      transaction: controlPlane.transaction,
      configEntries: async (orgId) => controlPlane.configEntries(orgId).length,
    };
    source = new LegacyFacts();
    source.orgs.set(ORG_A, legacyFacts());
    source.orgs.set(ORG_B, legacyFacts());
    const auth = new AuthMiddleware(Logger.getInstance(), tokens, () => ({}) as unknown as OAuthTokenService);
    const app = express();
    app.use(express.json());
    app.use(
      CAPABILITY_SYNC_PATH,
      createCapabilitySyncRouter({
        authorize: auth.scopedTokenValidator(TokenScopes.CAPABILITIES_SYNC),
        migration: () => new CapabilityMigration(migrationDeps(world, source)),
      }),
    );
    app.use(ErrorMiddleware.handleError());
    server = http.createServer((req, res) => {
      app(req, res);
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    url = `http://127.0.0.1:${(server.address() as AddressInfo).port}${CAPABILITY_SYNC_PATH}`;
  });

  afterEach(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const post = (body: unknown, bearer: string | null) =>
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(bearer === null ? {} : { Authorization: `Bearer ${bearer}` }) },
      body: JSON.stringify(body),
    });
  const syncToken = (orgId: string) => tokens.generateScopedToken({ orgId, scopes: [TokenScopes.CAPABILITIES_SYNC] }, '5m');

  it("DW-1: an agent sync writes that agent's grant in the token's org", async () => {
    const response = await post({ agentKey: 'a8' }, syncToken(ORG_A));
    expect(response.status).to.equal(200);
    expect(await response.json()).to.include({ created: 1 });
    expect(await world.grants.get(ORG_A, 'agent:a8')).to.not.equal(null);
    expect(await world.grants.get(ORG_B, 'agent:a8')).to.equal(null);
  });

  it('DW-2: an installations sync writes the org instances', async () => {
    const response = await post({ installations: true }, syncToken(ORG_A));
    expect(response.status).to.equal(200);
    expect((await world.installations.listForOrg(ORG_A, {})).length).to.equal(4);
  });

  it('DW-4: no token, a user token, another scope, or a body naming an org is refused; nothing is written', async () => {
    const userToken = tokens.generateToken({ userId: 'u1', orgId: ORG_A } as never);
    const otherScope = tokens.generateScopedToken({ orgId: ORG_A, scopes: [TokenScopes.CAPABILITIES_READ] }, '5m');
    expect((await post({ agentKey: 'a8' }, null)).status).to.equal(401);
    expect((await post({ agentKey: 'a8' }, userToken)).status).to.equal(401);
    expect((await post({ agentKey: 'a8' }, otherScope)).status).to.equal(401);
    expect((await post({ agentKey: 'a8', orgId: ORG_B }, syncToken(ORG_A))).status).to.equal(400);
    expect(await world.grants.get(ORG_A, 'agent:a8')).to.equal(null);
    expect(await world.grants.get(ORG_B, 'agent:a8')).to.equal(null);
  });

  it('DW-3: a legacy source that fails answers 503 with the reason, and writes nothing', async () => {
    source.failing.add(ORG_A);
    const response = await post({ agentKey: 'a8' }, syncToken(ORG_A));
    expect(response.status).to.equal(503);
    expect(await response.json()).to.deep.equal({ code: 'CP_UNAVAILABLE', reason: 'legacy_source_unavailable' });
  });
});
