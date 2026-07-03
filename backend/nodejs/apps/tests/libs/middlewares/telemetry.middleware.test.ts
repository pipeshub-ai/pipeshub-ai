import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'events';
import { metricsMiddleware } from '../../../src/libs/middlewares/telemetry.middleware';
import * as httpMetricsModule from '../../../src/libs/services/telemetry/modules/http-metrics';

type FinishableResponse = EventEmitter & { statusCode: number };

function mockRes(statusCode = 200): FinishableResponse {
  const res = new EventEmitter() as FinishableResponse;
  res.statusCode = statusCode;
  return res;
}

function mockReq(overrides: Record<string, unknown> = {}): any {
  return {
    method: 'GET',
    path: '/api/v1/users',
    baseUrl: '',
    ...overrides,
  };
}

describe('telemetry middleware', () => {
  let sandbox: sinon.SinonSandbox;
  let recordStub: sinon.SinonStub;

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    recordStub = sandbox.stub(httpMetricsModule, 'recordHttpRequest');
  });

  afterEach(() => {
    sandbox.restore();
  });

  it('should call next() and record nothing until the response finishes', () => {
    const middleware = metricsMiddleware();
    const req = mockReq();
    const res = mockRes();
    const next = sinon.stub();

    middleware(req, res as any, next);

    expect(next.calledOnce).to.be.true;
    expect(recordStub.called).to.be.false;
  });

  it('should record method, status, and a positive duration on finish', () => {
    const middleware = metricsMiddleware();
    const req = mockReq({ method: 'POST' });
    const res = mockRes(201);

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    expect(recordStub.calledOnce).to.be.true;
    const [route, method, status, , duration] = recordStub.firstCall.args;
    expect(route).to.equal('/api/v1/users');
    expect(method).to.equal('POST');
    expect(status).to.equal(201);
    expect(duration).to.be.a('number').and.to.be.at.least(0);
  });

  it('should prefer the matched Express route template over the raw path', () => {
    const middleware = metricsMiddleware();
    const req = mockReq({
      baseUrl: '/api/v1/users',
      path: '/507f1f77bcf86cd799439011',
      route: { path: '/:userId' },
    });
    const res = mockRes();

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    expect(recordStub.firstCall.args[0]).to.equal('/api/v1/users/:userId');
  });

  it('should normalize id-like segments when no route template matched (404s stay low-cardinality)', () => {
    const middleware = metricsMiddleware();
    const req = mockReq({
      path: '/api/v1/users/507f1f77bcf86cd799439011/items/42/e2c1a1f0-1234-4abc-9def-1234567890ab',
    });
    const res = mockRes(404);

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    expect(recordStub.firstCall.args[0]).to.equal(
      '/api/v1/users/:id/items/:id/:id',
    );
  });

  it('should record "/" for an empty path', () => {
    const middleware = metricsMiddleware();
    const req = mockReq({ path: '' });
    const res = mockRes();

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    expect(recordStub.firstCall.args[0]).to.equal('/');
  });

  it('should extract org and email-domain from the authenticated user', () => {
    const middleware = metricsMiddleware();
    const req = mockReq({
      user: { orgId: 'org-42', email: 'jane@Acme.IO' },
    });
    const res = mockRes();

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    const [, , , orgId, , domain] = recordStub.firstCall.args;
    expect(orgId).to.equal('org-42');
    expect(domain).to.equal('acme.io');
  });

  it('should fall back to "unknown" org/domain for unauthenticated requests', () => {
    const middleware = metricsMiddleware();
    const req = mockReq();
    const res = mockRes();

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    const [, , , orgId, , domain] = recordStub.firstCall.args;
    expect(orgId).to.equal('unknown');
    expect(domain).to.equal('unknown');
  });

  it('should attach only once per request even when applied twice', () => {
    const middleware = metricsMiddleware();
    const req = mockReq();
    const res = mockRes();
    const next1 = sinon.stub();
    const next2 = sinon.stub();

    middleware(req, res as any, next1);
    middleware(req, res as any, next2);
    res.emit('finish');

    expect(next1.calledOnce).to.be.true;
    expect(next2.calledOnce).to.be.true;
    expect(recordStub.calledOnce).to.be.true;
  });
});
