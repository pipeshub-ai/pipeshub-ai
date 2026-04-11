import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createMcpServersRouter } from '../../../../src/modules/mcp_servers/routes/mcp_servers_routes'

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

describe('mcp_servers/routes/mcp_servers_routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('createMcpServersRouter', () => {
    it('should register exactly 17 routes', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const routes = getRoutes(router)
      expect(routes.length).to.equal(17)
    })

    it('should register GET /catalog with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/catalog', 'get')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register GET /catalog/:typeId with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/catalog/:typeId', 'get')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register GET /my-mcp-servers with auth and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/my-mcp-servers', 'get')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(2)
    })

    it('should register GET /instances with auth and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances', 'get')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(2)
    })

    it('should register POST /instances with auth and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances', 'post')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(2)
    })

    it('should register GET /instances/:instanceId with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances/:instanceId', 'get')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register PUT /instances/:instanceId with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances/:instanceId', 'put')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register DELETE /instances/:instanceId with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances/:instanceId', 'delete')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register POST /instances/:instanceId/authenticate with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances/:instanceId/authenticate', 'post')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register PUT /instances/:instanceId/credentials with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances/:instanceId/credentials', 'put')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register DELETE /instances/:instanceId/credentials with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances/:instanceId/credentials', 'delete')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register POST /instances/:instanceId/reauthenticate with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances/:instanceId/reauthenticate', 'post')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register GET /instances/:instanceId/tools with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/instances/:instanceId/tools', 'get')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register GET /agents/:agentKey with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(router, '/agents/:agentKey', 'get')
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register POST /agents/:agentKey/instances/:instanceId/authenticate with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/authenticate',
        'post',
      )
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register PUT /agents/:agentKey/instances/:instanceId/credentials with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/credentials',
        'put',
      )
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })

    it('should register DELETE /agents/:agentKey/instances/:instanceId/credentials with auth, validate, and metrics', () => {
      const { container } = makeContainer()
      const router = createMcpServersRouter(container)
      const route = findRoute(
        router,
        '/agents/:agentKey/instances/:instanceId/credentials',
        'delete',
      )
      expect(route).to.exist
      expect(route!.handlers.length).to.be.greaterThanOrEqual(3)
    })
  })
})
