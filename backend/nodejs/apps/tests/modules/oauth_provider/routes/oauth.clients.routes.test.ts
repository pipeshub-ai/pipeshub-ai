import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createOAuthClientsRouter } from '../../../../src/modules/oauth_provider/routes/oauth.clients.routes'

describe('OAuth Clients Routes', () => {
  afterEach(() => { sinon.restore() })

  describe('createOAuthClientsRouter', () => {
    it('should be a function', () => {
      expect(createOAuthClientsRouter).to.be.a('function')
    })

    it('should create a router when given a valid container', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'OAuthAppController') return {}
          if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
          return {}
        }),
      }

      const router = createOAuthClientsRouter(mockContainer as any)
      expect(router).to.exist
      expect(router.stack).to.be.an('array')
      // Should have routes for GET, POST, etc.
      expect(router.stack.length).to.be.greaterThan(0)
    })
  })
})
