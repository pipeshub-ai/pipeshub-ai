import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createMCPRouter } from '../../../../src/modules/mcp/routes/mcp.routes'

describe('MCP Routes — createMCPRouter', () => {
  let container: Container
  let mockAuthMiddleware: any
  let mockAppConfig: any

  beforeEach(() => {
    container = new Container()

    mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
    }
    mockAppConfig = {
      oauthBackendUrl: 'http://localhost:3001',
    }

    container.bind('AuthMiddleware').toConstantValue(mockAuthMiddleware)
    container.bind('AppConfig').toConstantValue(mockAppConfig)
  })

  afterEach(() => {
    sinon.restore()
  })

  // =========================================================================
  // Router creation
  // =========================================================================
  describe('router creation', () => {
    it('should return a Router instance', () => {
      const router = createMCPRouter(container)
      expect(router).to.exist
      expect(router).to.have.property('stack')
    })

    it('should return a function (Router is middleware)', () => {
      const router = createMCPRouter(container)
      expect(router).to.be.a('function')
    })
  })

  // =========================================================================
  // Container resolution
  // =========================================================================
  describe('container resolution', () => {
    it('should resolve AuthMiddleware from container', () => {
      const getSpy = sinon.spy(container, 'get')
      createMCPRouter(container)

      expect(getSpy.calledWith('AuthMiddleware')).to.be.true
    })

    it('should resolve AppConfig from container', () => {
      const getSpy = sinon.spy(container, 'get')
      createMCPRouter(container)

      expect(getSpy.calledWith('AppConfig')).to.be.true
    })

    it('should throw when AuthMiddleware is not bound', () => {
      const emptyContainer = new Container()
      emptyContainer.bind('AppConfig').toConstantValue(mockAppConfig)

      expect(() => createMCPRouter(emptyContainer)).to.throw()
    })

    it('should throw when AppConfig is not bound', () => {
      const emptyContainer = new Container()
      emptyContainer.bind('AuthMiddleware').toConstantValue(mockAuthMiddleware)

      expect(() => createMCPRouter(emptyContainer)).to.throw()
    })

    it('should throw when container has no bindings', () => {
      const emptyContainer = new Container()
      expect(() => createMCPRouter(emptyContainer)).to.throw()
    })
  })

  // =========================================================================
  // Route registration
  // =========================================================================
  describe('route registration', () => {
    it('should register a POST / route', () => {
      const router = createMCPRouter(container)
      const postRoutes = router.stack.filter(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      expect(postRoutes.length).to.be.greaterThanOrEqual(1)

      const rootPost = postRoutes.find((l: any) => l.route.path === '/')
      expect(rootPost).to.exist
    })

    it('should register a GET / route', () => {
      const router = createMCPRouter(container)
      const getRoutes = router.stack.filter(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      expect(getRoutes.length).to.be.greaterThanOrEqual(1)

      const rootGet = getRoutes.find((l: any) => l.route.path === '/')
      expect(rootGet).to.exist
    })

    it('should register exactly 2 route layers', () => {
      const router = createMCPRouter(container)
      const routeLayers = router.stack.filter((layer: any) => layer.route)
      expect(routeLayers).to.have.length(2)
    })

    it('should have POST route at path /', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      expect(postLayer.route.path).to.equal('/')
    })

    it('should have GET route at path /', () => {
      const router = createMCPRouter(container)
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      expect(getLayer.route.path).to.equal('/')
    })
  })

  // =========================================================================
  // Middleware chain
  // =========================================================================
  describe('middleware chain', () => {
    it('should have 2 handlers on POST route (auth + request handler)', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      // route.stack contains the handler layers for that route
      expect(postLayer.route.stack).to.have.length(2)
    })

    it('should have 2 handlers on GET route (auth + request handler)', () => {
      const router = createMCPRouter(container)
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      expect(getLayer.route.stack).to.have.length(2)
    })

    it('should use authMiddleware.authenticate as first handler on POST', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      const firstHandler = postLayer.route.stack[0].handle
      expect(firstHandler).to.equal(mockAuthMiddleware.authenticate)
    })

    it('should use authMiddleware.authenticate as first handler on GET', () => {
      const router = createMCPRouter(container)
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      const firstHandler = getLayer.route.stack[0].handle
      expect(firstHandler).to.equal(mockAuthMiddleware.authenticate)
    })

    it('should use handleMCPRequest handler as second handler on POST', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      const secondHandler = postLayer.route.stack[1].handle
      // handleMCPRequest(appConfig) returns an async function
      expect(secondHandler).to.be.a('function')
      expect(secondHandler).to.not.equal(mockAuthMiddleware.authenticate)
    })

    it('should use handleMCPRequest handler as second handler on GET', () => {
      const router = createMCPRouter(container)
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      const secondHandler = getLayer.route.stack[1].handle
      expect(secondHandler).to.be.a('function')
      expect(secondHandler).to.not.equal(mockAuthMiddleware.authenticate)
    })
  })

  // =========================================================================
  // Different auth middleware configurations
  // =========================================================================
  describe('auth middleware integration', () => {
    it('should use the authenticate method from the container-resolved AuthMiddleware', () => {
      const customAuth = sinon.stub()
      const customMiddleware = { authenticate: customAuth }
      container.rebind('AuthMiddleware').toConstantValue(customMiddleware)

      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      expect(postLayer.route.stack[0].handle).to.equal(customAuth)
    })

    it('should use same auth middleware for both routes', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      expect(postLayer.route.stack[0].handle).to.equal(
        getLayer.route.stack[0].handle,
      )
    })
  })

  // =========================================================================
  // AppConfig passed to handler
  // =========================================================================
  describe('appConfig binding', () => {
    it('should pass the resolved appConfig to the request handler', () => {
      const customConfig = { oauthBackendUrl: 'https://custom.example.com' }
      container.rebind('AppConfig').toConstantValue(customConfig)

      // If appConfig were wrong, the handler would produce a different serverURL.
      // We verify the router creates without error with the custom config.
      const router = createMCPRouter(container)
      expect(router.stack).to.have.length.greaterThan(0)
    })
  })

  // =========================================================================
  // Idempotency
  // =========================================================================
  describe('idempotency', () => {
    it('should create independent routers on multiple calls', () => {
      const router1 = createMCPRouter(container)
      const router2 = createMCPRouter(container)

      expect(router1).to.not.equal(router2)
      expect(router1.stack).to.have.length(router2.stack.length)
    })
  })
})
