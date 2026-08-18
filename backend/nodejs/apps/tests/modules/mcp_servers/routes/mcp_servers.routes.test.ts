import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import { createMcpServersRouter } from '../../../../src/modules/mcp_servers/routes/mcp_servers.routes'

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function makeContainer(overrides: Record<string, any> = {}) {
  const mockAuthMiddleware = {
    authenticate: (_req: any, _res: any, next: any) => next(),
    ...overrides.authMiddleware,
  }
  const mockAppConfig = {
    connectorBackend: 'http://localhost:8088',
    ...overrides.appConfig,
  }
  const container: any = {
    get: sinon.stub().callsFake((key: string) => {
      if (key === 'AppConfig') return mockAppConfig
      if (key === 'AuthMiddleware') return mockAuthMiddleware
      return undefined
    }),
  }
  return { container, mockAuthMiddleware, mockAppConfig }
}

function getRoutes(router: any) {
  return router.stack
    .filter((layer: any) => layer.route)
    .map((layer: any) => ({
      path: layer.route.path as string,
      methods: layer.route.methods as Record<string, boolean>,
      handlers: layer.route.stack as any[],
    }))
}

function findRoute(router: any, path: string, method: string) {
  return getRoutes(router).find(
    (r: any) => r.path === path && r.methods[method.toLowerCase()],
  )
}

