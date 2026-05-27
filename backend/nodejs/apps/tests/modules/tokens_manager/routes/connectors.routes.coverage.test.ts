import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConnectorRouter } from '../../../../src/modules/tokens_manager/routes/connectors.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Connector Routes - handler coverage', () => {
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

    const mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      cmBackend: 'http://localhost:3004',
      connectorBackend: 'http://localhost:8088',
    }

    const mockEventService = {
      start: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(false),
    }

    const mockScheduler = {
      scheduleJob: sinon.stub().resolves(),
      removeJob: sinon.stub().resolves(),
      getJobStatus: sinon.stub().resolves(null),
    }

    const mockCrawlingContainer = {
      get: sinon.stub().returns(mockScheduler),
    }

    const mockKeyValueStoreService = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<any>('AppConfig').toConstantValue(mockConfig)
    container.bind<any>('EntitiesEventProducer').toConstantValue(mockEventService)
    container.bind<any>(PrometheusService).toConstantValue({ recordActivity: sinon.stub() })
    container.bind<any>('KeyValueStoreService').toConstantValue(mockKeyValueStoreService)

    router = createConnectorRouter(container, mockCrawlingContainer)
  })

  afterEach(() => {
    sinon.restore()
  })

  function findHandler(path: string, method: string) {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    )
    if (!layer) return null
    return layer.route.stack[layer.route.stack.length - 1].handle
  }

  function mockRes() {
    const res: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
      cookie: sinon.stub().returnsThis(),
      redirect: sinon.stub().returnsThis(),
    }
    return res
  }

  it('should create router with all expected routes', () => {
    expect(router).to.exist
    expect(router.stack.length).to.be.greaterThan(0)
  })

  describe('GET /registry', () => {
    it('should have a handler for GET /registry', () => {
      const handler = findHandler('/registry', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /registry/:connectorType/schema', () => {
    it('should have a handler', () => {
      const handler = findHandler('/registry/:connectorType/schema', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /', () => {
    it('should have handlers for GET /', () => {
      const layers = router.stack.filter(
        (l: any) => l.route && l.route.path === '/' && l.route.methods.get,
      )
      expect(layers.length).to.be.greaterThan(0)
    })
  })

  describe('POST /', () => {
    it('should have a handler for POST /', () => {
      const handler = findHandler('/', 'post')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /active', () => {
    it('should have handlers', () => {
      const layers = router.stack.filter(
        (l: any) => l.route && l.route.path === '/active' && l.route.methods.get,
      )
      expect(layers.length).to.be.greaterThan(0)
    })
  })

  describe('GET /inactive', () => {
    it('should have handlers', () => {
      const layers = router.stack.filter(
        (l: any) => l.route && l.route.path === '/inactive' && l.route.methods.get,
      )
      expect(layers.length).to.be.greaterThan(0)
    })
  })

  describe('GET /agents/active', () => {
    it('should have a handler', () => {
      const handler = findHandler('/agents/active', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /configured', () => {
    it('should have a handler', () => {
      const handler = findHandler('/configured', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /:connectorId', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('DELETE /:connectorId', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId', 'delete')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /:connectorId/config', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/config', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('PUT /:connectorId/config', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/config', 'put')
      expect(handler).to.be.a('function')
    })
  })

  describe('PUT /:connectorId/config/auth', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/config/auth', 'put')
      expect(handler).to.be.a('function')
    })
  })

  describe('PUT /:connectorId/config/filters-sync', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/config/filters-sync', 'put')
      expect(handler).to.be.a('function')
    })
  })

  describe('PUT /:connectorId/name', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/name', 'put')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /:connectorId/oauth/authorize', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/oauth/authorize', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /oauth/callback', () => {
    it('should have a handler', () => {
      const handler = findHandler('/oauth/callback', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /:connectorId/filters', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/filters', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('POST /:connectorId/filters', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/filters', 'post')
      expect(handler).to.be.a('function')
    })
  })

  describe('GET /:connectorId/filters/:filterKey/options', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/filters/:filterKey/options', 'get')
      expect(handler).to.be.a('function')
    })
  })

  describe('POST /:connectorId/toggle', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/toggle', 'post')
      expect(handler).to.be.a('function')
    })
  })

  // Legacy routes
  describe('POST /getTokenFromCode', () => {
    it('should have a handler', () => {
      const handler = findHandler('/getTokenFromCode', 'post')
      expect(handler).to.be.a('function')
    })

    it('handler should call next on error when user not found', async () => {
      const handler = findHandler('/getTokenFromCode', 'post')
      const req = { user: undefined, body: {}, headers: {} }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(Error)
    })
  })

  describe('POST /internal/refreshIndividualConnectorToken', () => {
    it('should have a handler', () => {
      const handler = findHandler('/internal/refreshIndividualConnectorToken', 'post')
      expect(handler).to.be.a('function')
    })
  })

  describe('POST /updateAppConfig', () => {
    it('should have a handler', () => {
      const handler = findHandler('/updateAppConfig', 'post')
      expect(handler).to.be.a('function')
    })

    it('handler should call next on error', async () => {
      const handler = findHandler('/updateAppConfig', 'post')
      // loadAppConfig will fail since env is not configured
      const req = { body: {}, headers: {} }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('POST /internal/refreshIndividualConnectorToken', () => {
    it('should have a handler', () => {
      const handler = findHandler('/internal/refreshIndividualConnectorToken', 'post')
      expect(handler).to.be.a('function')
    })

    it('handler should call next on error when refresh token unavailable', async () => {
      const handler = findHandler('/internal/refreshIndividualConnectorToken', 'post')
      const req = {
        body: {},
        headers: { authorization: 'Bearer test-token' },
        tokenPayload: { orgId: 'o1', userId: 'u1' },
      }
      const res = mockRes()
      const next = sinon.stub()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('POST /getTokenFromCode - error paths', () => {
    it('handler should call next when config response fails', async () => {
      const handler = findHandler('/getTokenFromCode', 'post')
      const req = {
        user: { userId: 'u1', orgId: 'o1' },
        body: { tempCode: 'auth-code' },
        headers: { authorization: 'Bearer test-token' },
      }
      const res = mockRes()
      const next = sinon.stub()

      // Will fail because getGoogleWorkspaceConfig makes real HTTP call
      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('PUT /:connectorId/name', () => {
    it('should have a handler', () => {
      const handler = findHandler('/:connectorId/name', 'put')
      expect(handler).to.be.a('function')
    })
  })

  describe('POST /:connectorId/toggle', () => {
    it('should have a handler for toggle', () => {
      const handler = findHandler('/:connectorId/toggle', 'post')
      expect(handler).to.be.a('function')
    })
  })

  describe('Route structure validation', () => {
    it('should register all expected routes', () => {
      const routes = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => ({
          path: l.route.path,
          methods: Object.keys(l.route.methods),
        }))

      const expectedRoutes = [
        { path: '/registry', method: 'get' },
        { path: '/registry/:connectorType/schema', method: 'get' },
        { path: '/', method: 'get' },
        { path: '/', method: 'post' },
        { path: '/active', method: 'get' },
        { path: '/inactive', method: 'get' },
        { path: '/agents/active', method: 'get' },
        { path: '/configured', method: 'get' },
        { path: '/:connectorId', method: 'get' },
        { path: '/:connectorId', method: 'delete' },
        { path: '/:connectorId/config', method: 'get' },
        { path: '/:connectorId/config', method: 'put' },
        { path: '/:connectorId/config/auth', method: 'put' },
        { path: '/:connectorId/config/filters-sync', method: 'put' },
        { path: '/:connectorId/name', method: 'put' },
        { path: '/:connectorId/oauth/authorize', method: 'get' },
        { path: '/oauth/callback', method: 'get' },
        { path: '/:connectorId/filters', method: 'get' },
        { path: '/:connectorId/filters', method: 'post' },
        { path: '/:connectorId/filters/:filterKey/options', method: 'get' },
        { path: '/:connectorId/toggle', method: 'post' },
        { path: '/getTokenFromCode', method: 'post' },
        { path: '/internal/refreshIndividualConnectorToken', method: 'post' },
        { path: '/updateAppConfig', method: 'post' },
      ]

      for (const expected of expectedRoutes) {
        const found = routes.find(
          (r: any) =>
            r.path === expected.path && r.methods.includes(expected.method),
        )
        expect(found, `Expected route ${expected.method.toUpperCase()} ${expected.path} to exist`).to.not.be.undefined
      }
    })
  })
})
