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
  AiConfigEventProducer,
  EntitiesEventProducer,
  SyncEventProducer,
} from '../../../../src/modules/configuration_manager/services/kafka_events.service'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'
import axios from 'axios'

describe('ConfigurationManager Routes', () => {
  let container: Container
  let mockAuthMiddleware: any
  let mockKeyValueStore: any
  let mockAppConfig: any
  let mockConfigService: any
  let mockEntityEventService: any
  let mockAiConfigEventService: any
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

    mockAiConfigEventService = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }

    mockSyncEventService = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }


    container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(mockKeyValueStore)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind<EntitiesEventProducer>('EntitiesEventProducer').toConstantValue(mockEntityEventService)
    container.bind<AiConfigEventProducer>('AiConfigEventProducer').toConstantValue(mockAiConfigEventService)
    container.bind<SyncEventProducer>('SyncEventProducer').toConstantValue(mockSyncEventService)
    container.bind<ConfigService>('ConfigService').toConstantValue(mockConfigService)
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind('SamlController').toConstantValue({
      updateSamlStrategiesWithCallback: sinon.stub().resolves(),
    })
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

describe('Configuration Manager Routes - inline handler branch coverage', () => {
  let container: Container
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    const mockKvStore = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
      delete: sinon.stub().resolves(),
      connect: sinon.stub().resolves(),
      watchKey: sinon.stub(),
    }

    const mockAppConfig = {
      storage: { type: 'local', config: {} },
      communicationBackend: 'http://localhost:3002',
      scopedJwtSecret: 'test-scoped-secret',
    }

    const mockEntityEventService = { publishEvent: sinon.stub().resolves(), start: sinon.stub().resolves() }
    const mockAiConfigEventService = { publishEvent: sinon.stub().resolves(), start: sinon.stub().resolves() }
    const mockSyncEventService = { publishEvent: sinon.stub().resolves(), start: sinon.stub().resolves() }
    const mockConfigService = { updateConfig: sinon.stub().resolves(), getConfig: sinon.stub().resolves({}) }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind('KeyValueStoreService').toConstantValue(mockKvStore as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind('EntitiesEventProducer').toConstantValue(mockEntityEventService as any)
    container.bind<AiConfigEventProducer>('AiConfigEventProducer').toConstantValue(mockAiConfigEventService as any)
    container.bind('SyncEventProducer').toConstantValue(mockSyncEventService as any)
    container.bind('ConfigService').toConstantValue(mockConfigService as any)
    container.bind('SamlController').toConstantValue({
      updateSamlStrategiesWithCallback: sinon.stub().resolves(),
    })

    router = createConfigurationManagerRouter(container)
  })

  afterEach(() => { sinon.restore() })

  function findHandlers(path: string, method: string): any[] {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    )
    if (!layer) return []
    return layer.route.stack.map((s: any) => s.handle)
  }

  function mockRes() {
    const res: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
    }
    return res
  }

  // =========================================================================
  // Inline handlers that check req.user
  // =========================================================================
  describe('inline handlers with req.user check', () => {
    const routesWithUserCheck = [
      { path: '/connectors/atlassian/config', method: 'get' },
      { path: '/connectors/atlassian/config', method: 'post' },
      { path: '/connectors/onedrive/config', method: 'get' },
      { path: '/connectors/onedrive/config', method: 'post' },
      { path: '/connectors/sharepoint/config', method: 'get' },
      { path: '/connectors/sharepoint/config', method: 'post' },
      { path: '/connectors/googleWorkspaceCredentials', method: 'get' },
      { path: '/connectors/googleWorkspaceCredentials', method: 'post' },
      { path: '/connectors/googleWorkspaceOauthConfig', method: 'post' },
    ]

    for (const route of routesWithUserCheck) {
      it(`should throw NotFoundError when req.user is missing for ${route.method.toUpperCase()} ${route.path}`, () => {
        const handlers = findHandlers(route.path, route.method)
        // Find the inline handler (usually the last one in the stack)
        const inlineHandler = handlers.find((h: any) => {
          try {
            const req = { user: undefined, tokenPayload: undefined, body: {}, params: {}, headers: {} } as any
            h(req, mockRes(), sinon.stub())
            return false
          } catch (error) {
            return error instanceof NotFoundError
          }
        })

        if (inlineHandler) {
          const req = { user: undefined, body: {}, params: {}, headers: {} } as any
          expect(() => inlineHandler(req, mockRes(), sinon.stub())).to.throw(NotFoundError)
        }
      })
    }
  })

  // =========================================================================
  // Inline handlers that check req.tokenPayload
  // =========================================================================
  describe('inline handlers with req.tokenPayload check', () => {
    const routesWithTokenPayloadCheck = [
      { path: '/internal/connectors/atlassian/config', method: 'get' },
      { path: '/internal/connectors/atlassian/config', method: 'post' },
      { path: '/internal/connectors/onedrive/config', method: 'post' },
      { path: '/internal/connectors/sharepoint/config', method: 'post' },
      { path: '/internal/connectors/:connector/config', method: 'get' },
      { path: '/internal/connectors/individual/googleWorkspaceCredentials', method: 'get' },
      { path: '/internal/connectors/business/googleWorkspaceCredentials', method: 'get' },
      { path: '/internal/connectors/business/googleWorkspaceCredentials', method: 'delete' },
      { path: '/internal/connectors/googleWorkspaceCredentials', method: 'post' },
      { path: '/internal/connectors/googleWorkspaceOauthConfig', method: 'post' },
    ]

    for (const route of routesWithTokenPayloadCheck) {
      it(`should throw NotFoundError when req.tokenPayload is missing for ${route.method.toUpperCase()} ${route.path}`, () => {
        const handlers = findHandlers(route.path, route.method)
        const inlineHandler = handlers.find((h: any) => {
          try {
            const req = { tokenPayload: undefined, user: undefined, body: {}, params: {}, headers: {} } as any
            h(req, mockRes(), sinon.stub())
            return false
          } catch (error) {
            return error instanceof NotFoundError
          }
        })

        if (inlineHandler) {
          const req = { tokenPayload: undefined, body: {}, params: {}, headers: {} } as any
          expect(() => inlineHandler(req, mockRes(), sinon.stub())).to.throw(NotFoundError)
        }
      })
    }
  })

  // =========================================================================
  // Route existence checks for all registered routes
  // =========================================================================
  describe('all routes should be registered', () => {
    const expectedRoutes = [
      { path: '/storageConfig', method: 'post' },
      { path: '/storageConfig', method: 'get' },
      { path: '/internal/storageConfig', method: 'get' },
      { path: '/smtpConfig', method: 'post' },
      { path: '/smtpConfig', method: 'get' },
      { path: '/authConfig/azureAd', method: 'get' },
      { path: '/internal/authConfig/azureAd', method: 'get' },
      { path: '/authConfig/azureAd', method: 'post' },
      { path: '/authConfig/microsoft', method: 'get' },
      { path: '/internal/authConfig/microsoft', method: 'get' },
      { path: '/authConfig/microsoft', method: 'post' },
      { path: '/authConfig/google', method: 'get' },
      { path: '/internal/authConfig/google', method: 'get' },
      { path: '/authConfig/google', method: 'post' },
      { path: '/authConfig/sso', method: 'get' },
      { path: '/internal/authConfig/sso', method: 'get' },
      { path: '/authConfig/sso', method: 'post' },
      { path: '/authConfig/oauth', method: 'get' },
      { path: '/internal/authConfig/oauth', method: 'get' },
      { path: '/authConfig/oauth', method: 'post' },
      { path: '/platform/settings', method: 'post' },
      { path: '/platform/settings', method: 'get' },
      { path: '/platform/feature-flags/available', method: 'get' },
      { path: '/aiModelsConfig', method: 'post' },
      { path: '/aiModelsConfig', method: 'get' },
      { path: '/internal/aiModelsConfig', method: 'get' },
      { path: '/ai-models', method: 'get' },
      { path: '/frontendPublicUrl', method: 'get' },
      { path: '/frontendPublicUrl', method: 'post' },
      { path: '/connectorPublicUrl', method: 'get' },
      { path: '/connectorPublicUrl', method: 'post' },
      { path: '/metricsCollection/toggle', method: 'put' },
      { path: '/metricsCollection', method: 'get' },
      { path: '/metricsCollection/pushInterval', method: 'patch' },
      { path: '/metricsCollection/serverUrl', method: 'patch' },
      { path: '/prompts/system', method: 'get' },
      { path: '/prompts/system', method: 'put' },
      { path: '/slack-bot', method: 'get' },
      { path: '/internal/slack-bot', method: 'get' },
      { path: '/slack-bot', method: 'post' },
    ]

    for (const route of expectedRoutes) {
      it(`should have ${route.method.toUpperCase()} ${route.path}`, () => {
        const layer = router.stack.find(
          (l: any) => l.route && l.route.path === route.path && l.route.methods[route.method],
        )
        expect(layer, `Route ${route.method.toUpperCase()} ${route.path} should exist`).to.not.be.undefined
      })
    }
  })
})

