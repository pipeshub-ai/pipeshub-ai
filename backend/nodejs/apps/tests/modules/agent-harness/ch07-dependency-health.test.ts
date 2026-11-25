/**
 * CH-07 (and CH-06, -08, -09): an admin sees which store the harness found degraded and since when, at
 * GET /api/v1/agent-harness/admin/dependencies; a store recovers a minute after its last failure; no error text.
 */
import {
  addServiceHealthCheck,
  clearServiceHealthChecks,
} from '../../../src/modules/agent-harness/ops/service-health-checks';
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { MongoNetworkError } from 'mongodb';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import * as userAdminService from '../../../src/modules/user_management/services/user-admin.service';
import {
  DependencyHealth,
  dependencyHealth,
  WINDOW_MS,
} from '../../../src/modules/agent-harness/ops/dependency-health';
import { createAgentHarnessAdminRouter } from '../../../src/modules/agent-harness/transport/admin-routes';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { RedisRunReadyNotifier } from '../../../src/modules/agent-harness/adapters/redis/redis-run-ready';
import { bindAdminAuthorizer } from './support/admin-authorizer';

const OK = { status: 'ok', since: null };

describe('CH-07: the degraded-dependency view', () => {
  it('a store is degraded from its first failure until a minute passes without one', () => {
    let now = Date.parse('2026-10-30T12:00:00.000Z');
    const health = new DependencyHealth(() => now);
    expect(health.view()).to.deep.equal({ mongo: OK, kv: OK, broker: OK, blob: OK });
    health.report('kv');
    now += 30_000;
    health.report('kv');
    now += WINDOW_MS - 1;
    expect(health.view().kv).to.deep.equal({ status: 'degraded', since: '2026-10-30T12:00:00.000Z' });
    expect(health.view().mongo).to.deep.equal(OK);
    now += 2;
    expect(health.view().kv).to.deep.equal(OK);
    // A new failure after recovery starts a new streak.
    health.report('kv');
    expect(health.view().kv.since).to.equal(new Date(now).toISOString());
  });

  it('the places that meet an outage report it: a Mongo failover on a route, the run-ready signal on Redis', async () => {
    const report = sinon.spy(dependencyHealth, 'report');
    try {
      transportError(new MongoNetworkError('connection 7 to 10.0.0.3:27017 closed'));
      await new RedisRunReadyNotifier({
        keyNamespace: '',
        getClient: () => ({ publish: () => Promise.reject(new Error('ECONNREFUSED')) }),
      } as never).notify([{ orgId: 'o', runId: 'r', kind: 'admitted' } as never]);
      expect(report.args.map((args) => args[0])).to.deep.equal(['mongo', 'kv']);
    } finally {
      report.restore();
    }
  });
});

describe('CH-07: GET /api/v1/agent-harness/admin/dependencies', () => {
  let server: Server;
  let base: string;
  let admin = true;

  before(() => {
    sinon.stub(userAdminService, 'isUserOrgAdmin').callsFake(() => Promise.resolve(admin));
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { userId: 'u-admin', orgId: 'org-1' };
        req.tokenPayload = { userId: 'u-admin', orgId: 'org-1' };
        next();
      },
    } as never);
    const app = express();
    bindAdminAuthorizer(container);
    app.use('/api/v1/agent-harness/admin', createAgentHarnessAdminRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = app.listen(0);
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1/agent-harness/admin/dependencies`;
  });

  after(() => {
    sinon.restore();
    server.close();
  });

  it('answers each store plainly, never the error text', async () => {
    admin = true;
    transportError(new MongoNetworkError('connection 7 to 10.0.0.3:27017 closed'));
    const answer = await fetch(base);
    expect(answer.status).to.equal(200);
    const body = (await answer.json()) as Record<string, { status: string; since: string | null }>;
    expect(Object.keys(body).sort()).to.deep.equal(['blob', 'broker', 'kv', 'mongo']);
    expect(body['mongo']?.status).to.equal('degraded');
    expect(body['mongo']?.since).to.be.a('string');
    expect(JSON.stringify(body)).to.not.include('10.0.0.3');
  });

  it('an added service check is answered under its name, with its plain words', async () => {
    admin = true;
    let ok = false;
    addServiceHealthCheck({
      name: 'publishedSettings',
      critical: true,
      view: () =>
        ok
          ? { status: 'ok', since: null }
          : { status: 'degraded', since: '2026-01-01T00:00:00.000Z', detail: 'still publishing' },
    });
    try {
      const pending = (await (await fetch(base)).json()) as Record<string, { status: string; detail?: string }>;
      expect(pending['publishedSettings']?.status).to.equal('degraded');
      expect(pending['publishedSettings']?.detail).to.equal('still publishing');
      ok = true;
      const ready = (await (await fetch(base)).json()) as Record<string, { status: string; detail?: string }>;
      expect(ready['publishedSettings']).to.deep.equal({ status: 'ok', since: null });
    } finally {
      clearServiceHealthChecks();
    }
  });

  it('is for org admins only', async () => {
    admin = false;
    expect((await fetch(base)).status).to.equal(403);
    admin = true;
  });
});
