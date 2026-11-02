import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'events';
import { metricsMiddleware } from '../../../src/libs/middlewares/telemetry.middleware';
import * as httpMetricsModule from '../../../src/libs/services/telemetry/modules/http-metrics';
import * as clientMetricsModule from '../../../src/libs/services/telemetry/modules/client-metrics';

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
    const req = mockReq({ method: 'POST', route: { path: '/api/v1/users' } });
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

  it('should record "unmatched" when no route template matched (404s, assets, and bot scans stay one series)', () => {
    const middleware = metricsMiddleware();
    const req = mockReq({
      path: '/assets/index-Bo3xK9qw.js',
    });
    const res = mockRes(404);

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    expect(recordStub.firstCall.args[0]).to.equal('unmatched');
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

  it('should stringify an ObjectId-like orgId instead of falling back to "unknown"', () => {
    const middleware = metricsMiddleware();
    const objectIdLike = {
      toString: () => '507f1f77bcf86cd799439011',
    };
    const req = mockReq({ user: { orgId: objectIdLike } });
    const res = mockRes();

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    expect(recordStub.firstCall.args[3]).to.equal('507f1f77bcf86cd799439011');
  });

  it('should fall back to "unknown" when orgId is a plain object', () => {
    const middleware = metricsMiddleware();
    const req = mockReq({ user: { orgId: { nested: true } } });
    const res = mockRes();

    middleware(req, res as any, sinon.stub());
    res.emit('finish');

    expect(recordStub.firstCall.args[3]).to.equal('unknown');
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

  describe('SDK and MCP clients', () => {
    let clientStub: sinon.SinonStub;
    let mcpStub: sinon.SinonStub;

    beforeEach(() => {
      clientStub = sandbox.stub(clientMetricsModule, 'recordClientRequest');
      mcpStub = sandbox.stub(clientMetricsModule, 'recordMcpMessages');
    });

    it('should pass an SDK request to the client metrics with no MCP transport', () => {
      const req = mockReq({
        method: 'POST',
        route: { path: '/api/v1/search' },
        context: { requestId: 'sdk-ts-u1-abc' },
      });
      const res = mockRes(200);

      metricsMiddleware()(req, res as any, sinon.stub());
      res.emit('finish');

      const [info, transport, route] = clientStub.firstCall.args;
      expect(info).to.deep.equal({ client: 'sdk', sdk: 'ts' });
      expect(transport).to.equal('none');
      expect(route).to.equal('/api/v1/search');
      expect(mcpStub.called).to.be.false;
    });

    it('should mark a hosted /mcp POST and count its JSON-RPC messages', () => {
      const body = { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'pipeshub_search' } };
      const req = mockReq({
        method: 'POST',
        path: '/',
        baseUrl: '/mcp',
        route: { path: '/' },
        body,
        context: { requestId: 'mcp-abc' },
      });
      const res = mockRes(200);

      metricsMiddleware()(req, res as any, sinon.stub());
      res.emit('finish');

      expect(clientStub.firstCall.args[1]).to.equal('hosted');
      expect(mcpStub.calledOnceWith(body, 200)).to.be.true;
    });

    it('should mark REST calls from the local MCP server as local', () => {
      const req = mockReq({ route: { path: '/api/v1/records' }, context: { requestId: 'mcp-u1-abc' } });
      const res = mockRes(200);

      metricsMiddleware()(req, res as any, sinon.stub());
      res.emit('finish');

      expect(clientStub.firstCall.args[1]).to.equal('local');
      expect(mcpStub.called).to.be.false;
    });
  });
});
