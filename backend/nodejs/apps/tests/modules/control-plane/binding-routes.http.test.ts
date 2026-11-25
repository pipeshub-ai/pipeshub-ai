/**
 * CPD-8 "My connections" and the internal binding routes over HTTP (MC-1..4,
 * IN-1..2): the real routers and the platform's scoped-token validator over
 * the in-memory store, the KV vault and the audited transaction. Service
 * tokens are minted as the Python services mint them (HS256, the scoped
 * secret, `scopes`).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import jwt from 'jsonwebtoken';
import type { AddressInfo } from 'node:net';
import type { Server } from 'node:http';
import { Container } from 'inversify';
import sinon from 'sinon';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { Logger } from '../../../src/libs/services/logger.service';
import { BindingService, REFRESH_CLAIM_MS } from '../../../src/modules/control-plane/application/binding-service';
import {
  CONTROL_PLANE_API_PREFIX,
  CONTROL_PLANE_AUTHORIZER,
  CONTROL_PLANE_BINDINGS,
  CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX,
  createControlPlaneAdminRouter,
  createInternalBindingRouter,
} from '../../../src/modules/control-plane';
import { RoleAuthorizer } from '../../../src/modules/control-plane/application/role-authorizer';
import { UnreadableDirectory } from '../../../src/modules/control-plane/adapters/unreadable-directory';
import type { ChangeEvent } from '../../../src/modules/control-plane';
import { assertNoCanary } from '../agent-harness/support/canary-scan';
import { anInstallation, instId, ORG_A, ORG_B } from './support/cp-objects';
import { InMemoryBindingStore } from './support/in-memory-stores';
import { vaultBinderWorld, type VaultBinderWorld } from './support/binding-world';
import { continueAs } from '../../helpers/request-org-context';

/** "My connections" is on the control plane's admin route table since CPD-11 (R14, R16). */
const CONTROL_PLANE_BINDINGS_PREFIX = `${CONTROL_PLANE_API_PREFIX}/bindings`;
const JWT_SECRET = 'cpd8-jwt-secret-0123456789abcdef0123456789';
const SCOPED_SECRET = 'cpd8-scoped-secret-0123456789abcdef01234567';
const ACCESS = 'xoxe.xoxp-1-' + 'RouteAccessToken0123456789abcdef';
const serviceToken = (claims: Record<string, unknown>, secret = SCOPED_SECRET): string =>
  jwt.sign({ scopes: ['control-plane:bindings'], ...claims }, secret, { algorithm: 'HS256', expiresIn: 300 });

interface Caller {
  userId: string;
  orgId: string;
}

