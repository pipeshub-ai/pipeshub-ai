import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createPatRouter } from '../../../../src/modules/oauth_provider/routes/pat.routes'

describe('Personal Access Token Routes', () => {
  afterEach(() => { sinon.restore() })

  describe('createPatRouter', () => {
    it('should be a function', () => {
      expect(createPatRouter).to.be.a('function')
    })

    it('should create a router when given a valid container', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'Logger') return { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() }
          if (key === 'AppConfig') return { maxOAuthClientRequestsPerMinute: 100 }
          if (key === 'PatController') return {}
          if (key === 'AuthMiddleware') return { authenticate: sinon.stub() }
          return {}
        }),
      }

      const router = createPatRouter(mockContainer as any)
      expect(router).to.exist
      expect(router.stack).to.be.an('array')
      // GET /, POST /, GET /scopes, DELETE /:tokenId
      expect(router.stack.length).to.be.greaterThan(0)
    })
  })
})
