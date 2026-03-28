import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createOAuthRouter } from '../../../../src/modules/tokens_manager/routes/oauth.routes'

describe('tokens_manager/routes/oauth.routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('createOAuthRouter', () => {
    it('should create a router with expected routes', () => {
      const mockAuthMiddleware = { authenticate: sinon.stub() }
      const mockAppConfig = { connectorBackend: 'http://localhost:8088' }

      const container: any = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'AppConfig') return mockAppConfig
          if (key === 'AuthMiddleware') return mockAuthMiddleware
        }),
      }

      const router = createOAuthRouter(container)

      expect(router).to.exist
      const routes = (router as any).stack.filter((r: any) => r.route)
      expect(routes.length).to.be.greaterThan(0)

      // Verify key routes exist
      const paths = routes.map((r: any) => ({
        path: r.route.path,
        methods: Object.keys(r.route.methods),
      }))

      // Should have GET /registry
      expect(paths.some((p: any) => p.path === '/registry' && p.methods.includes('get'))).to.be.true
      // Should have GET /
      expect(paths.some((p: any) => p.path === '/' && p.methods.includes('get'))).to.be.true
      // Should have POST /:connectorType
      expect(paths.some((p: any) => p.path === '/:connectorType' && p.methods.includes('post'))).to.be.true
      // Should have DELETE /:connectorType/:configId
      expect(paths.some((p: any) => p.path === '/:connectorType/:configId' && p.methods.includes('delete'))).to.be.true
    })
  })
})
