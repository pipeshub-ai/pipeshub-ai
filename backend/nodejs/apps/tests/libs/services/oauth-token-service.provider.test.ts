import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  resolveOAuthTokenService,
  registerOAuthTokenService,
} from '../../../src/libs/services/oauth-token-service.provider'

describe('oauth-token-service.provider', () => {
  afterEach(() => {
    sinon.restore()
    // Reset the module-level instance to null
    registerOAuthTokenService(null as any)
  })

  describe('resolveOAuthTokenService', () => {
    it('should return null before any service is registered', () => {
      // Reset by registering null
      registerOAuthTokenService(null as any)
      const result = resolveOAuthTokenService()
      expect(result).to.be.null
    })

    it('should return the registered service after registration', () => {
      const mockService = { mockMethod: sinon.stub() } as any
      registerOAuthTokenService(mockService)
      const result = resolveOAuthTokenService()
      expect(result).to.equal(mockService)
    })
  })

  describe('registerOAuthTokenService', () => {
    it('should register a service that can be resolved later', () => {
      const mockService = { getToken: sinon.stub() } as any
      registerOAuthTokenService(mockService)
      expect(resolveOAuthTokenService()).to.equal(mockService)
    })

    it('should overwrite a previously registered service', () => {
      const service1 = { id: 1 } as any
      const service2 = { id: 2 } as any
      registerOAuthTokenService(service1)
      registerOAuthTokenService(service2)
      expect(resolveOAuthTokenService()).to.equal(service2)
    })
  })
})
