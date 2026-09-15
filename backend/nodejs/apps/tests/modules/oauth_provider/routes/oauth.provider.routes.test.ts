import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createOAuthProviderRouter } from '../../../../src/modules/oauth_provider/routes/oauth.provider.routes'

describe('OAuth Provider Routes', () => {
  afterEach(() => { sinon.restore() })

  describe('createOAuthProviderRouter', () => {
    it('should be a function', () => {
      expect(createOAuthProviderRouter).to.be.a('function')
    })

    it('should create a router when given a valid container', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AuthTokenService') return { verifyToken: sinon.stub() }
          if (key === 'AppConfig') return { frontendUrl: 'http://localhost:3000', maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'OAuthProviderController') return {}
          if (key === 'OIDCProviderController') return {}
          if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
          if (key === 'OAuthAuthMiddleware') return { authenticate: sinon.stub(), requireScopes: sinon.stub().returns(sinon.stub()) }
          return {}
        }),
      }

      const router = createOAuthProviderRouter(mockContainer as any)
      expect(router).to.exist
      expect(router.stack).to.be.an('array')
      const routes = (router as any).stack
        .filter((layer: any) => layer.route)
        .map((layer: any) => {
          const method = Object.keys(layer.route.methods).find(
            (m: string) => layer.route.methods[m],
          )
          return `${method}:${layer.route.path}`
        })
      expect(routes).to.include('post:/device_authorization')
      expect(routes).to.include('post:/device/verify')
      expect(routes).to.include('post:/device/consent')
      expect(routes).to.include('post:/register')
      expect(routes).to.include('post:/token')
    })
  })
})
