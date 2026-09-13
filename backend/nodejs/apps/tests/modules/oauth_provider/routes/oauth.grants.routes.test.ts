import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createOAuthGrantsRouter } from '../../../../src/modules/oauth_provider/routes/oauth.grants.routes'

describe('OAuth Grants Routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('createOAuthGrantsRouter', () => {
    it('should be a function', () => {
      expect(createOAuthGrantsRouter).to.be.a('function')
    })

    it('should create a router when given a valid container', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger')
            return {
              info: sinon.stub(),
              debug: sinon.stub(),
              warn: sinon.stub(),
              error: sinon.stub(),
            }
          if (key === 'AppConfig')
            return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'OAuthGrantController') return {}
          if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
          return {}
        }),
      }

      const router = createOAuthGrantsRouter(mockContainer as any)
      expect(router).to.exist
      expect(router.stack).to.be.an('array')
      expect(router.stack.length).to.be.greaterThan(0)
    })

    it('should register the admin routes behind userAdminCheck', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger')
            return {
              info: sinon.stub(),
              debug: sinon.stub(),
              warn: sinon.stub(),
              error: sinon.stub(),
            }
          if (key === 'AppConfig')
            return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'OAuthGrantController') return {}
          if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
          return {}
        }),
      }

      const router = createOAuthGrantsRouter(mockContainer as any)
      const adminLayers = router.stack.filter(
        (layer: any) =>
          layer.route?.path === '/admin' ||
          layer.route?.path === '/admin/:grantId',
      )
      expect(adminLayers).to.have.lengthOf(2)

      for (const layer of adminLayers) {
        const handlerNames = layer.route.stack.map((s: any) => s.name)
        expect(handlerNames).to.include('userAdminCheck')
      }
    })
  })
})
