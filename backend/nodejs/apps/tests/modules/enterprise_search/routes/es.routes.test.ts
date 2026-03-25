import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConversationalRouter, createSemanticSearchRouter, createAgentConversationalRouter } from '../../../../src/modules/enterprise_search/routes/es.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Enterprise Search Routes', () => {
  let container: Container
  let mockAuthMiddleware: any
  let mockAppConfig: any

  beforeEach(() => {
    container = new Container()

    mockAuthMiddleware = {
      authenticate: (_req: any, _res: any, next: any) => next(),
      scopedTokenValidator: sinon.stub().returns((_req: any, _res: any, next: any) => next()),
    }

    mockAppConfig = {
      aiBackend: 'http://localhost:8000',
      connectorBackend: 'http://localhost:8088',
      jwtSecret: 'test-jwt-secret',
      scopedJwtSecret: 'test-scoped-secret',
      cmBackend: 'http://localhost:3001',
      iamBackend: 'http://localhost:3001',
    }

    const mockPrometheusService = {
      recordActivity: sinon.stub(),
      getMetrics: sinon.stub().resolves(''),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind(PrometheusService).toConstantValue(mockPrometheusService as any)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('should return a valid Express router', () => {
    const router = createConversationalRouter(container)
    expect(router).to.exist
    expect(router).to.have.property('stack')
  })

  it('should register conversation create routes', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const createRoute = routes.find((r: any) => r.path === '/create' && r.methods.post)
    expect(createRoute).to.exist
  })

  it('should register internal create route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const internalCreate = routes.find((r: any) => r.path === '/internal/create' && r.methods.post)
    expect(internalCreate).to.exist
  })

  it('should register stream route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const streamRoute = routes.find((r: any) => r.path === '/stream' && r.methods.post)
    expect(streamRoute).to.exist
  })

  it('should register messages route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const messagesRoute = routes.find((r: any) => r.path === '/:conversationId/messages' && r.methods.post)
    expect(messagesRoute).to.exist
  })

  it('should register conversation list route (GET /)', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const listRoute = routes.find((r: any) => r.path === '/' && r.methods.get)
    expect(listRoute).to.exist
  })

  it('should register conversation detail route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const detailRoute = routes.find((r: any) => r.path === '/:conversationId' && r.methods.get)
    expect(detailRoute).to.exist
  })

  it('should register delete conversation route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const deleteRoute = routes.find((r: any) => r.path === '/:conversationId' && r.methods.delete)
    expect(deleteRoute).to.exist
  })

  it('should register regenerate route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const regenerateRoute = routes.find((r: any) => r.path === '/:conversationId/message/:messageId/regenerate' && r.methods.post)
    expect(regenerateRoute).to.exist
  })

  it('should register title update route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:conversationId/title')
  })

  it('should register archive routes', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/show/archives')
  })

  it('should register share/unshare routes', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:conversationId/share')
    expect(paths).to.include('/:conversationId/unshare')
  })

  it('should register feedback route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:conversationId/message/:messageId/feedback')
  })

  // -----------------------------------------------------------------------
  // Additional route registrations
  // -----------------------------------------------------------------------
  it('should register internal stream route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const internalStream = routes.find((r: any) => r.path === '/internal/stream' && r.methods.post)
    expect(internalStream).to.exist
  })

  it('should register internal messages stream route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const route = routes.find((r: any) => r.path === '/internal/:conversationId/messages/stream' && r.methods.post)
    expect(route).to.exist
  })

  it('should register unarchive route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:conversationId/unarchive')
  })

  it('should register search routes', () => {
    const router = createSemanticSearchRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    // Search router has POST / for search and GET / for search history
    expect(paths).to.include('/')
  })

  it('should register search detail and delete routes', () => {
    const router = createSemanticSearchRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:searchId')
  })

  it('should register agent conversation routes', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/create')
    expect(paths).to.include('/')
    expect(paths).to.include('/:agentKey')
  })

  it('should register agent template routes', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/template')
    expect(paths).to.include('/template/:templateId')
  })

  it('should register agents CRUD routes', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/')
    expect(paths).to.include('/:agentKey')
  })

  it('should register agent stream routes', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:agentKey/conversations/stream')
  })

  it('should register agent messages routes', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:agentKey/conversations/:conversationId/messages')
    expect(paths).to.include('/:agentKey/conversations/:conversationId/messages/stream')
  })

  it('should register internal agent stream route', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:agentKey/conversations/internal/stream')
    expect(paths).to.include('/:agentKey/conversations/internal/:conversationId/messages/stream')
  })

  it('should register agent share/unshare routes', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:agentKey/share')
    expect(paths).to.include('/:agentKey/unshare')
  })

  it('should register agent permissions routes', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:agentKey/permissions')
  })

  it('should register agent regenerate route', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:agentKey/conversations/:conversationId/message/:messageId/regenerate')
  })

  it('should register agent tools route', () => {
    const router = createAgentConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/tools/list')
  })

  it('should register search share/unshare routes', () => {
    const router = createSemanticSearchRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:searchId/share')
    expect(paths).to.include('/:searchId/unshare')
  })

  it('should register search archive/unarchive routes', () => {
    const router = createSemanticSearchRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/:searchId/archive')
    expect(paths).to.include('/:searchId/unarchive')
  })

  it('should register conversation messages stream route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const route = routes.find((r: any) => r.path === '/:conversationId/messages/stream' && r.methods.post)
    expect(route).to.exist
  })

  it('should register search history delete route', () => {
    const router = createSemanticSearchRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))
    const route = routes.find((r: any) => r.path === '/' && r.methods.delete)
    expect(route).to.exist
  })

  it('should register total expected route count', () => {
    const convRouter = createConversationalRouter(container)
    const searchRouter = createSemanticSearchRouter(container)
    const agentRouter = createAgentConversationalRouter(container)
    const convRoutes = convRouter.stack.filter((layer: any) => layer.route).length
    const searchRoutes = searchRouter.stack.filter((layer: any) => layer.route).length
    const agentRoutes = agentRouter.stack.filter((layer: any) => layer.route).length
    const total = convRoutes + searchRoutes + agentRoutes
    // Should have a significant number of routes across all three routers
    expect(total).to.be.greaterThanOrEqual(30)
  })

  it('should register middleware chains on each route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack.filter((layer: any) => layer.route)

    for (const routeLayer of routes) {
      const handlerCount = routeLayer.route.stack.length
      expect(handlerCount).to.be.greaterThanOrEqual(1,
        `Route ${routeLayer.route.path} should have at least 1 handler`)
    }
  })

  it('should register archives route', () => {
    const router = createConversationalRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({ path: layer.route.path }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/show/archives')
  })

  describe('route handler invocations', () => {
    function findRouteHandler(router: any, path: string, method: string) {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === path && l.route.methods[method],
      )
      if (!layer) return undefined
      const handlers = layer.route.stack.map((s: any) => s.handle)
      return handlers[handlers.length - 1]
    }

    function createMockReqRes() {
      const mockReq: any = {
        user: { userId: 'user123', orgId: 'org123' },
        tokenPayload: { userId: 'user123', orgId: 'org123' },
        body: {},
        params: { conversationId: 'conv123', messageId: 'msg123', searchId: 'search123', agentKey: 'agent123', templateId: 'tmpl123' },
        query: {},
        headers: {},
      }
      const mockRes: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
      }
      const mockNext = sinon.stub()
      return { mockReq, mockRes, mockNext }
    }

    it('POST /updateAppConfig handler on search router should update config and respond 200', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config')
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').resolves(mockAppConfig as any)

      const router = createSemanticSearchRouter(container)
      const handler = findRouteHandler(router, '/updateAppConfig', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockRes.status.calledWith(200)).to.be.true
      expect(mockRes.json.calledOnce).to.be.true
      const response = mockRes.json.firstCall.args[0]
      expect(response.message).to.include('updated successfully')

      loadStub.restore()
    })

    it('POST /updateAppConfig handler on search router should call next on error', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config')
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').rejects(new Error('Config load failed'))

      const router = createSemanticSearchRouter(container)
      const handler = findRouteHandler(router, '/updateAppConfig', 'post')

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true

      loadStub.restore()
    })

    it('should have correct handler count on all conversational routes', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack.filter((layer: any) => layer.route)

      for (const routeLayer of routes) {
        expect(routeLayer.route.stack.length).to.be.greaterThanOrEqual(1,
          `Conversational route ${routeLayer.route.path} should have at least 1 handler`)
      }
    })

    it('should have correct handler count on all semantic search routes', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack.filter((layer: any) => layer.route)

      for (const routeLayer of routes) {
        expect(routeLayer.route.stack.length).to.be.greaterThanOrEqual(1,
          `Search route ${routeLayer.route.path} should have at least 1 handler`)
      }
    })

    it('should have correct handler count on all agent conversational routes', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack.filter((layer: any) => layer.route)

      for (const routeLayer of routes) {
        expect(routeLayer.route.stack.length).to.be.greaterThanOrEqual(1,
          `Agent route ${routeLayer.route.path} should have at least 1 handler`)
      }
    })

    it('agent router should register all agent CRUD operations', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      // Verify POST /create (agent)
      const createRoute = routes.find((r: any) => r.path === '/create' && r.methods.post)
      expect(createRoute).to.exist

      // Verify GET /:agentKey
      const getRoute = routes.find((r: any) => r.path === '/:agentKey' && r.methods.get)
      expect(getRoute).to.exist

      // Verify PUT /:agentKey
      const putRoute = routes.find((r: any) => r.path === '/:agentKey' && r.methods.put)
      expect(putRoute).to.exist

      // Verify DELETE /:agentKey
      const deleteRoute = routes.find((r: any) => r.path === '/:agentKey' && r.methods.delete)
      expect(deleteRoute).to.exist
    })

    it('agent router should register conversation management routes', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      // GET conversations for agent
      const listConvs = routes.find((r: any) => r.path === '/:agentKey/conversations' && r.methods.get)
      expect(listConvs).to.exist

      // GET specific conversation
      const getConv = routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId' && r.methods.get)
      expect(getConv).to.exist

      // DELETE specific conversation
      const deleteConv = routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId' && r.methods.delete)
      expect(deleteConv).to.exist
    })

    it('search router should register all search CRUD operations', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      // POST / for search
      const searchRoute = routes.find((r: any) => r.path === '/' && r.methods.post)
      expect(searchRoute).to.exist

      // GET / for search history
      const historyRoute = routes.find((r: any) => r.path === '/' && r.methods.get)
      expect(historyRoute).to.exist

      // GET /:searchId
      const getRoute = routes.find((r: any) => r.path === '/:searchId' && r.methods.get)
      expect(getRoute).to.exist

      // DELETE /:searchId
      const deleteRoute = routes.find((r: any) => r.path === '/:searchId' && r.methods.delete)
      expect(deleteRoute).to.exist

      // DELETE / for clearing history
      const clearRoute = routes.find((r: any) => r.path === '/' && r.methods.delete)
      expect(clearRoute).to.exist
    })
  })
})
