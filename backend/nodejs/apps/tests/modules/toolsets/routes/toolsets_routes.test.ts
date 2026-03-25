import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createToolsetsRouter } from '../../../../src/modules/toolsets/routes/toolsets_routes'

describe('toolsets/routes/toolsets_routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('createToolsetsRouter', () => {
    it('should create a router with expected routes', () => {
      const mockAuthMiddleware = { authenticate: sinon.stub() }
      const mockAppConfig = { connectorBackend: 'http://localhost:8088' }

      const container: any = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'AppConfig') return mockAppConfig
          if (key === 'AuthMiddleware') return mockAuthMiddleware
          if (key === 'KeyValueStoreService') return { watchKey: sinon.stub() }
        }),
        isBound: sinon.stub().returns(false),
      }

      const router = createToolsetsRouter(container)

      expect(router).to.exist
      const routes = (router as any).stack.filter((r: any) => r.route)
      expect(routes.length).to.be.greaterThan(0)

      // Verify key route paths
      const paths = routes.map((r: any) => r.route.path)
      expect(paths).to.include('/registry')
      expect(paths).to.include('/')
      expect(paths).to.include('/configured')
      expect(paths).to.include('/instances')
      expect(paths).to.include('/my-toolsets')
    })
  })
})
