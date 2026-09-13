import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createOAuthGrantsRouter } from '../../../../src/modules/oauth_provider/routes/oauth.grants.routes'

interface RouteLayer {
  route?: {
    path?: string
    stack: Array<{ name: string; handle: Function }>
  }
}

describe('OAuth Grants Routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('createOAuthGrantsRouter', () => {
    it('should be a function', () => {
      expect(createOAuthGrantsRouter).to.be.a('function')
    })

    it('should create a router when given a valid container', () => {
      const mockContainer: Pick<Container, 'get'> = {
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

      const router = createOAuthGrantsRouter(
        mockContainer as unknown as Container,
      )
      expect(router).to.exist
      expect(router.stack).to.be.an('array')
      expect(router.stack.length).to.be.greaterThan(0)
    })

    it('should register the admin routes behind userAdminCheck', () => {
      const mockContainer: Pick<Container, 'get'> = {
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

      const router = createOAuthGrantsRouter(
        mockContainer as unknown as Container,
      )
      const adminLayers = (router.stack as RouteLayer[]).filter(
        (layer) =>
          layer.route?.path === '/admin' ||
          layer.route?.path === '/admin/:grantId',
      )
      expect(adminLayers).to.have.lengthOf(2)

      for (const layer of adminLayers) {
        const handlerNames = layer.route?.stack.map((s) => s.name) ?? []
        expect(handlerNames).to.include('userAdminCheck')
      }
    })
  })
})
