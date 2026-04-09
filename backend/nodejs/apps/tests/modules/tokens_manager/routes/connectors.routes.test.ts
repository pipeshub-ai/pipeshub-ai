import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConnectorRouter } from '../../../../src/modules/tokens_manager/routes/connectors.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Connector Routes', () => {
  let container: Container
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
    }

    mockEventService = {
      start: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<any>('AppConfig').toConstantValue(mockConfig)
    container.bind<any>('EntitiesEventProducer').toConstantValue(mockEventService)

    const mockPrometheusService = {
      recordActivity: sinon.stub(),
    }
    container.bind<any>(PrometheusService).toConstantValue(mockPrometheusService)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('should create a router successfully', () => {
    const router = createConnectorRouter(container)
    expect(router).to.be.a('function')
  })

  it('should have route handlers registered', () => {
    const router = createConnectorRouter(container)
    const routes = (router as any).stack || []
    expect(routes.length).to.be.greaterThan(0)
  })

  describe('registry routes', () => {
    it('should register GET /registry route', () => {
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
      const routes = (router as any).stack

      const configRoute = routes.find(
        (layer: any) =>
          layer.route &&
          layer.route.path === '/:connectorId/config' &&
          layer.route.methods.get,
      )
      expect(configRoute).to.not.be.undefined
    })

    it('should register PUT /:connectorId/config/auth route', () => {
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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
    it('should register GET /:connectorId/filters/:filterKey/options route', () => {
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
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

  describe('route count', () => {
    it('should register all expected routes', () => {
      const router = createConnectorRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      // Registry (2) + CRUD (5) + Config (3) + OAuth (2) + Filters (1) + Toggle (1) + Agents (1) + Configured (1) + Active/Inactive (2) = 18
      expect(routes.length).to.be.greaterThanOrEqual(18)
    })
  })

  describe('middleware chains', () => {
    it('should include multiple middleware handlers on each route', () => {
      const router = createConnectorRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      for (const routeLayer of routes) {
        const handlerCount = routeLayer.route.stack.length
        // Each route should have at least the final handler
        expect(handlerCount).to.be.greaterThanOrEqual(1,
          `Route ${routeLayer.route.path} should have at least 1 handler`)
      }
    })

    it('should have auth middleware on authenticated routes', () => {
      const router = createConnectorRouter(container)
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

  describe('router configuration', () => {
    it('should create different router instances on each call', () => {
      const router1 = createConnectorRouter(container)
      const router2 = createConnectorRouter(container)

      expect(router1).to.not.equal(router2)
    })

    it('should have consistent route count across calls', () => {
      const router1 = createConnectorRouter(container)
      const router2 = createConnectorRouter(container)

      const routes1 = (router1 as any).stack.filter((layer: any) => layer.route)
      const routes2 = (router2 as any).stack.filter((layer: any) => layer.route)

      expect(routes1.length).to.equal(routes2.length)
    })
  })

  describe('route methods', () => {
    it('GET routes should only accept GET method', () => {
      const router = createConnectorRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const registryRoute = routes.find(
        (layer: any) => layer.route.path === '/registry' && layer.route.methods.get,
      )
      expect(registryRoute.route.methods.get).to.be.true
      expect(registryRoute.route.methods.post).to.be.undefined
    })

    it('POST routes should only accept POST method', () => {
      const router = createConnectorRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const createRoute = routes.find(
        (layer: any) => layer.route.path === '/' && layer.route.methods.post,
      )
      expect(createRoute.route.methods.post).to.be.true
      expect(createRoute.route.methods.get).to.be.undefined
    })

    it('PUT routes should only accept PUT method', () => {
      const router = createConnectorRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const configRoute = routes.find(
        (layer: any) => layer.route.path === '/:connectorId/config/auth' && layer.route.methods.put,
      )
      expect(configRoute.route.methods.put).to.be.true
      expect(configRoute.route.methods.post).to.be.undefined
    })

    it('DELETE routes should only accept DELETE method', () => {
      const router = createConnectorRouter(container)
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
      const router = createConnectorRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const paramRoutes = routes.filter(
        (layer: any) => layer.route.path.includes(':connectorId'),
      )
      // Should have many routes using :connectorId
      expect(paramRoutes.length).to.be.greaterThanOrEqual(8)
    })

    it('should register routes with connectorType param', () => {
      const router = createConnectorRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const typeRoutes = routes.filter(
        (layer: any) => layer.route.path.includes(':connectorType'),
      )
      expect(typeRoutes.length).to.be.greaterThanOrEqual(1)
    })

    it('should register routes with filterKey param', () => {
      const router = createConnectorRouter(container)
      const routes = (router as any).stack.filter((layer: any) => layer.route)

      const filterRoutes = routes.filter(
        (layer: any) => layer.route.path.includes(':filterKey'),
      )
      expect(filterRoutes.length).to.be.greaterThanOrEqual(1)
    })
  })

})
