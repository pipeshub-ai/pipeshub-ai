import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'
import { createSkillsRouter } from '../../../../src/modules/skills/routes/skills.routes'

// ---------------------------------------------------------------------------
// Shared helpers (mirrors mcp_servers.routes.test.ts's pattern for this
// same shape of router: authenticate + requireScopes + thin proxy handler)
// ---------------------------------------------------------------------------

function makeContainer(overrides: Record<string, any> = {}) {
  const mockAuthMiddleware = {
    authenticate: (_req: any, _res: any, next: any) => next(),
    ...overrides.authMiddleware,
  }
  const mockAppConfig = {
    aiBackend: 'http://ai:8000',
    ...overrides.appConfig,
  }
  const container: any = {
    get: sinon.stub().callsFake((key: string) => {
      if (key === 'AppConfig') return mockAppConfig
      if (key === 'AuthMiddleware') return mockAuthMiddleware
      return undefined
    }),
  }
  return { container, mockAuthMiddleware, mockAppConfig }
}

function getRoutes(router: any) {
  return router.stack
    .filter((layer: any) => layer.route)
    .map((layer: any) => ({
      path: layer.route.path as string,
      methods: layer.route.methods as Record<string, boolean>,
      handlers: layer.route.stack as any[],
    }))
}

function findRoute(router: any, path: string, method: string) {
  return getRoutes(router).find(
    (r: any) => r.path === path && r.methods[method.toLowerCase()],
  )
}

const READ_ROUTES: Array<[string, string]> = [
  ['get', '/'],
  ['get', '/categories'],
  ['get', '/search'],
  ['get', '/candidates/pending'],
  ['get', '/:name'],
  ['get', '/:name/export'],
  ['get', '/:name/usage'],
  ['get', '/:name/versions'],
  ['get', '/:name/versions/:version'],
  ['get', '/:name/resource'],
]

const WRITE_ROUTES: Array<[string, string]> = [
  ['post', '/candidates/:candidateId/approve'],
  ['post', '/candidates/:candidateId/reject'],
  ['post', '/import/npm/preview'],
  ['post', '/import/url/preview'],
  ['post', '/import/upload/preview'],
  ['post', '/import/finalize'],
  ['post', '/'],
  ['put', '/:name'],
  ['patch', '/:name/body'],
  ['post', '/:name/deprecate'],
  ['post', '/:name/disable'],
  ['post', '/:name/enable'],
  ['delete', '/:name'],
  ['post', '/:name/rollback'],
  ['put', '/:name/resource'],
  ['delete', '/:name/resource'],
]