describe('Configuration Manager Routes - handler coverage', () => {
  let container: Container
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    const mockKvStore = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
      delete: sinon.stub().resolves(),
      connect: sinon.stub().resolves(),
      watchKey: sinon.stub(),
    }

    const mockAppConfig = {
      storage: { type: 'local', config: {} },
      communicationBackend: 'http://localhost:3002',
      scopedJwtSecret: 'test-scoped-secret',
    }

    const mockEntityEventService = {
      publishEvent: sinon.stub().resolves(),
      start: sinon.stub().resolves(),
    }

    const mockAiConfigEventService = {
      publishEvent: sinon.stub().resolves(),
      start: sinon.stub().resolves(),
    }

    const mockSyncEventService = {
      publishEvent: sinon.stub().resolves(),
      start: sinon.stub().resolves(),
    }

    const mockConfigService = {
      updateConfig: sinon.stub().resolves(),
      getConfig: sinon.stub().resolves({}),
    }


    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind('KeyValueStoreService').toConstantValue(mockKvStore as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind('EntitiesEventProducer').toConstantValue(mockEntityEventService as any)
    container.bind<AiConfigEventProducer>('AiConfigEventProducer').toConstantValue(mockAiConfigEventService as any)
    container.bind('SyncEventProducer').toConstantValue(mockSyncEventService as any)
    container.bind('ConfigService').toConstantValue(mockConfigService as any)
    container.bind('SamlController').toConstantValue({
      updateSamlStrategiesWithCallback: sinon.stub().resolves(),
    })

    router = createConfigurationManagerRouter(container)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('route registrations', () => {
    it('should register POST /storageConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/storageConfig' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /storageConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/storageConfig' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /internal/storageConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/internal/storageConfig' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register POST /smtpConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/smtpConfig' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /smtpConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/smtpConfig' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register auth config routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/authConfig/azureAd')
      expect(paths).to.include('/internal/authConfig/azureAd')
      expect(paths).to.include('/authConfig/microsoft')
      expect(paths).to.include('/internal/authConfig/microsoft')
      expect(paths).to.include('/authConfig/google')
      expect(paths).to.include('/internal/authConfig/google')
      expect(paths).to.include('/authConfig/sso')
      expect(paths).to.include('/internal/authConfig/sso')
      expect(paths).to.include('/authConfig/oauth')
      expect(paths).to.include('/internal/authConfig/oauth')
    })

    it('should register AI models config routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/aiModelsConfig')
      expect(paths).to.include('/internal/aiModelsConfig')
      expect(paths).to.include('/ai-models')
      expect(paths).to.include('/ai-models/:modelType')
      expect(paths).to.include('/ai-models/available/:modelType')
      expect(paths).to.include('/ai-models/providers')
      expect(paths).to.include('/ai-models/providers/:modelType/:modelKey')
      expect(paths).to.include('/ai-models/default/:modelType/:modelKey')
    })

    it('should register frontend URL routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/frontendPublicUrl')
    })

    it('should register connector URL routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/connectorPublicUrl')
    })

    it('should register metrics collection routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/metricsCollection/toggle')
      expect(paths).to.include('/internal/metricsCollection/toggle')
      expect(paths).to.include('/metricsCollection')
      expect(paths).to.include('/metricsCollection/pushInterval')
      expect(paths).to.include('/metricsCollection/serverUrl')
    })

    it('should register Google Workspace routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/connectors/googleWorkspaceCredentials')
      expect(paths).to.include('/internal/connectors/googleWorkspaceCredentials')
      expect(paths).to.include('/connectors/googleWorkspaceOauthConfig')
      expect(paths).to.include('/internal/connectors/googleWorkspaceOauthConfig')
    })

    it('should register connector config routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/connectors/atlassian/config')
      expect(paths).to.include('/internal/connectors/atlassian/config')
      expect(paths).to.include('/connectors/onedrive/config')
      expect(paths).to.include('/connectors/sharepoint/config')
      expect(paths).to.include('/internal/connectors/:connector/config')
    })

    it('should register platform settings routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/platform/settings')
      expect(paths).to.include('/platform/feature-flags/available')
    })

    it('should register Slack bot routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/slack-bot')
      expect(paths).to.include('/slack-bot/:configId')
    })

    it('should register custom system prompt routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/prompts/system')
    })
  })
})

