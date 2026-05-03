import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConversationalRouter, createSemanticSearchRouter, createAgentConversationalRouter } from '../../../../src/modules/enterprise_search/routes/es.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Enterprise Search Routes - handler coverage', () => {
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

  describe('createSemanticSearchRouter', () => {
    it('should register POST / search route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const searchRoute = routes.find((r: any) => r.path === '/' && r.methods.post)
      expect(searchRoute).to.exist
    })

    it('should register GET / search history route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const historyRoute = routes.find((r: any) => r.path === '/' && r.methods.get)
      expect(historyRoute).to.exist
    })

    it('should register GET /:searchId route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const getRoute = routes.find((r: any) => r.path === '/:searchId' && r.methods.get)
      expect(getRoute).to.exist
    })

    it('should register DELETE /:searchId route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const deleteRoute = routes.find((r: any) => r.path === '/:searchId' && r.methods.delete)
      expect(deleteRoute).to.exist
    })

    it('should register DELETE / search history route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const deleteAllRoute = routes.find((r: any) => r.path === '/' && r.methods.delete)
      expect(deleteAllRoute).to.exist
    })

    it('should register PATCH /:searchId/share route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const shareRoute = routes.find((r: any) => r.path === '/:searchId/share' && r.methods.patch)
      expect(shareRoute).to.exist
    })

    it('should register PATCH /:searchId/unshare route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const unshareRoute = routes.find((r: any) => r.path === '/:searchId/unshare' && r.methods.patch)
      expect(unshareRoute).to.exist
    })

    it('should register PATCH /:searchId/archive route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const archiveRoute = routes.find((r: any) => r.path === '/:searchId/archive' && r.methods.patch)
      expect(archiveRoute).to.exist
    })

    it('should register PATCH /:searchId/unarchive route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const unarchiveRoute = routes.find((r: any) => r.path === '/:searchId/unarchive' && r.methods.patch)
      expect(unarchiveRoute).to.exist
    })

    it('should register POST /updateAppConfig route', () => {
      const router = createSemanticSearchRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const updateConfigRoute = routes.find((r: any) => r.path === '/updateAppConfig' && r.methods.post)
      expect(updateConfigRoute).to.exist
    })

    it('should handle updateAppConfig handler', async () => {
      const router = createSemanticSearchRouter(container)
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/updateAppConfig' && l.route.methods.post,
      )
      const handler = layer.route.stack[layer.route.stack.length - 1].handle

      const configModule = require('../../../../src/modules/tokens_manager/config/config')
      sinon.stub(configModule, 'loadAppConfig').resolves(mockAppConfig)

      const req = { tokenPayload: { orgId: 'org1' } } as any
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      await handler(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('createAgentConversationalRouter', () => {
    it('should register all agent routes', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:agentKey/conversations' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/stream' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId/messages' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId/messages/stream' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations' && r.methods.get)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId' && r.methods.get)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId' && r.methods.delete)).to.exist
    })

    it('should register template routes', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/template' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/template/:templateId' && r.methods.get)).to.exist
      expect(routes.find((r: any) => r.path === '/template/:templateId' && r.methods.put)).to.exist
      expect(routes.find((r: any) => r.path === '/template/:templateId' && r.methods.delete)).to.exist
      expect(routes.find((r: any) => r.path === '/template' && r.methods.get)).to.exist
    })

    it('should register agent CRUD routes', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/create' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey' && r.methods.get)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey' && r.methods.put)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey' && r.methods.delete)).to.exist
      expect(routes.find((r: any) => r.path === '/' && r.methods.get)).to.exist
    })

    it('should register agent sharing and permissions routes', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:agentKey/share' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/unshare' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/permissions' && r.methods.get)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/permissions' && r.methods.put)).to.exist
    })

    it('should register tools list route', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/tools/list' && r.methods.get)).to.exist
    })

    it('should register internal stream routes', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/internal/:conversationId/messages/stream' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/internal/stream' && r.methods.post)).to.exist
    })

    it('should register regenerate agent answers route', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId/message/:messageId/regenerate' && r.methods.post)).to.exist
    })

    it('should register agent archive, rename, and archives listing routes', () => {
      const router = createAgentConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/conversations/show/archives' && r.methods.get)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/show/archives' && r.methods.get)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId/archive' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId/unarchive' && r.methods.post)).to.exist
      expect(routes.find((r: any) => r.path === '/:agentKey/conversations/:conversationId/title' && r.methods.patch)).to.exist
    })
  })

  describe('createConversationalRouter - additional coverage', () => {
    it('should register internal stream route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/internal/stream' && r.methods.post)).to.exist
    })

    it('should register internal messages stream route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/internal/:conversationId/messages/stream' && r.methods.post)).to.exist
    })

    it('should register share route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:conversationId/share' && r.methods.post)).to.exist
    })

    it('should register unshare route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:conversationId/unshare' && r.methods.post)).to.exist
    })

    it('should register feedback route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:conversationId/message/:messageId/feedback' && r.methods.post)).to.exist
    })

    it('should register unarchive route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:conversationId/unarchive' && r.methods.patch)).to.exist
    })

    it('should register internal messages route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/internal/:conversationId/messages' && r.methods.post)).to.exist
    })

    it('should register messages stream route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/:conversationId/messages/stream' && r.methods.post)).to.exist
    })

    it('should register GET /show/archives/search route', () => {
      const router = createConversationalRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      expect(routes.find((r: any) => r.path === '/show/archives/search' && r.methods.get)).to.exist
    })
  })
})