describe('skills/routes/skills.routes', () => {
  afterEach(() => sinon.restore())

  describe('createSkillsRouter', () => {
    it('should register the full expected route set', () => {
      const { container } = makeContainer()
      const router = createSkillsRouter(container)

      for (const [method, path] of [...READ_ROUTES, ...WRITE_ROUTES]) {
        const route = findRoute(router, path, method)
        expect(route, `${method.toUpperCase()} ${path} should be registered`).to.exist
      }
    })

    it('every route should have authenticate + scope-check + handler middleware', () => {
      const { container } = makeContainer()
      const router = createSkillsRouter(container)

      for (const route of getRoutes(router)) {
        expect(route.handlers.length).to.be.greaterThanOrEqual(
          3,
          `Route ${route.path} should have at least 3 handlers (authenticate, requireScopes, controller)`,
        )
      }
    })

    it('every route\'s first handler should be the injected AuthMiddleware.authenticate', () => {
      const authenticate = sinon.stub().callsFake((_req: any, _res: any, next: any) => next())
      const { container } = makeContainer({ authMiddleware: { authenticate } })
      const router = createSkillsRouter(container)

      for (const route of getRoutes(router)) {
        expect(route.handlers[0].handle).to.equal(authenticate)
      }
    })
  })

  // -------------------------------------------------------------------------
  // requireScopes gating — a token lacking the required scope must be
  // rejected by handlers[1] BEFORE the proxy handler (and therefore axios)
  // ever runs.
  // -------------------------------------------------------------------------
  describe('requireScopes gating', () => {
    function scopeCheckOf(router: any, path: string, method: string) {
      const route = findRoute(router, path, method)
      expect(route, `${method.toUpperCase()} ${path} should be registered`).to.exist
      return route!.handlers[1].handle
    }

    function makeReq(oauthScopes: string[]): any {
      return {
        headers: {},
        body: {},
        params: { name: 'deploy-runbook', version: '1.0.0', candidateId: 'c1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1', isOAuth: true, oauthScopes },
      }
    }

    for (const [method, path] of READ_ROUTES) {
      it(`GET-family route ${method.toUpperCase()} ${path} should require skill:read`, () => {
        const { container } = makeContainer()
        const router = createSkillsRouter(container)
        const scopeCheck = scopeCheckOf(router, path, method)
        const axiosSpy = sinon.spy(axios, 'request')

        const req = makeReq(['skill:write']) // has write, not read
        const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
        const next = sinon.stub()

        scopeCheck(req, res, next)

        expect(next.calledOnce).to.be.true
        const err = next.firstCall.args[0]
        expect(err, `${method.toUpperCase()} ${path} should reject a token missing skill:read`).to.exist
        expect(err.message).to.include('Insufficient scope')
        expect(axiosSpy.called).to.be.false
      })
    }

    for (const [method, path] of WRITE_ROUTES) {
      it(`mutating route ${method.toUpperCase()} ${path} should require skill:write`, () => {
        const { container } = makeContainer()
        const router = createSkillsRouter(container)
        const scopeCheck = scopeCheckOf(router, path, method)
        const axiosSpy = sinon.spy(axios, 'request')

        const req = makeReq(['skill:read']) // has read, not write
        const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
        const next = sinon.stub()

        scopeCheck(req, res, next)

        expect(next.calledOnce).to.be.true
        const err = next.firstCall.args[0]
        expect(err, `${method.toUpperCase()} ${path} should reject a token missing skill:write`).to.exist
        expect(err.message).to.include('Insufficient scope')
        expect(axiosSpy.called).to.be.false
      })
    }

    it('should allow a regular (non-OAuth) JWT through every scope check', () => {
      const { container } = makeContainer()
      const router = createSkillsRouter(container)

      for (const [method, path] of [...READ_ROUTES, ...WRITE_ROUTES]) {
        const scopeCheck = scopeCheckOf(router, path, method)
        const req: any = {
          headers: {},
          body: {},
          params: { name: 'deploy-runbook', version: '1.0.0', candidateId: 'c1' },
          query: {},
          user: { userId: 'u1', orgId: 'o1' },
        }
        const next = sinon.stub()
        scopeCheck(req, {}, next)
        expect(next.calledOnce, `${method.toUpperCase()} ${path}`).to.be.true
        expect(next.firstCall.args.length, `${method.toUpperCase()} ${path} should call next() with no error`).to.equal(0)
      }
    })
  })

  describe('import rate limiter placement', () => {
    it('npm/url/finalize import routes insert the limiter before the proxy handler', () => {
      const { container } = makeContainer()
      const router = createSkillsRouter(container)

      for (const path of ['/import/npm/preview', '/import/url/preview', '/import/finalize']) {
        const route = findRoute(router, path, 'post')
        expect(route, `POST ${path} should be registered`).to.exist
        expect(route!.handlers.length).to.equal(
          4,
          `POST ${path} should be authenticate, requireScopes, limiter, handler`,
        )
      }

      const npm = findRoute(router, '/import/npm/preview', 'post')
      const url = findRoute(router, '/import/url/preview', 'post')
      const finalize = findRoute(router, '/import/finalize', 'post')
      expect(npm!.handlers[2].handle).to.equal(url!.handlers[2].handle)
      expect(npm!.handlers[2].handle).to.equal(finalize!.handlers[2].handle)
    })

    it('upload preview mounts the limiter before multer so a 429 never buffers the archive', () => {
      const { container } = makeContainer()
      const router = createSkillsRouter(container)
      const upload = findRoute(router, '/import/upload/preview', 'post')
      const npm = findRoute(router, '/import/npm/preview', 'post')

      expect(upload, 'POST /import/upload/preview should be registered').to.exist
      expect(upload!.handlers.length).to.equal(
        5,
        'POST /import/upload/preview should be authenticate, requireScopes, limiter, multer, handler',
      )
      expect(upload!.handlers[2].handle).to.equal(npm!.handlers[2].handle)
    })
  })
})
