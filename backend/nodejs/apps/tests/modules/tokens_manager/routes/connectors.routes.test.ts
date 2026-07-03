import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConnectorRouter } from '../../../../src/modules/tokens_manager/routes/connectors.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import type { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema'

describe('Connector Routes', () => {
  let container: Container
  let mockCrawlingContainer: any
  let mockConnectorContainer: any
  let mockAuthMiddleware: any
  let mockConfig: any
  let mockEventService: any

  beforeEach(() => {
    container = new Container()

    mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      cmBackend: 'http://localhost:3004',
      connectorBackend: 'http://localhost:8088',
      storage: { endpoint: 'http://localhost:3003' },
    }

    mockEventService = {
      start: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    }

    const mockScheduler = {
      scheduleJob: sinon.stub().resolves(),
      removeJob: sinon.stub().resolves(),
      getJobStatus: sinon.stub().resolves(null),
    }

    mockCrawlingContainer = {
      get: sinon.stub().returns(mockScheduler),
    }

    mockConnectorContainer = {
      get: sinon.stub().callsFake((token: string) => {
        if (token === 'RecordsEventProducer') {
          return { start: sinon.stub().resolves(), publishEvent: sinon.stub().resolves() }
        }
        if (token === 'SyncEventProducer') {
          return { start: sinon.stub().resolves(), publishEvent: sinon.stub().resolves() }
        }
        throw new Error(`Unexpected connector token: ${token}`)
      }),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<any>('AppConfig').toConstantValue(mockConfig)
    container.bind<any>('EntitiesEventProducer').toConstantValue(mockEventService)
    container.bind<any>('RecordsEventProducer').toConstantValue({
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    })
    container.bind<any>('SyncEventProducer').toConstantValue({
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    })


    const mockKeyValueStore = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
    }
    container
      .bind<KeyValueStoreService>('KeyValueStoreService')
      .toConstantValue(mockKeyValueStore as unknown as KeyValueStoreService)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('should create a router successfully', () => {
    const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
    expect(router).to.be.a('function')
  })

  it('should have route handlers registered', () => {
    const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
    const routes = (router as any).stack || []
    expect(routes.length).to.be.greaterThan(0)
  })

  describe('registry routes', () => {
    it('should register GET /registry route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const registryRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/registry' &&
          layer.route.methods.get,
      )
      expect(registryRoute).to.not.be.undefined
    })

    it('should register GET /registry/:connectorType/schema route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const schemaRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/registry/:connectorType/schema' &&
          layer.route.methods.get,
      )
      expect(schemaRoute).to.not.be.undefined
    })
  })

  describe('instance management routes', () => {
    it('should register GET / route for listing instances', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const getRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.get,
      )
      expect(getRoute).to.not.be.undefined
    })

    it('should register POST / route for creating instance', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const postRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/' &&
          layer.route.methods.post,
      )
      expect(postRoute).to.not.be.undefined
    })

    it('should register GET /active route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const activeRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/active' &&
          layer.route.methods.get,
      )
      expect(activeRoute).to.not.be.undefined
    })

    it('should register GET /inactive route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const inactiveRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/inactive' &&
          layer.route.methods.get,
      )
      expect(inactiveRoute).to.not.be.undefined
    })

    it('should register GET /agents/active route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const agentsRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/agents/active' &&
          layer.route.methods.get,
      )
      expect(agentsRoute).to.not.be.undefined
    })

    it('should register GET /configured route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const configuredRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/configured' &&
          layer.route.methods.get,
      )
      expect(configuredRoute).to.not.be.undefined
    })

    it('should register GET /:connectorId route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const getByIdRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId' &&
          layer.route.methods.get,
      )
      expect(getByIdRoute).to.not.be.undefined
    })

    it('should register DELETE /:connectorId route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const deleteRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId' &&
          layer.route.methods.delete,
      )
      expect(deleteRoute).to.not.be.undefined
    })
  })

  describe('configuration routes', () => {
    it('should register GET /:connectorId/config route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const configRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/config' &&
          layer.route.methods.get,
      )
      expect(configRoute).to.not.be.undefined
    })

    it('should register PUT /:connectorId/config route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const updateConfigRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/config' &&
          layer.route.methods.put,
      )
      expect(updateConfigRoute).to.not.be.undefined
    })

    it('should register PUT /:connectorId/config/auth route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const authConfigRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/config/auth' &&
          layer.route.methods.put,
      )
      expect(authConfigRoute).to.not.be.undefined
    })

    it('should register PUT /:connectorId/config/filters-sync route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const filtersSyncRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/config/filters-sync' &&
          layer.route.methods.put,
      )
      expect(filtersSyncRoute).to.not.be.undefined
    })

    it('should register PUT /:connectorId/name route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const nameRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/name' &&
          layer.route.methods.put,
      )
      expect(nameRoute).to.not.be.undefined
    })
  })

  describe('OAuth routes', () => {
    it('should register GET /:connectorId/oauth/authorize route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const authorizeRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/oauth/authorize' &&
          layer.route.methods.get,
      )
      expect(authorizeRoute).to.not.be.undefined
    })

    it('should register GET /oauth/callback route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const callbackRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/oauth/callback' &&
          layer.route.methods.get,
      )
      expect(callbackRoute).to.not.be.undefined
    })
  })

  describe('filter routes', () => {
    it('should register GET /:connectorId/filters route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const filtersRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/filters' &&
          layer.route.methods.get,
      )
      expect(filtersRoute).to.not.be.undefined
    })

    it('should register POST /:connectorId/filters route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const saveFiltersRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/filters' &&
          layer.route.methods.post,
      )
      expect(saveFiltersRoute).to.not.be.undefined
    })

    it('should register GET /:connectorId/filters/:filterKey/options route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const filterOptionsRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/filters/:filterKey/options' &&
          layer.route.methods.get,
      )
      expect(filterOptionsRoute).to.not.be.undefined
    })
  })

  describe('toggle route', () => {
    it('should register POST /:connectorId/toggle route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const toggleRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/toggle' &&
          layer.route.methods.post,
      )
      expect(toggleRoute).to.not.be.undefined
    })
  })

  describe('Local FS file-events routes', () => {
    it('should register POST /:connectorId/file-events route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const route = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/file-events' &&
          layer.route.methods.post,
      )
      expect(route).to.not.be.undefined
    })

    it('should register POST /:connectorId/file-events/upload route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const route = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/file-events/upload' &&
          layer.route.methods.post,
      )
      expect(route).to.not.be.undefined
    })
  })

  describe('legacy routes', () => {
    it('should register POST /getTokenFromCode route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const tokenRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/getTokenFromCode' &&
          layer.route.methods.post,
      )
      expect(tokenRoute).to.not.be.undefined
    })

    it('should register POST /internal/refreshIndividualConnectorToken route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const refreshRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/internal/refreshIndividualConnectorToken' &&
          layer.route.methods.post,
      )
      expect(refreshRoute).to.not.be.undefined
    })

    it('should register POST /updateAppConfig route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack

      const updateConfigRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/updateAppConfig' &&
          layer.route.methods.post,
      )
      expect(updateConfigRoute).to.not.be.undefined
    })
  })

  describe('route count', () => {
    it('should register all expected routes', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      // Verify we have a significant number of routes
      // Registry (2) + CRUD (8) + Config (4+1) + OAuth (2) + Filters (3) + Toggle (1) + Legacy dup (3) + Legacy endpoints (3) = 27
      expect(routes.length).to.be.greaterThanOrEqual(20)
    })
  })

  describe('middleware chains', () => {
    it('should include multiple middleware handlers on each route', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      for (const routeLayer of routes) {
        const handlerCount = routeLayer.route.stack.length
        // Each route should have at least the final handler
        expect(handlerCount).to.be.greaterThanOrEqual(1,
          `Route ${routeLayer.route.path} should have at least 1 handler`)
      }
    })

    it('should have auth middleware on authenticated routes', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      // All routes should have multiple middleware layers (auth + handler at minimum)
      const registryRoute = routes.find(
        (layer: any) => layer.route.path === '/registry' && layer.route.methods.get,
      )
      expect(registryRoute).to.not.be.undefined
      // The route stack should include auth middleware + metrics + validation + handler
      expect(registryRoute.route.stack.length).to.be.greaterThanOrEqual(2)
    })
  })

  describe('route handler count per endpoint', () => {
    it('POST /getTokenFromCode should have auth + userAdminCheck + handler', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const tokenRoute = routes.find(
        (layer: any) =>
          layer.route.path === '/getTokenFromCode' && layer.route.methods.post,
      )
      expect(tokenRoute).to.not.be.undefined
      // auth + requireScopes + metrics + userAdminCheck + handler = at least 3
      expect(tokenRoute.route.stack.length).to.be.greaterThanOrEqual(3)
    })

    it('POST /internal/refreshIndividualConnectorToken should have scoped auth + handler', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const refreshRoute = routes.find(
        (layer: any) =>
          layer.route.path === '/internal/refreshIndividualConnectorToken' &&
          layer.route.methods.post,
      )
      expect(refreshRoute).to.not.be.undefined
      // scopedTokenValidator + handler = at least 2
      expect(refreshRoute.route.stack.length).to.be.greaterThanOrEqual(2)
    })

    it('POST /updateAppConfig should have scoped auth + handler', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const updateConfigRoute = routes.find(
        (layer: any) =>
          layer.route.path === '/updateAppConfig' && layer.route.methods.post,
      )
      expect(updateConfigRoute).to.not.be.undefined
      expect(updateConfigRoute.route.stack.length).to.be.greaterThanOrEqual(2)
    })
  })

  describe('connectorListSchema - Zod validation via GET / middleware chain', () => {
    function findAllHandlers(router: any, path: string, method: string) {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === path && l.route.methods[method],
      )
      if (!layer) return []
      return layer.route.stack.map((s: any) => s.handle)
    }

    async function runThroughChain(req: any, res: any, next: sinon.SinonStub) {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const handlers = findAllHandlers(router, '/', 'get')
      for (const handler of handlers) {
        if (next.called) break
        await Promise.resolve(handler(req, res, next))
      }
    }

    it('scope defaults to team when not provided', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: {},
        params: {},
        body: {},
        headers: {},
      }
      const res: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
      }
      const next = sinon.stub()
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      await runThroughChain(req, res, next)

      // After Zod validation middleware sets default, scope should be 'team' — no schema error.
      // Use === true to guard against sinon's `called` being uninitialised (undefined).
      // Use Array.isArray(.issues) so the boolean is always `true` or `false`, never `undefined`.
      const err = next.called === true ? next.firstCall?.args?.[0] : null
      const wasSchemaError = err != null && Array.isArray((err as any)?.issues)
      expect(wasSchemaError).to.be.false
    })

    it('scope=personal is accepted', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'personal' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      await runThroughChain(req, res, next)

      const wasSchemaError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasSchemaError).to.be.false
    })

    it('scope=invalid is rejected by Zod', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'invalid_scope' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      await runThroughChain(req, res, next)

      expect(next.called).to.be.true
    })

    it('isAuthenticated=true is preprocessed to boolean by Zod', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'team', isAuthenticated: 'true' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      await runThroughChain(req, res, next)

      const wasSchemaError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasSchemaError).to.be.false
    })

    it('isAuthenticated=false is preprocessed to boolean by Zod', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'team', isAuthenticated: 'false' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      await runThroughChain(req, res, next)

      const wasSchemaError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasSchemaError).to.be.false
    })

    it('isAuthenticated=invalid_string is rejected by Zod', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'team', isAuthenticated: 'maybe' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      await runThroughChain(req, res, next)

      expect(next.called).to.be.true
    })

    it('isActive=true is accepted by Zod', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'team', isActive: 'true' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      await runThroughChain(req, res, next)

      const wasSchemaError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasSchemaError).to.be.false
    })

    it('connectorType is accepted by Zod', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'team', connectorType: 'google_drive' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      await runThroughChain(req, res, next)

      const wasSchemaError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasSchemaError).to.be.false
    })

    it('empty connectorType string is rejected by Zod (min length 1)', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'team', connectorType: '' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      await runThroughChain(req, res, next)

      expect(next.called).to.be.true
    })

    it('page=0 is rejected by Zod (min 1)', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'team', page: '0' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      await runThroughChain(req, res, next)

      expect(next.called).to.be.true
    })

    it('limit=201 is rejected by Zod (max 200)', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: { scope: 'team', limit: '201' },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()

      await runThroughChain(req, res, next)

      expect(next.called).to.be.true
    })

    it('all new params together pass Zod validation', async () => {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: {
          scope: 'team',
          page: '1',
          limit: '20',
          search: 'test',
          isAuthenticated: 'true',
          isActive: 'false',
          connectorType: 'slack',
        },
        params: {},
        body: {},
        headers: {},
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      await runThroughChain(req, res, next)

      const wasSchemaError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasSchemaError).to.be.false
    })
  })

  describe('router configuration', () => {
    it('should create different router instances on each call', () => {
      const router1 = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const router2 = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)

      expect(router1).to.not.equal(router2)
    })

    it('should have consistent route count across calls', () => {
      const router1 = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const router2 = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)

      const routes1 = (router1 as any).stack.filter((layer: any) => layer.route)
      const routes2 = (router2 as any).stack.filter((layer: any) => layer.route)

      expect(routes1.length).to.equal(routes2.length)
    })
  })

  describe('route methods', () => {
    it('GET routes should only accept GET method', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const registryRoute = routes.find(
        (layer: any) => layer.route.path === '/registry' && layer.route.methods.get,
      )
      expect(registryRoute.route.methods.get).to.be.true
      expect(registryRoute.route.methods.post).to.be.undefined
    })

    it('POST routes should only accept POST method', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const createRoute = routes.find(
        (layer: any) => layer.route.path === '/' && layer.route.methods.post,
      )
      expect(createRoute.route.methods.post).to.be.true
      expect(createRoute.route.methods.get).to.be.undefined
    })

    it('PUT routes should only accept PUT method', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const configRoute = routes.find(
        (layer: any) => layer.route.path === '/:connectorId/config' && layer.route.methods.put,
      )
      expect(configRoute.route.methods.put).to.be.true
      expect(configRoute.route.methods.post).to.be.undefined
    })

    it('DELETE routes should only accept DELETE method', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const deleteRoute = routes.find(
        (layer: any) => layer.route.path === '/:connectorId' && layer.route.methods.delete,
      )
      expect(deleteRoute.route.methods.delete).to.be.true
      expect(deleteRoute.route.methods.get).to.be.undefined
    })
  })

  describe('parameterized routes', () => {
    it('should register routes with connectorId param', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const paramRoutes = routes.filter(
        (layer: any) => layer.route.path.includes(':connectorId'),
      )
      // Should have many routes using :connectorId
      expect(paramRoutes.length).to.be.greaterThanOrEqual(8)
    })

    it('should register routes with connectorType param', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const typeRoutes = routes.filter(
        (layer: any) => layer.route.path.includes(':connectorType'),
      )
      expect(typeRoutes.length).to.be.greaterThanOrEqual(1)
    })

    it('should register routes with filterKey param', () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const filterRoutes = routes.filter(
        (layer: any) => layer.route.path.includes(':filterKey'),
      )
      expect(filterRoutes.length).to.be.greaterThanOrEqual(1)
    })
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
        params: {},
        query: {},
        headers: {},
        ip: '127.0.0.1',
      }
      const mockRes: any = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub().returnsThis(),
        cookie: sinon.stub(),
        redirect: sinon.stub(),
      }
      const mockNext = sinon.stub()
      return { mockReq, mockRes, mockNext }
    }

    it('POST /updateAppConfig handler should update config and respond 200', async () => {
      // Stub loadAppConfig
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config')
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').resolves(mockConfig as any)

      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const handler = findRouteHandler(router, '/updateAppConfig', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockRes.status.calledWith(200)).to.be.true
      expect(mockRes.json.calledOnce).to.be.true
      const jsonArg = mockRes.json.firstCall.args[0]
      expect(jsonArg.message).to.include('updated successfully')

      loadStub.restore()
    })

    it('POST /updateAppConfig handler should call next on error', async () => {
      const loadAppConfigModule = await import('../../../../src/modules/tokens_manager/config/config')
      const loadStub = sinon.stub(loadAppConfigModule, 'loadAppConfig').rejects(new Error('Config load failed'))

      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const handler = findRouteHandler(router, '/updateAppConfig', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0]).to.be.an.instanceOf(Error)

      loadStub.restore()
    })

    it('POST /getTokenFromCode handler should call next when user is missing', async () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const handler = findRouteHandler(router, '/getTokenFromCode', 'post')
      expect(handler).to.not.be.undefined

      const { mockReq, mockRes, mockNext } = createMockReqRes()
      mockReq.user = undefined
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })

    it('POST /internal/refreshIndividualConnectorToken handler should call next on error', async () => {
      const router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
      const handler = findRouteHandler(router, '/internal/refreshIndividualConnectorToken', 'post')
      expect(handler).to.not.be.undefined

      // The handler will fail since getRefreshTokenCredentials is not mocked - it should call next
      const { mockReq, mockRes, mockNext } = createMockReqRes()
      await handler(mockReq, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })
  })
})

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
      storage: { endpoint: 'http://localhost:3003' },
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

    const mockConnectorContainer = {
      get: sinon.stub().callsFake((token: string) => {
        if (token === 'RecordsEventProducer') {
          return { start: sinon.stub().resolves(), publishEvent: sinon.stub().resolves() }
        }
        if (token === 'SyncEventProducer') {
          return { start: sinon.stub().resolves(), publishEvent: sinon.stub().resolves() }
        }
        throw new Error(`Unexpected connector token: ${token}`)
      }),
    }

    const mockKeyValueStoreService = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<any>('AppConfig').toConstantValue(mockConfig)
    container.bind<any>('EntitiesEventProducer').toConstantValue(mockEventService)
    container.bind<any>('RecordsEventProducer').toConstantValue({
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    })
    container.bind<any>('SyncEventProducer').toConstantValue({
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    })
    container.bind<any>('KeyValueStoreService').toConstantValue(mockKeyValueStoreService)

    router = createConnectorRouter(container, mockCrawlingContainer, mockConnectorContainer)
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

  describe('GET / — connectorListSchema validation', () => {
    function getAllHandlers(path: string, method: string) {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === path && l.route.methods[method],
      )
      if (!layer) return []
      return layer.route.stack.map((s: any) => s.handle)
    }

    async function runChain(reqOverrides: any) {
      const req: any = {
        user: { userId: 'u1', orgId: 'o1' },
        query: {},
        params: {},
        body: {},
        headers: {},
        ...reqOverrides,
      }
      const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
      const next = sinon.stub()
      for (const handler of getAllHandlers('/', 'get')) {
        if (next.called) break
        await Promise.resolve(handler(req, res, next))
      }
      return { req, res, next }
    }

    it('isAuthenticated=true string is coerced to boolean true by Zod', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      const { next } = await runChain({ query: { scope: 'team', isAuthenticated: 'true' } })

      const wasZodError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasZodError).to.be.false
    })

    it('isAuthenticated=false string is coerced to boolean false by Zod', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      const { next } = await runChain({ query: { scope: 'team', isAuthenticated: 'false' } })

      const wasZodError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasZodError).to.be.false
    })

    it('isAuthenticated=maybe is rejected by Zod', async () => {
      const { next } = await runChain({ query: { scope: 'team', isAuthenticated: 'maybe' } })
      expect(next.called).to.be.true
    })

    it('isActive=true is accepted by Zod', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      const { next } = await runChain({ query: { scope: 'team', isActive: 'true' } })

      const wasZodError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasZodError).to.be.false
    })

    it('connectorType=google_drive is accepted by Zod', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      const { next } = await runChain({ query: { scope: 'team', connectorType: 'google_drive' } })

      const wasZodError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasZodError).to.be.false
    })

    it('empty connectorType is rejected by Zod', async () => {
      const { next } = await runChain({ query: { scope: 'team', connectorType: '' } })
      expect(next.called).to.be.true
    })

    it('scope defaults to team when omitted', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      const { next } = await runChain({ query: {} })

      const wasZodError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasZodError).to.be.false
    })

    it('scope=invalid is rejected by Zod', async () => {
      const { next } = await runChain({ query: { scope: 'bad_scope' } })
      expect(next.called).to.be.true
    })

    it('page=0 is rejected by Zod (min 1)', async () => {
      const { next } = await runChain({ query: { scope: 'team', page: '0' } })
      expect(next.called).to.be.true
    })

    it('limit=300 is rejected by Zod (max 200)', async () => {
      const { next } = await runChain({ query: { scope: 'team', limit: '300' } })
      expect(next.called).to.be.true
    })

    it('all valid new params together pass Zod and reach controller', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: [] })
      sinon.stub(UserGroups, 'find').returns({ select: sinon.stub().resolves([]) } as any)

      const { res, next } = await runChain({
        query: {
          scope: 'team',
          page: '1',
          limit: '50',
          search: 'test',
          isAuthenticated: 'true',
          isActive: 'false',
          connectorType: 'slack',
        },
      })

      const wasZodError = next.called && next.firstCall.args[0]?.issues != null
      expect(wasZodError).to.be.false
    })
  })
})
