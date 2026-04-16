import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import axios from 'axios'
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
