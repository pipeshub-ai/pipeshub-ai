import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import express from 'express'
import { AddressInfo } from 'net'
import { createMCPRouter } from '../../../../src/modules/mcp/routes/mcp.routes'
import { UnauthorizedError } from '../../../../src/libs/errors/http.errors'
import { BaseError } from '../../../../src/libs/errors/base.error'
import { ExpiredTokenError, InvalidTokenError } from '../../../../src/libs/errors/oauth.errors'
import { createMockLogger } from '../../../helpers/mock-logger'

describe('MCP Routes — createMCPRouter', () => {
  let container: Container
  let mockAuthMiddleware: any
  let mockAppConfig: any

  beforeEach(() => {
    container = new Container()

    mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((req: any, _res: any, next: any) => {
        req.user = { userId: 'user-1', isOAuth: true }
        next()
      }),
    }
    mockAppConfig = {
      oauthBackendUrl: 'http://localhost:3001',
      oauthIssuer: 'https://pipeshub.example.com',
      frontendUrl: 'https://pipeshub.example.com',
      scopedJwtSecret: 'test-scoped-secret',
      maxRequestsPerMinute: 1000,
    }

    container.bind('AuthMiddleware').toConstantValue(mockAuthMiddleware)
    container.bind('AppConfig').toConstantValue(mockAppConfig)
    container.bind('Logger').toConstantValue(createMockLogger())
    container.bind('OAuthTokenService').toConstantValue({ mcpResource: 'https://pipeshub.example.com/mcp' })
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
    it('should guard POST with origin, limits, auth and the token check before the handler', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      // route.stack contains the handler layers for that route
      expect(postLayer.route.stack).to.have.length(6)
    })

    it('should guard GET with origin, limits, auth and the token check before the 405 handler', () => {
      const router = createMCPRouter(container)
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      expect(getLayer.route.stack).to.have.length(6)
    })

    it('should authenticate after the origin check and the rejected-token limit on POST', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      const firstHandler = postLayer.route.stack[2].handle
      expect(firstHandler).to.equal(mockAuthMiddleware.authenticate)
    })

    it('should authenticate after the origin check and the rejected-token limit on GET', () => {
      const router = createMCPRouter(container)
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      const firstHandler = getLayer.route.stack[2].handle
      expect(firstHandler).to.equal(mockAuthMiddleware.authenticate)
    })

    it('should end POST with the handleMCPRequest handler', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      const secondHandler = postLayer.route.stack[5].handle
      // handleMCPRequest(appConfig) returns an async function
      expect(secondHandler).to.be.a('function')
      expect(secondHandler).to.not.equal(mockAuthMiddleware.authenticate)
    })

    it('should end GET with a 405 handler', () => {
      const router = createMCPRouter(container)
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      const secondHandler = getLayer.route.stack[5].handle
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
      expect(postLayer.route.stack[2].handle).to.equal(customAuth)
    })

    it('should use same auth middleware for both routes', () => {
      const router = createMCPRouter(container)
      const postLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.post,
      )
      const getLayer = router.stack.find(
        (layer: any) => layer.route && layer.route.methods.get,
      )
      expect(postLayer.route.stack[2].handle).to.equal(
        getLayer.route.stack[2].handle,
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

  // The router behind an error handler shaped like the app's.
  async function request(init: RequestInit, authError?: Error): Promise<Response> {
    if (authError) {
      mockAuthMiddleware.authenticate = (_req: any, _res: any, next: any) => next(authError)
    }
    mockAppConfig.oauthIssuer = 'https://pipeshub.example.com'
    const app = express()
    app.use('/mcp', createMCPRouter(container))
    app.use((err: any, _req: any, res: any, _next: any) => {
      res.status(err instanceof BaseError ? err.statusCode : 500).json({ error: err.message })
    })
    const server = app.listen(0)
    try {
      const { port } = server.address() as AddressInfo
      return await fetch(`http://127.0.0.1:${port}/mcp`, init)
    } finally {
      server.close()
    }
  }

  // =========================================================================
  // 401 challenge: how an MCP client with no token finds where to authorize
  // =========================================================================
  describe('401 challenge', () => {
    const post = (authError: Error) => request({ method: 'POST', body: '{}' }, authError)

    it('names the protected-resource metadata in WWW-Authenticate', async () => {
      const res = await post(new UnauthorizedError('No token provided'))
      expect(res.status).to.equal(401)
      expect(res.headers.get('www-authenticate')).to.equal(
        'Bearer resource_metadata="https://pipeshub.example.com/.well-known/oauth-protected-resource/mcp"',
      )
    })

    for (const [name, error] of [
      ['an expired token', new ExpiredTokenError('Access token has expired')],
      ['an invalid or revoked token', new InvalidTokenError('Invalid access token')],
    ] as const) {
      it(`sends the same challenge for ${name}`, async () => {
        const res = await post(error)
        expect(res.status).to.equal(401)
        expect(res.headers.get('www-authenticate')).to.equal(
          'Bearer resource_metadata="https://pipeshub.example.com/.well-known/oauth-protected-resource/mcp"',
        )
      })
    }

    it('adds no challenge to an error that is not about authentication', async () => {
      const res = await post(new Error('boom'))
      expect(res.status).to.equal(500)
      expect(res.headers.get('www-authenticate')).to.equal(null)
    })
  })

  // =========================================================================
  // GET: a stateless server offers no SSE stream
  // =========================================================================
  describe('GET /', () => {
    const get = (accept: string, authError?: Error) => request({ headers: { accept } }, authError)

    for (const accept of ['text/event-stream', '*/*']) {
      it(`answers 405 with Allow: POST (Accept: ${accept})`, async () => {
        const res = await get(accept)
        expect(res.status).to.equal(405)
        expect(res.headers.get('allow')).to.equal('POST')
        expect(await res.json()).to.deep.equal({
          jsonrpc: '2.0',
          error: { code: -32000, message: 'Method not allowed.' },
          id: null,
        })
      })
    }

    it('still challenges a caller with no token', async () => {
      const res = await get('text/event-stream', new UnauthorizedError('No token provided'))
      expect(res.status).to.equal(401)
      expect(res.headers.get('www-authenticate')).to.equal(
        'Bearer resource_metadata="https://pipeshub.example.com/.well-known/oauth-protected-resource/mcp"',
      )
    })
  })

  // =========================================================================
  // Guard: Origin and rate limits, on one router so the limits keep their counts
  // =========================================================================
  describe('guard', () => {
    let user: string | undefined
    let authFails: boolean
    let audience: string | undefined

    async function serve(): Promise<{ get: (headers?: Record<string, string>) => Promise<Response>; close: () => void }> {
      mockAuthMiddleware.authenticate = (req: any, _res: any, next: any) => {
        if (authFails) return next(new UnauthorizedError('Invalid token'))
        req.user = { userId: user, isOAuth: true, oauthAudience: audience }
        next()
      }
      const app = express()
      app.use('/mcp', createMCPRouter(container))
      app.use((err: any, _req: any, res: any, _next: any) => {
        res.status(err instanceof BaseError ? err.statusCode : 500).json({ error: err.message })
      })
      const server = app.listen(0)
      const { port } = server.address() as AddressInfo
      return {
        get: (headers = {}) => fetch(`http://127.0.0.1:${port}/mcp`, { headers }),
        close: () => server.close(),
      }
    }

    beforeEach(() => {
      user = 'user-1'
      authFails = false
      audience = undefined
    })

    it('takes a token for /mcp and one with no audience, and refuses one for another resource', async () => {
      const { get, close } = await serve()
      try {
        expect((await get()).status).to.equal(405)
        audience = 'https://pipeshub.example.com/mcp'
        expect((await get()).status).to.equal(405)
        audience = 'https://other.example/api'
        const refused = await get()
        expect(refused.status).to.equal(401)
        expect(refused.headers.get('www-authenticate')).to.contain('resource_metadata=')
      } finally {
        close()
      }
    })

    it('refuses a web session token, which is not meant for MCP clients', async () => {
      mockAuthMiddleware.authenticate = (req: any, _res: any, next: any) => {
        req.user = { userId: 'user-1' }
        next()
      }
      const app = express()
      app.use('/mcp', createMCPRouter(container))
      app.use((err: any, _req: any, res: any, _next: any) => {
        res.status(err instanceof BaseError ? err.statusCode : 500).json({ error: err.message })
      })
      const server = app.listen(0)
      try {
        const { port } = server.address() as AddressInfo
        const res = await fetch(`http://127.0.0.1:${port}/mcp`)
        expect(res.status).to.equal(401)
      } finally {
        server.close()
      }
    })

    it('refuses a browser page from another origin, before authentication', async () => {
      const { get, close } = await serve()
      try {
        const res = await get({ origin: 'https://evil.example' })
        expect(res.status).to.equal(403)
        expect(await res.json()).to.deep.equal({
          jsonrpc: '2.0',
          error: { code: -32000, message: 'Origin not allowed.' },
          id: null,
        })
      } finally {
        close()
      }
    })

    it("lets through clients that send no Origin, and the instance's own origin", async () => {
      const { get, close } = await serve()
      try {
        expect((await get()).status).to.equal(405)
        expect((await get({ origin: 'https://pipeshub.example.com' })).status).to.equal(405)
      } finally {
        close()
      }
    })

    it('gives each account its own budget, answered in JSON-RPC', async () => {
      mockAppConfig.maxRequestsPerMinute = 2
      const { get, close } = await serve()
      try {
        expect((await get()).status).to.equal(405)
        expect((await get()).status).to.equal(405)
        const limited = await get()
        expect(limited.status).to.equal(429)
        expect(((await limited.json()) as any).error.message).to.contain('This account')
        user = 'user-2'
        expect((await get()).status).to.equal(405)
      } finally {
        close()
      }
    })

    it('counts an address only on rejected tokens', async () => {
      mockAppConfig.maxRequestsPerMinute = 2
      const { get, close } = await serve()
      try {
        // Accepted calls from many accounts behind one address do not use its budget.
        for (const u of ['a', 'b', 'c', 'd']) {
          user = u
          expect((await get()).status).to.equal(405)
        }
        authFails = true
        expect((await get()).status).to.equal(401)
        expect((await get()).status).to.equal(401)
        expect((await get()).status).to.equal(429)
      } finally {
        close()
      }
    })
  })
})
