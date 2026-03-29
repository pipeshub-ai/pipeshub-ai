import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createOIDCDiscoveryRouter } from '../../../../src/modules/oauth_provider/routes/oid.provider.routes'

describe('OIDC Discovery Routes', () => {
  afterEach(() => { sinon.restore() })

  describe('createOIDCDiscoveryRouter', () => {
    it('should be a function', () => {
      expect(createOIDCDiscoveryRouter).to.be.a('function')
    })

    it('should create a router with discovery endpoints', () => {
      const mockContainer = {
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'OIDCProviderController') {
            return {
              openidConfiguration: sinon.stub(),
              oauthProtectedResource: sinon.stub(),
              jwks: sinon.stub(),
            }
          }
          return {}
        }),
      }

      const router = createOIDCDiscoveryRouter(mockContainer as any)
      expect(router).to.exist
      expect(router.stack).to.be.an('array')
      // Should have routes for openid-configuration, oauth-authorization-server, oauth-protected-resource, jwks.json
      expect(router.stack.length).to.be.greaterThan(0)
    })
  })
})