describe('mcp_servers/routes/mcp_servers.routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('createMcpServersRouter', () => {
    it('should register the full expected route set', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)

      const expected: Array<[string, string]> = [
        ['get', '/catalog'],
        ['get', '/catalog/:typeId'],
        ['get', '/my-mcp-servers'],
        ['get', '/oauth/callback'],
        ['get', '/instances'],
        ['post', '/instances'],
        ['get', '/instances/:instanceId'],
        ['put', '/instances/:instanceId'],
        ['delete', '/instances/:instanceId'],
        ['get', '/instances/:instanceId/tools'],
        ['post', '/instances/:instanceId/authenticate'],
        ['put', '/instances/:instanceId/credentials'],
        ['delete', '/instances/:instanceId/credentials'],
        ['post', '/instances/:instanceId/auto-authenticate'],
        ['post', '/instances/:instanceId/reauthenticate'],
        ['get', '/instances/:instanceId/oauth/authorize'],
        ['post', '/instances/:instanceId/oauth/refresh'],
        ['get', '/instances/:instanceId/oauth-config'],
        ['put', '/instances/:instanceId/oauth-config'],
        ['get', '/agents/:agentKey'],
        ['post', '/agents/:agentKey/instances/:instanceId/authenticate'],
        ['put', '/agents/:agentKey/instances/:instanceId/credentials'],
        ['delete', '/agents/:agentKey/instances/:instanceId/credentials'],
        ['post', '/agents/:agentKey/instances/:instanceId/reauthenticate'],
        ['get', '/agents/:agentKey/instances/:instanceId/oauth/authorize'],
      ]

      for (const [method, path] of expected) {
        const route = findRoute(router, path, method)
        expect(route, `${method.toUpperCase()} ${path} should be registered`).to.exist
      }
    })

    it('every route should have authenticate + scope-check + handler middleware', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)

      for (const route of getRoutes(router)) {
        expect(route.handlers.length).to.be.greaterThanOrEqual(
          3,
          `Route ${route.path} should have at least 3 handlers (authenticate, requireScopes, controller)`,
        )
      }
    })
  })

  // -------------------------------------------------------------------------
  // Handler wiring smoke tests — confirms each route ultimately proxies to
  // the correct Python connectors path (deep coverage lives in
  // mcp_servers.controller.test.ts; this just confirms route -> handler wiring).
  // -------------------------------------------------------------------------
  describe('route -> controller wiring', () => {
    let executeStub: sinon.SinonStub

    beforeEach(() => {
      executeStub = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: { success: true } })
      sinon.stub(connectorUtils, 'handleConnectorResponse').callsFake((response: any, res: any) => {
        res.status(response.statusCode).json(response.data)
      })
      sinon.stub(connectorUtils, 'handleBackendError').callsFake((err: any) => err)
    })

    function getLastHandler(router: any, path: string, method: string) {
      const route = findRoute(router, path, method)
      if (!route) return undefined
      const handles = route.handlers.map((s: any) => s.handle)
      return handles[handles.length - 1]
    }

    function createMockReqRes(params: Record<string, string> = {}) {
      const req: any = {
        headers: { authorization: 'Bearer tok' },
        body: {},
        params,
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
      }
      const res: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
      }
      const next = sinon.stub()
      return { req, res, next }
    }

    it('GET /instances/:instanceId/tools should hit the tools endpoint', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const handler = getLastHandler(router, '/instances/:instanceId/tools', 'get')
      expect(handler).to.be.a('function')

      const { req, res, next } = createMockReqRes({ instanceId: 'inst-1' })
      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      expect(executeStub.firstCall.args[0]).to.equal('http://localhost:8088/api/v1/mcp-servers/instances/inst-1/tools')
      expect(res.status.calledWith(200)).to.be.true
    })

    it('DELETE /instances/:instanceId should use DELETE method', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const handler = getLastHandler(router, '/instances/:instanceId', 'delete')

      const { req, res, next } = createMockReqRes({ instanceId: 'inst-1' })
      await handler(req, res, next)

      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
    })

    it('POST /instances should forward the request body', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const handler = getLastHandler(router, '/instances', 'post')

      const { req, res, next } = createMockReqRes()
      req.body = { name: 'My Server', typeId: 'brave-search', transport: 'stdio', authMode: 'api_token' }
      await handler(req, res, next)

      expect(executeStub.firstCall.args[3]).to.deep.equal(req.body)
    })

    it('GET /agents/:agentKey should hit the agent-scoped MCP servers endpoint', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const handler = getLastHandler(router, '/agents/:agentKey', 'get')

      const { req, res, next } = createMockReqRes({ agentKey: 'agent-1' })
      await handler(req, res, next)

      expect(executeStub.firstCall.args[0]).to.equal('http://localhost:8088/api/v1/mcp-servers/agents/agent-1')
      expect(executeStub.firstCall.args[1]).to.equal('GET')
    })

    it('PUT /agents/:agentKey/instances/:instanceId/credentials should forward method + body', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const handler = getLastHandler(router, '/agents/:agentKey/instances/:instanceId/credentials', 'put')

      const { req, res, next } = createMockReqRes({ agentKey: 'agent-1', instanceId: 'inst-1' })
      req.body = { auth: { apiToken: 'tok' } }
      await handler(req, res, next)

      expect(executeStub.firstCall.args[0]).to.equal(
        'http://localhost:8088/api/v1/mcp-servers/agents/agent-1/instances/inst-1/credentials',
      )
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
      expect(executeStub.firstCall.args[3]).to.deep.equal(req.body)
    })
  })

  describe('requireScopes gating', () => {
    it('should reject an OAuth token missing the mcp:write scope on a write route', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances', 'post')
      expect(route).to.exist

      const req: any = {
        headers: {},
        body: {},
        params: {},
        query: {},
        user: { userId: 'u1', orgId: 'o1', isOAuth: true, oauthScopes: ['mcp:read'] },
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      // handlers[0] = authenticate, handlers[1] = requireScopes(MCP_WRITE)
      const requireScopesMiddleware = route!.handlers[1].handle
      requireScopesMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const err = next.firstCall.args[0]
      expect(err).to.exist
      expect(err.message).to.include('Insufficient scope')
    })

    it('should allow a regular (non-OAuth) JWT through the scope check', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances', 'post')

      const req: any = {
        headers: {},
        body: {},
        params: {},
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
      }
      const res: any = {}
      const next = sinon.stub()

      const requireScopesMiddleware = route!.handlers[1].handle
      requireScopesMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args.length).to.equal(0)
    })

    it('should reject an OAuth token missing agent:write on an agent-scoped write route', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/agents/:agentKey/instances/:instanceId/authenticate', 'post')
      expect(route).to.exist

      const req: any = {
        headers: {},
        body: {},
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1', isOAuth: true, oauthScopes: ['agent:read'] },
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      const requireScopesMiddleware = route!.handlers[1].handle
      requireScopesMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const err = next.firstCall.args[0]
      expect(err).to.exist
      expect(err.message).to.include('Insufficient scope')
    })

    it('should reject an OAuth token missing agent:read on GET /agents/:agentKey', async () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/agents/:agentKey', 'get')
      expect(route).to.exist

      const req: any = {
        headers: {},
        body: {},
        params: { agentKey: 'agent-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1', isOAuth: true, oauthScopes: [] },
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      const requireScopesMiddleware = route!.handlers[1].handle
      requireScopesMiddleware(req, res, next)

      expect(next.calledOnce).to.be.true
      const err = next.firstCall.args[0]
      expect(err).to.exist
      expect(err.message).to.include('Insufficient scope')
    })
  })
})