describe('CPD-8 binding routes (MC, IN)', () => {
  let world: VaultBinderWorld;
  let server: Server;
  let base: string;
  let caller: Caller;
  let admins: Set<string>;
  let sandbox: sinon.SinonSandbox;

  beforeEach(async () => {
    sandbox = sinon.createSandbox();
    for (const level of ['error', 'warn', 'info', 'debug'] as const) sandbox.stub(Logger.prototype, level);
    world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    caller = { userId: 'u3', orgId: ORG_A };
    admins = new Set();
    const auth = new AuthMiddleware(new Logger({ service: 'cpd8-test' }), new AuthTokenService(JWT_SECRET, SCOPED_SECRET));
    // Users authenticate as the platform's middleware leaves them; service tokens go through the real validator.
    auth.authenticate = (async (req: any, _res: unknown, next: () => void) => {
      req.user = { ...caller };
      continueAs(req.user, next);
    }) as never;
    const container = new Container();
    container.bind('AuthMiddleware').toConstantValue(auth);
    container.bind(CONTROL_PLANE_BINDINGS).toConstantValue(new BindingService(world.bindings, world.binder, () => world.clock.now));
    const directory = new UnreadableDirectory();
    container.bind(CONTROL_PLANE_AUTHORIZER).toConstantValue(
      new RoleAuthorizer(
        { roleOf: async (orgId, userId) => (admins.has(`${orgId}|${userId}`) ? 'admin' : 'member') },
        directory,
        directory,
        { delegatedActions: async () => [] },
      ),
    );
    const app = express();
    app.use(express.json());
    app.use(CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX, createInternalBindingRouter(container));
    app.use(CONTROL_PLANE_API_PREFIX, createControlPlaneAdminRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = app.listen(0);
    await new Promise((resolve) => server.once('listening', resolve));
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterEach(() => {
    server.close();
    sandbox.restore();
  });

  async function call(
    method: string,
    path: string,
    options: { body?: unknown; token?: string; headers?: Record<string, string> } = {},
  ): Promise<{ status: number; json: any; text: string; etag: string | null }> {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: {
        'content-type': 'application/json',
        ...(options.token === undefined ? {} : { authorization: `Bearer ${options.token}` }),
        ...options.headers,
      },
      ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
    });
    const text = await response.text();
    return { status: response.status, json: text ? JSON.parse(text) : null, text, etag: response.headers.get('etag') };
  }

  const bind = async (owner: 'user:u3' | 'user:u4' | 'agent:a1' | 'org', installation = instId(1)) =>
    (
      await world.binder.begin({ orgId: ORG_A, actor: 'user:u3' }, anInstallation({ id: installation }), owner, 'api_key', {
        kind: 'api_key',
        value: { apiKey: ACCESS },
      })
    ).binding;

  const importBody = (overrides: Record<string, unknown> = {}) => ({
    installation: instId(1),
    owner: 'user:u3',
    mode: 'oauth',
    kind: 'toolset',
    provider: 'slack',
    material: { kind: 'record', value: { isAuthenticated: true, credentials: { access_token: ACCESS } }, expiresAt: 5_000 },
    source: { path: `/services/toolsets/${instId(1)}/u3`, digest: 'sha256:' + 'a'.repeat(64) },
    ...overrides,
  });

  it('MC-1: the caller lists their own bindings in their org, without a secret, a path or a digest', async () => {
    const own = await bind('user:u3');
    await bind('user:u4', instId(2));
    const listed = await call('GET', CONTROL_PLANE_BINDINGS_PREFIX);
    expect(listed.status).to.equal(200);
    expect(listed.json.bindings).to.deep.equal([
      {
        id: own.id,
        installation: instId(1),
        owner: 'user:u3',
        mode: 'api_key',
        status: 'active',
        expiresAt: null,
        refreshedAt: own.refreshedAt,
        lastError: null,
        revision: 1,
      },
    ]);
    expect(listed.text).to.not.include('/services/');
    expect(listed.text).to.not.include('sha256:');
    assertNoCanary('my connections', listed.json, { secrets: [ACCESS], shapes: 'vendor' });
    caller = { userId: 'u3', orgId: ORG_B };
    expect((await call('GET', CONTROL_PLANE_BINDINGS_PREFIX)).json.bindings).to.deep.equal([]);
  });

  it('MC-2: the org\'s and agents\' bindings are for org admins; a malformed owner is refused', async () => {
    await bind('org');
    await bind('agent:a1', instId(2));
    for (const owner of ['org', 'agent:a1']) {
      expect((await call('GET', `${CONTROL_PLANE_BINDINGS_PREFIX}?owner=${owner}`)).status).to.equal(403);
    }
    admins.add(`${ORG_A}|u3`);
    expect((await call('GET', `${CONTROL_PLANE_BINDINGS_PREFIX}?owner=org`)).json.bindings).to.have.length(1);
    expect((await call('GET', `${CONTROL_PLANE_BINDINGS_PREFIX}?owner=agent:a1`)).json.bindings).to.have.length(1);
    expect((await call('GET', `${CONTROL_PLANE_BINDINGS_PREFIX}?owner=team:t1`)).status).to.equal(422);
    expect((await call('GET', `${CONTROL_PLANE_BINDINGS_PREFIX}?owner=me&orgId=${ORG_B}`)).status).to.equal(422);
  });

  it('MC-3, MC-4: revoke needs the current revision, answers the new one and is audited as a narrowing', async () => {
    const own = await bind('user:u3');
    const revoke = (headers: Record<string, string> = {}) =>
      call('POST', `${CONTROL_PLANE_BINDINGS_PREFIX}/${own.id}/actions/revoke`, { headers });
    expect((await revoke()).status).to.equal(428);
    const stale = await revoke({ 'if-match': '"7"' });
    expect(stale.status).to.equal(409);
    expect(stale.text).to.include('CP_REVISION_CONFLICT');
    const done = await revoke({ 'if-match': '"1"' });
    expect(done.status).to.equal(200);
    expect(done.json.binding).to.deep.include({ id: own.id, status: 'revoked', revision: 2 });
    expect(done.etag).to.equal('"2"');
    expect(await world.vaultRead(own.secret!.path)).to.equal(null);
    const events = (await world.changeEvents()) as ChangeEvent[];
    expect(events.at(-1)).to.deep.include({ narrowing: true, touched: [{ kind: 'binding', id: own.id }] });
    expect(await world.configEntries(ORG_A)).to.have.length(2);
  });

  it('MC-3: someone else\'s binding is not found for a member, and revocable by an admin; another org\'s never', async () => {
    const theirs = await bind('user:u4');
    const revoke = () =>
      call('POST', `${CONTROL_PLANE_BINDINGS_PREFIX}/${theirs.id}/actions/revoke`, { headers: { 'if-match': '1' } });
    expect((await revoke()).status).to.equal(404);
    caller = { userId: 'u9', orgId: ORG_B };
    admins.add(`${ORG_B}|u9`);
    expect((await revoke()).status).to.equal(404);
    caller = { userId: 'u3', orgId: ORG_A };
    admins.add(`${ORG_A}|u3`);
    expect((await revoke()).json.binding).to.deep.include({ status: 'revoked' });
  });

  it('IN-1: the internal routes need a service token of their scope; the org is the token\'s, a body cannot name one', async () => {
    const path = `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/import`;
    expect((await call('POST', path, { body: importBody() })).status).to.equal(401);
    expect((await call('POST', path, { body: importBody(), token: serviceToken({ orgId: ORG_A }, 'another-secret-0123456789abcdef') })).status).to.equal(401);
    expect((await call('POST', path, { body: importBody(), token: serviceToken({ orgId: ORG_A, scopes: ['fetch:config'] }) })).status).to.equal(401);
    expect((await call('POST', path, { body: importBody(), token: serviceToken({}) })).status).to.equal(403);
    expect((await call('POST', path, { body: importBody({ orgId: ORG_B }), token: serviceToken({ orgId: ORG_A }) })).status).to.equal(400);
    const made = await call('POST', path, { body: importBody(), token: serviceToken({ orgId: ORG_A }) });
    expect(made.status).to.equal(200);
    expect(made.json).to.deep.include({ outcome: 'created' });
    expect(made.json.binding).to.deep.include({ orgId: ORG_A, status: 'active' });
    expect(await world.bindings.listForOwner(ORG_B, 'user:u3')).to.deep.equal([]);
    assertNoCanary('import answer', made.json, { secrets: [ACCESS], shapes: 'vendor' });
  });

  it('QA23-F2: a due binding is claimed by the replica that scanned it; another replica gets nothing until a write or the claim lapses', async () => {
    const token = serviceToken({ orgId: ORG_A });
    const imported = (await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/import`, { body: importBody(), token })).json;
    const scan = (holder: string) =>
      call('GET', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/due?before=6000&limit=10&holder=${holder}`, { token: serviceToken({}) });
    const owner = (holder: string) =>
      call('GET', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/owner?installation=${instId(1)}&owner=user:u3&holder=${holder}`, { token });
    expect((await call('GET', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/due?before=6000`, { token: serviceToken({}) })).status).to.equal(400);
    expect((await call('GET', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/owner?installation=${instId(1)}&owner=user:u3`, { token })).status).to.equal(400);
    expect((await scan('replica-a')).json.bindings.map((b: { id: string }) => b.id)).to.deep.equal([imported.binding.id]);
    expect((await scan('replica-b')).json.bindings).to.deep.equal([]);
    expect((await owner('replica-b')).json.binding).to.deep.include({ id: imported.binding.id, claimed: false });
    expect((await scan('replica-a')).json.bindings).to.have.length(1);
    world.clock.now += REFRESH_CLAIM_MS;
    expect((await scan('replica-b')).json.bindings).to.have.length(1);
    const failed = await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/${imported.binding.id}/refresh-failed`, {
      body: { expectedRevision: 1, code: 'server_error', permanent: false },
      token,
    });
    expect(failed.status).to.equal(200);
    expect((await owner('replica-a')).json.binding).to.deep.include({ revision: 2, claimed: true });
  });

  it('IN-2: import, refresh outcomes, the due scan, revoke and revert over HTTP', async () => {
    const token = serviceToken({ orgId: ORG_A });
    const imported = (await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/import`, { body: importBody(), token })).json;
    expect((await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/import`, { body: importBody(), token })).json.outcome).to.equal(
      'unchanged',
    );
    const due = await call('GET', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/due?before=6000&limit=10&holder=replica-a`, { token: serviceToken({}) });
    expect(due.json.bindings).to.deep.equal([
      {
        id: imported.binding.id,
        orgId: ORG_A,
        installation: instId(1),
        owner: 'user:u3',
        mode: 'oauth',
        revision: 1,
        expiresAt: 5_000,
        path: imported.binding.secret.path,
        claimed: true,
      },
    ]);
    const owned = await call('GET', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/owner?installation=${instId(1)}&owner=user:u3&holder=replica-a`, { token });
    expect(owned.json.binding).to.deep.equal(due.json.bindings[0]);
    const elsewhere = await call('GET', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/owner?installation=${instId(1)}&owner=user:u3&holder=replica-a`, {
      token: serviceToken({ orgId: ORG_B }),
    });
    expect(elsewhere.json.binding).to.equal(null);
    const failed = await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/${imported.binding.id}/refresh-failed`, {
      body: { expectedRevision: 1, code: 'server_error', permanent: false },
      token,
    });
    expect(failed.json.binding).to.deep.include({ status: 'active', revision: 2, lastError: { code: 'server_error', at: world.clock.now } });
    const refreshed = await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/${imported.binding.id}/refreshed`, {
      body: { expectedRevision: 2, material: { kind: 'record', value: { refreshed: true }, expiresAt: 90_000 } },
      token,
    });
    expect(refreshed.json.binding).to.deep.include({ status: 'active', revision: 3, expiresAt: 90_000, lastError: null });
    const stale = await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/${imported.binding.id}/refreshed`, {
      body: { expectedRevision: 2, material: { kind: 'record', value: { lost: true } } },
      token,
    });
    expect(stale.status).to.equal(409);
    const otherOrg = await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/${imported.binding.id}/refreshed`, {
      body: { expectedRevision: 3, material: { kind: 'record', value: { foreign: true } } },
      token: serviceToken({ orgId: ORG_B }),
    });
    expect(otherOrg.status).to.equal(404);
    const reverted = await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/import/revert`, {
      body: { installation: instId(1), owner: 'user:u3' },
      token,
    });
    expect(reverted.json).to.deep.include({ changedSinceImport: true });
    expect(await world.bindings.get(ORG_A, imported.binding.id)).to.equal(null);
    const native = await bind('user:u3', instId(2));
    const notImported = await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/import/revert`, {
      body: { installation: instId(2), owner: 'user:u3' },
      token,
    });
    expect(notImported.status).to.equal(409);
    const revoked = await call('POST', `${CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX}/revoke`, {
      body: { installation: instId(2), owner: 'user:u3' },
      token: serviceToken({ orgId: ORG_A, userId: 'u3' }),
    });
    expect(revoked.json.binding).to.deep.include({ id: native.id, status: 'revoked' });
    expect(JSON.stringify(await world.configEntries(ORG_A))).to.include('user:u3');
  });
});