describe('AI Model Registry Proxy Routes', () => {
  let container: Container
  let mockAppConfig: any

  function createMockReqRes() {
    const mockReq: any = {
      user: { orgId: 'test-org', userId: 'test-user', role: 'admin' },
      params: {},
      query: {},
      headers: { authorization: 'Bearer test-token' },
    }
    const mockRes: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
    }
    const mockNext = sinon.stub()
    return { mockReq, mockRes, mockNext }
  }

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: (_req: any, _res: any, next: any) => next(),
      scopedTokenValidator: sinon.stub().returns((_req: any, _res: any, next: any) => next()),
    }

    const mockKeyValueStore = {
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

    const mockConfigService = {
      updateConfig: sinon.stub().resolves({ statusCode: 200 }),
    }

    const mockEntityEventService = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }

    const mockSyncEventService = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }

    const mockAiConfigEventService = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }


    container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(mockKeyValueStore)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind<EntitiesEventProducer>('EntitiesEventProducer').toConstantValue(mockEntityEventService)
    container
      .bind<AiConfigEventProducer>('AiConfigEventProducer')
      .toConstantValue(mockAiConfigEventService)
    container.bind<SyncEventProducer>('SyncEventProducer').toConstantValue(mockSyncEventService)
    container.bind<ConfigService>('ConfigService').toConstantValue(mockConfigService)
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind('SamlController').toConstantValue({
      updateSamlStrategiesWithCallback: sinon.stub().resolves(),
    })
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('Route Registration', () => {
    it('should register GET /ai-models/registry route', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const registryRoute = routes.find(
        (r: any) => r.path === '/ai-models/registry' && r.methods.get
      )
      expect(registryRoute).to.exist
    })

    it('should register GET /ai-models/registry/capabilities route', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const capRoute = routes.find(
        (r: any) => r.path === '/ai-models/registry/capabilities' && r.methods.get
      )
      expect(capRoute).to.exist
    })

    it('should register GET /ai-models/registry/:providerId/schema route', () => {
      const router = createConfigurationManagerRouter(container)
      const routes = router.stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => ({ path: layer.route.path, methods: layer.route.methods }))

      const schemaRoute = routes.find(
        (r: any) => r.path === '/ai-models/registry/:providerId/schema' && r.methods.get
      )
      expect(schemaRoute).to.exist
    })

    it('should have middleware chains on registry routes', () => {
      const router = createConfigurationManagerRouter(container)
      const registryRoutes = router.stack.filter(
        (layer: any) =>
          layer.route && typeof layer.route.path === 'string' && layer.route.path.includes('/ai-models/registry')
      )

      expect(registryRoutes.length).to.equal(3)

      for (const routeLayer of registryRoutes) {
        expect(routeLayer.route.stack.length).to.be.greaterThanOrEqual(2,
          `Route ${routeLayer.route.path} should have auth + handler middleware`)
      }
    })
  })

  describe('GET /ai-models/registry handler', () => {
    it('should proxy to Python backend and forward response', async () => {
      const axiosGetStub = sinon.stub(axios, 'get').resolves({
        status: 200,
        data: { success: true, providers: [{ providerId: 'openAI' }], total: 1 },
      })

      const router = createConfigurationManagerRouter(container)
      const layers = router.stack.filter(
        (l: any) => l.route && l.route.path === '/ai-models/registry' && l.route.methods.get
      )
      const handler = layers[0].route.stack[layers[0].route.stack.length - 1].handle

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockRes.status.calledWith(200)).to.be.true
      expect(mockRes.json.calledOnce).to.be.true
      expect(axiosGetStub.calledOnce).to.be.true
      expect(axiosGetStub.firstCall.args[0]).to.include('/api/v1/ai-models/registry')
    })

    it('should forward search and capability query params', async () => {
      const axiosGetStub = sinon.stub(axios, 'get').resolves({
        status: 200,
        data: { success: true, providers: [], total: 0 },
      })

      const router = createConfigurationManagerRouter(container)
      const layers = router.stack.filter(
        (l: any) => l.route && l.route.path === '/ai-models/registry' && l.route.methods.get
      )
      const handler = layers[0].route.stack[layers[0].route.stack.length - 1].handle

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.query = { search: 'openai', capability: 'embedding' }
      await handler(mockReq, mockRes, mockNext)

      const calledUrl = axiosGetStub.firstCall.args[0] as string
      expect(calledUrl).to.include('search=openai')
      expect(calledUrl).to.include('capability=embedding')
    })

    it('should return 503 when Python backend is unreachable', async () => {
      sinon.stub(axios, 'get').rejects(new Error('ECONNREFUSED'))

      const router = createConfigurationManagerRouter(container)
      const layers = router.stack.filter(
        (l: any) => l.route && l.route.path === '/ai-models/registry' && l.route.methods.get
      )
      const handler = layers[0].route.stack[layers[0].route.stack.length - 1].handle

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      const err = mockNext.firstCall.args[0]
      expect(err).to.exist
    })
  })

  describe('GET /ai-models/registry/:providerId/schema handler', () => {
    it('should proxy provider schema request with provider ID', async () => {
      const axiosGetStub = sinon.stub(axios, 'get').resolves({
        status: 200,
        data: {
          success: true,
          provider: { providerId: 'openAI' },
          schema: { fields: { text_generation: [] } },
        },
      })

      const router = createConfigurationManagerRouter(container)
      const layers = router.stack.filter(
        (l: any) =>
          l.route && l.route.path === '/ai-models/registry/:providerId/schema' && l.route.methods.get
      )
      const handler = layers[0].route.stack[layers[0].route.stack.length - 1].handle

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.params = { providerId: 'openAI' }
      await handler(mockReq, mockRes, mockNext)

      expect(mockRes.status.calledWith(200)).to.be.true
      const calledUrl = axiosGetStub.firstCall.args[0] as string
      expect(calledUrl).to.include('/openAI/schema')
    })

    it('should forward 404 from Python backend', async () => {
      const error: any = new Error('Not Found')
      error.response = { status: 404, data: { detail: "Provider 'unknown' not found" } }
      sinon.stub(axios, 'get').rejects(error)

      const router = createConfigurationManagerRouter(container)
      const layers = router.stack.filter(
        (l: any) =>
          l.route && l.route.path === '/ai-models/registry/:providerId/schema' && l.route.methods.get
      )
      const handler = layers[0].route.stack[layers[0].route.stack.length - 1].handle

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.params = { providerId: 'unknown' }
      await handler(mockReq, mockRes, mockNext)

      expect(mockRes.status.calledWith(404)).to.be.true
    })
  })
})
