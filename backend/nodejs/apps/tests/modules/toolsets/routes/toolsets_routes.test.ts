import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import { createToolsetsRouter } from '../../../../src/modules/toolsets/routes/toolsets_routes'

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
      if (key === 'KeyValueStoreService') return { watchKey: sinon.stub() }
      return undefined
    }),
    isBound: sinon.stub().returns(false),
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
    (r) => r.path === path && r.methods[method.toLowerCase()],
  )
}

describe('toolsets/routes/toolsets_routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -------------------------------------------------------------------------
  // Basic smoke test (existing behaviour)
  // -------------------------------------------------------------------------
  describe('createToolsetsRouter', () => {
    it('should create a router with expected core routes', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)

      expect(router).to.exist
      const paths = getRoutes(router).map((r) => r.path)
      expect(paths).to.include('/registry')
      expect(paths).to.include('/')
      expect(paths).to.include('/instances')
      expect(paths).to.include('/my-toolsets')
    })
  })

  // -------------------------------------------------------------------------
  // Agent-scoped toolset routes
  // -------------------------------------------------------------------------
  describe('agent-scoped toolset routes', () => {
    it('should register GET /agents/:agentKey', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const route = findRoute(router, '/agents/:agentKey', 'get')
      expect(route).to.exist
    })

    it('should register POST /agents/:agentKey/instances/:instanceId/authenticate', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/authenticate',
        'post',
      )
      expect(route).to.exist
    })

    it('should register PUT /agents/:agentKey/instances/:instanceId/credentials', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/credentials',
        'put',
      )
      expect(route).to.exist
    })

    it('should register DELETE /agents/:agentKey/instances/:instanceId/credentials', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/credentials',
        'delete',
      )
      expect(route).to.exist
    })

    it('should register POST /agents/:agentKey/instances/:instanceId/reauthenticate', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/reauthenticate',
        'post',
      )
      expect(route).to.exist
    })

    it('should register GET /agents/:agentKey/instances/:instanceId/oauth/authorize', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/oauth/authorize',
        'get',
      )
      expect(route).to.exist
    })

    it('all agent routes should have at least 1 middleware handler', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const agentRoutes = getRoutes(router).filter((r) =>
        r.path.startsWith('/agents/'),
      )
      expect(agentRoutes.length).to.be.greaterThanOrEqual(6)
      for (const route of agentRoutes) {
        expect(route.handlers.length).to.be.greaterThanOrEqual(
          1,
          `Route ${route.path} should have at least 1 handler`,
        )
      }
    })

    it('GET /agents/:agentKey should include validation middleware', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const route = findRoute(router, '/agents/:agentKey', 'get')
      // Route has: authenticate + metricsMiddleware + ValidationMiddleware.validate + handler = ≥3
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('PUT /agents/:agentKey/instances/:instanceId/credentials should include validation middleware', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/credentials',
        'put',
      )
      // authenticate + metricsMiddleware + ValidationMiddleware.validate + handler = ≥3
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('routes without extra validation should have at least authenticate + metrics + handler', () => {
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const noValidationPaths = [
        { path: '/agents/:agentKey/instances/:instanceId/authenticate', method: 'post' },
        { path: '/agents/:agentKey/instances/:instanceId/credentials', method: 'delete' },
        { path: '/agents/:agentKey/instances/:instanceId/reauthenticate', method: 'post' },
        { path: '/agents/:agentKey/instances/:instanceId/oauth/authorize', method: 'get' },
      ]
      for (const { path, method } of noValidationPaths) {
        const route = findRoute(router, path, method)
        expect(route).to.exist
        expect(route!.handlers.length).to.be.greaterThanOrEqual(
          2,
          `${method.toUpperCase()} ${path} should have at least 2 handlers`,
        )
      }
    })
  })

  // -------------------------------------------------------------------------
  // Agent route handler smoke tests
  // -------------------------------------------------------------------------
  describe('agent route handler invocations', () => {
    let executeStub: sinon.SinonStub
    let handleResponseStub: sinon.SinonStub

    beforeEach(() => {
      executeStub = sinon.stub(connectorUtils, 'executeConnectorCommand')
      handleResponseStub = sinon.stub(connectorUtils, 'handleConnectorResponse')
      sinon.stub(connectorUtils, 'handleBackendError').callsFake((err: any) => err)
    })

    function createMockReqRes(params: Record<string, string> = {}) {
      const req: any = {
        headers: { authorization: 'Bearer tok' },
        body: {},
        params: { agentKey: 'agent-1', instanceId: 'inst-1', ...params },
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

    function getLastHandler(router: any, path: string, method: string) {
      const route = findRoute(router, path, method)
      if (!route) return undefined
      const handles = route.handlers.map((s: any) => s.handle)
      return handles[handles.length - 1]
    }

    it('GET /agents/:agentKey handler should forward to connector backend', async () => {
      executeStub.resolves({ statusCode: 200, data: { toolsets: [] } })
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(router, '/agents/:agentKey', 'get')
      expect(handler).to.be.a('function')

      const { req, res, next } = createMockReqRes()
      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/toolsets/agents/agent-1')
      expect(handleResponseStub.calledOnce).to.be.true
    })

    it('GET /agents/:agentKey handler should append query params', async () => {
      executeStub.resolves({ statusCode: 200, data: {} })
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(router, '/agents/:agentKey', 'get')

      const { req, res, next } = createMockReqRes()
      req.query = { search: 'jira', page: '2', limit: '10', includeRegistry: 'true' }
      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('search=jira')
      expect(url).to.include('page=2')
      expect(url).to.include('limit=10')
      expect(url).to.include('includeRegistry=true')
    })

    it('GET /agents/:agentKey handler should call next on connector error', async () => {
      executeStub.rejects(new Error('network error'))
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(router, '/agents/:agentKey', 'get')

      const { req, res, next } = createMockReqRes()
      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('POST .../authenticate handler should forward body to connector', async () => {
      executeStub.resolves({ statusCode: 200, data: { isAuthenticated: true } })
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(
        router,
        '/agents/:agentKey/instances/:instanceId/authenticate',
        'post',
      )
      expect(handler).to.be.a('function')

      const { req, res, next } = createMockReqRes()
      req.body = { auth: { apiToken: 'my-token' } }
      await handler(req, res, next)

      expect(executeStub.calledOnce).to.be.true
      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/toolsets/agents/agent-1/instances/inst-1/authenticate')
      expect(executeStub.firstCall.args[2]).to.deep.include(req.headers)
    })

    it('POST .../authenticate handler should call next on error', async () => {
      executeStub.rejects(new Error('fail'))
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(
        router,
        '/agents/:agentKey/instances/:instanceId/authenticate',
        'post',
      )

      const { req, res, next } = createMockReqRes()
      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('PUT .../credentials handler should use PUT method and correct URL', async () => {
      executeStub.resolves({ statusCode: 200, data: { status: 'success' } })
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(
        router,
        '/agents/:agentKey/instances/:instanceId/credentials',
        'put',
      )
      expect(handler).to.be.a('function')

      const { req, res, next } = createMockReqRes()
      req.body = { auth: { apiToken: 'new-tok' } }
      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/toolsets/agents/agent-1/instances/inst-1/credentials')
      expect(executeStub.firstCall.args[1]).to.equal('PUT')
    })

    it('DELETE .../credentials handler should use DELETE method', async () => {
      executeStub.resolves({ statusCode: 200, data: { status: 'success' } })
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(
        router,
        '/agents/:agentKey/instances/:instanceId/credentials',
        'delete',
      )
      expect(handler).to.be.a('function')

      const { req, res, next } = createMockReqRes()
      await handler(req, res, next)

      expect(executeStub.firstCall.args[1]).to.equal('DELETE')
      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/toolsets/agents/agent-1/instances/inst-1/credentials')
    })

    it('POST .../reauthenticate handler should hit correct endpoint', async () => {
      executeStub.resolves({ statusCode: 200, data: { status: 'success' } })
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(
        router,
        '/agents/:agentKey/instances/:instanceId/reauthenticate',
        'post',
      )
      expect(handler).to.be.a('function')

      const { req, res, next } = createMockReqRes()
      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/toolsets/agents/agent-1/instances/inst-1/reauthenticate')
    })

    it('GET .../oauth/authorize handler should forward base_url query param', async () => {
      executeStub.resolves({ statusCode: 200, data: { authorizationUrl: 'https://oauth.example.com' } })
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(
        router,
        '/agents/:agentKey/instances/:instanceId/oauth/authorize',
        'get',
      )
      expect(handler).to.be.a('function')

      const { req, res, next } = createMockReqRes()
      req.query = { base_url: 'https://app.example.com' }
      await handler(req, res, next)

      const url: string = executeStub.firstCall.args[0]
      expect(url).to.include('/toolsets/agents/agent-1/instances/inst-1/oauth/authorize')
      expect(url).to.include('base_url=https%3A%2F%2Fapp.example.com')
    })

    it('GET .../oauth/authorize handler should call next on error', async () => {
      executeStub.rejects(new Error('oauth-fail'))
      const { container } = makeContainer()
      const router = createToolsetsRouter(container)
      const handler = getLastHandler(
        router,
        '/agents/:agentKey/instances/:instanceId/oauth/authorize',
        'get',
      )

      const { req, res, next } = createMockReqRes()
      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })
})
