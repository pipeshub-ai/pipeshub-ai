import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConfigurationManagerRouter } from '../../../../src/modules/configuration_manager/routes/cm_routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { ConfigService } from '../../../../src/modules/configuration_manager/services/updateConfig.service'
import {
  EntitiesEventProducer,
  SyncEventProducer,
} from '../../../../src/modules/configuration_manager/services/kafka_events.service'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('ConfigurationManager Routes', () => {
  let container: Container
  let mockAuthMiddleware: any
  let mockKeyValueStore: any
  let mockAppConfig: any
  let mockConfigService: any
  let mockEntityEventService: any
  let mockSyncEventService: any

  beforeEach(() => {
    container = new Container()

    mockAuthMiddleware = {
      authenticate: (_req: any, _res: any, next: any) => next(),
      scopedTokenValidator: sinon.stub().returns((_req: any, _res: any, next: any) => next()),
    }

    mockKeyValueStore = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
      delete: sinon.stub().resolves(),
      compareAndSet: sinon.stub().resolves(true),
    }

    mockAppConfig = {
      jwtSecret: 'test-secret',
      scopedJwtSecret: 'test-scoped-secret',
      storage: { endpoint: 'http://localhost:3003' },
      communicationBackend: 'http://localhost:3004',
      aiBackend: 'http://localhost:8000',
      cmBackend: 'http://localhost:3001',
    }

    mockConfigService = {
      updateConfig: sinon.stub().resolves({ statusCode: 200 }),
    }

    mockEntityEventService = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }

    mockSyncEventService = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }

    const mockPrometheusService = {
      recordActivity: sinon.stub(),
      getMetrics: sinon.stub().resolves(''),
    }

    container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(mockKeyValueStore)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind<EntitiesEventProducer>('EntitiesEventProducer').toConstantValue(mockEntityEventService)
    container.bind<SyncEventProducer>('SyncEventProducer').toConstantValue(mockSyncEventService)
    container.bind<ConfigService>('ConfigService').toConstantValue(mockConfigService)
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind(PrometheusService).toConstantValue(mockPrometheusService as any)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('should return a valid Express router', () => {
    const router = createConfigurationManagerRouter(container)
    expect(router).to.exist
    expect(router).to.have.property('stack')
  })

  it('should register storageConfig routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const postStorage = routes.find((r: any) => r.path === '/storageConfig' && r.methods.post)
    expect(postStorage).to.exist

    const getStorage = routes.find((r: any) => r.path === '/storageConfig' && r.methods.get)
    expect(getStorage).to.exist
  })

  it('should register internal storageConfig route', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const internalStorage = routes.find((r: any) => r.path === '/internal/storageConfig' && r.methods.get)
    expect(internalStorage).to.exist
  })

  it('should register smtpConfig routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const postSmtp = routes.find((r: any) => r.path === '/smtpConfig' && r.methods.post)
    expect(postSmtp).to.exist
  })

  it('should register auth config routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const paths = routes.map((r: any) => r.path)
    expect(paths).to.include('/authConfig/azureAd')
    expect(paths).to.include('/authConfig/google')
    expect(paths).to.include('/authConfig/microsoft')
    expect(paths).to.include('/authConfig/sso')
    expect(paths).to.include('/authConfig/oauth')
  })

  it('should register AI models config routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const paths = routes.map((r: any) => r.path)
    expect(paths).to.include('/aiModelsConfig')
  })

  it('should register frontend URL routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const paths = routes.map((r: any) => r.path)
    expect(paths).to.include('/frontendPublicUrl')
  })

  it('should register connector public URL routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const paths = routes.map((r: any) => r.path)
    expect(paths).to.include('/connectorPublicUrl')
  })

  it('should register metrics collection routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const paths = routes.map((r: any) => r.path)
    expect(paths).to.include('/metricsCollection/toggle')
  })

  it('should register platform settings routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const paths = routes.map((r: any) => r.path)
    expect(paths).to.include('/platform/settings')
  })

  it('should register slack bot config routes', () => {
    const router = createConfigurationManagerRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))

    const paths = routes.map((r: any) => r.path)
    expect(paths).to.include('/slack-bot')
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
        params: { connector: 'test-connector', configId: 'config123' },
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

    it('GET /internal/connectors/atlassian/config handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/atlassian/config', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('GET /connectors/atlassian/config handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/connectors/atlassian/config', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /connectors/atlassian/config handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/connectors/atlassian/config', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /internal/connectors/atlassian/config handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/atlassian/config', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('GET /connectors/onedrive/config handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/connectors/onedrive/config', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /internal/connectors/onedrive/config handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/onedrive/config', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('GET /connectors/sharepoint/config handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/connectors/sharepoint/config', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /internal/connectors/sharepoint/config handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/sharepoint/config', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('GET /internal/connectors/:connector/config handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/:connector/config', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('GET /connectors/googleWorkspaceCredentials handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/connectors/googleWorkspaceCredentials', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /connectors/googleWorkspaceCredentials handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/connectors/googleWorkspaceCredentials', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /internal/connectors/googleWorkspaceCredentials handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/googleWorkspaceCredentials', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('GET /internal/connectors/individual/googleWorkspaceCredentials handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/individual/googleWorkspaceCredentials', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('GET /internal/connectors/business/googleWorkspaceCredentials handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/business/googleWorkspaceCredentials', 'get')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('DELETE /internal/connectors/business/googleWorkspaceCredentials handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/business/googleWorkspaceCredentials', 'delete')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /connectors/googleWorkspaceOauthConfig handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/connectors/googleWorkspaceOauthConfig', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /internal/connectors/googleWorkspaceOauthConfig handler should throw when tokenPayload is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const handler = findRouteHandler(router, '/internal/connectors/googleWorkspaceOauthConfig', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.tokenPayload = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /connectors/sharepoint/config handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      // Find the second POST /connectors/sharepoint/config (the non-internal one after the internal one)
      const layers = router.stack.filter(
        (l: any) => l.route && l.route.path === '/connectors/sharepoint/config' && l.route.methods.post,
      )
      expect(layers.length).to.be.greaterThanOrEqual(1)
      const handler = layers[0].route.stack[layers[0].route.stack.length - 1].handle

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('POST /connectors/onedrive/config handler should throw when user is missing', () => {
      const router = createConfigurationManagerRouter(container)
      const layers = router.stack.filter(
        (l: any) => l.route && l.route.path === '/connectors/onedrive/config' && l.route.methods.post,
      )
      expect(layers.length).to.be.greaterThanOrEqual(1)
      const handler = layers[0].route.stack[layers[0].route.stack.length - 1].handle

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined

      expect(() => handler(mockReq, mockRes, mockNext)).to.throw()
    })

    it('should register all expected routes across all sections', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack.filter((layer: any) => layer.route)

      // The CM router has many routes. Verify total count is significant.
      expect(routes.length).to.be.greaterThanOrEqual(40)
    })

    it('should have middleware chains on all routes', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack.filter((layer: any) => layer.route)

      for (const routeLayer of routes) {
        expect(routeLayer.route.stack.length).to.be.greaterThanOrEqual(1,
          `Route ${routeLayer.route.path} should have at least 1 handler`)
      }
    })

    it('should register custom system prompt routes', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const getPrompt = routes.find((r: any) => r.path === '/prompts/system' && r.methods.get)
      expect(getPrompt).to.exist

      const putPrompt = routes.find((r: any) => r.path === '/prompts/system' && r.methods.put)
      expect(putPrompt).to.exist
    })

    it('should register AI model provider CRUD routes', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const addProvider = routes.find((r: any) => r.path === '/ai-models/providers' && r.methods.post)
      expect(addProvider).to.exist

      const updateProvider = routes.find((r: any) => r.path === '/ai-models/providers/:modelType/:modelKey' && r.methods.put)
      expect(updateProvider).to.exist

      const deleteProvider = routes.find((r: any) => r.path === '/ai-models/providers/:modelType/:modelKey' && r.methods.delete)
      expect(deleteProvider).to.exist

      const updateDefault = routes.find((r: any) => r.path === '/ai-models/default/:modelType/:modelKey' && r.methods.put)
      expect(updateDefault).to.exist
    })

    it('should register platform feature flags route', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const featureFlags = routes.find((r: any) => r.path === '/platform/feature-flags/available' && r.methods.get)
      expect(featureFlags).to.exist
    })

    it('should register metrics collection CRUD routes', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const getMetrics = routes.find((r: any) => r.path === '/metricsCollection' && r.methods.get)
      expect(getMetrics).to.exist

      const patchInterval = routes.find((r: any) => r.path === '/metricsCollection/pushInterval' && r.methods.patch)
      expect(patchInterval).to.exist

      const patchServer = routes.find((r: any) => r.path === '/metricsCollection/serverUrl' && r.methods.patch)
      expect(patchServer).to.exist
    })

    it('should register slack-bot CRUD routes', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const getSlack = routes.find((r: any) => r.path === '/slack-bot' && r.methods.get)
      expect(getSlack).to.exist

      const postSlack = routes.find((r: any) => r.path === '/slack-bot' && r.methods.post)
      expect(postSlack).to.exist

      const putSlack = routes.find((r: any) => r.path === '/slack-bot/:configId' && r.methods.put)
      expect(putSlack).to.exist

      const deleteSlack = routes.find((r: any) => r.path === '/slack-bot/:configId' && r.methods.delete)
      expect(deleteSlack).to.exist
    })

    it('should register internal slack-bot config route', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const internalSlack = routes.find((r: any) => r.path === '/internal/slack-bot' && r.methods.get)
      expect(internalSlack).to.exist
    })

    it('should register internal metricsCollection toggle route', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const internalToggle = routes.find((r: any) => r.path === '/internal/metricsCollection/toggle' && r.methods.post)
      expect(internalToggle).to.exist
    })
  })
})
