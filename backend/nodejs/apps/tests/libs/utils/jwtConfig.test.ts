import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  getJwtConfig,
  getJwtKeyFromConfig,
  JwtConfig,
} from '../../../src/libs/utils/jwtConfig'
import { ConfigService } from '../../../src/modules/tokens_manager/services/cm.service'
import { Logger } from '../../../src/libs/services/logger.service'

describe('jwtConfig', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getJwtConfig', () => {
    let mockLogger: Logger
    let configServiceStub: any

    beforeEach(() => {
      mockLogger = Logger.getInstance()
      // Stub ConfigService.getInstance to return a mock
      configServiceStub = {
        getJwtSecret: sinon.stub().resolves('test-jwt-secret'),
      }
      sinon.stub(ConfigService, 'getInstance').returns(configServiceStub)
    })

    it('should return HS256 config by default', async () => {
      const originalAlg = process.env.JWT_ALGORITHM
      delete process.env.JWT_ALGORITHM

      const result = await getJwtConfig(mockLogger)
      expect(result.algorithm).to.equal('HS256')
      expect(result.secret).to.equal('test-jwt-secret')

      process.env.JWT_ALGORITHM = originalAlg
    })

    it('should return HS256 config when JWT_ALGORITHM is HS256', async () => {
      const originalAlg = process.env.JWT_ALGORITHM
      process.env.JWT_ALGORITHM = 'HS256'

      const result = await getJwtConfig(mockLogger)
      expect(result.algorithm).to.equal('HS256')
      expect(result.secret).to.equal('test-jwt-secret')

      process.env.JWT_ALGORITHM = originalAlg
    })

    it('should return RS256 config when JWT_ALGORITHM is RS256', async () => {
      const originalAlg = process.env.JWT_ALGORITHM
      const originalKey = process.env.OAUTH_RSA_PRIVATE_KEY
      process.env.JWT_ALGORITHM = 'RS256'
      delete process.env.OAUTH_RSA_PRIVATE_KEY

      const originalNodeEnv = process.env.NODE_ENV
      process.env.NODE_ENV = 'test'

      const result = await getJwtConfig(mockLogger)
      expect(result.algorithm).to.equal('RS256')
      expect(result.privateKey).to.be.a('string')
      expect(result.publicKey).to.be.a('string')
      expect(result.keyId).to.be.a('string')

      process.env.JWT_ALGORITHM = originalAlg
      process.env.OAUTH_RSA_PRIVATE_KEY = originalKey
      process.env.NODE_ENV = originalNodeEnv
    })

    it('should throw in production when RS256 is used without OAUTH_RSA_PRIVATE_KEY', async () => {
      const originalAlg = process.env.JWT_ALGORITHM
      const originalKey = process.env.OAUTH_RSA_PRIVATE_KEY
      const originalNodeEnv = process.env.NODE_ENV
      process.env.JWT_ALGORITHM = 'RS256'
      delete process.env.OAUTH_RSA_PRIVATE_KEY
      process.env.NODE_ENV = 'production'

      try {
        await getJwtConfig(mockLogger)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('OAUTH_RSA_PRIVATE_KEY must be set')
      }

      process.env.JWT_ALGORITHM = originalAlg
      process.env.OAUTH_RSA_PRIVATE_KEY = originalKey
      process.env.NODE_ENV = originalNodeEnv
    })
  })

  describe('getJwtKeyFromConfig', () => {
    it('should return HS256 config with same signing and verify key for HS256', () => {
      const config: JwtConfig = {
        algorithm: 'HS256',
        secret: 'my-secret-key',
      }
      const result = getJwtKeyFromConfig(config)
      expect(result.algorithm).to.equal('HS256')
      expect(result.signingKey).to.equal('my-secret-key')
      expect(result.verifyKey).to.equal('my-secret-key')
      expect(result.keyId).to.be.undefined
    })

    it('should throw when HS256 config has no secret', () => {
      const config: JwtConfig = {
        algorithm: 'HS256',
      }
      expect(() => getJwtKeyFromConfig(config)).to.throw(
        'HS256 algorithm requires secret',
      )
    })

    it('should return RS256 config with separate signing and verify keys', () => {
      const config: JwtConfig = {
        algorithm: 'RS256',
        privateKey: 'private-key-data',
        publicKey: 'public-key-data',
        keyId: 'key-123',
      }
      const result = getJwtKeyFromConfig(config)
      expect(result.algorithm).to.equal('RS256')
      expect(result.signingKey).to.equal('private-key-data')
      expect(result.verifyKey).to.equal('public-key-data')
      expect(result.keyId).to.equal('key-123')
    })

    it('should throw when RS256 config is missing privateKey', () => {
      const config: JwtConfig = {
        algorithm: 'RS256',
        publicKey: 'public-key-data',
      }
      expect(() => getJwtKeyFromConfig(config)).to.throw(
        'RS256 algorithm requires privateKey and publicKey',
      )
    })

    it('should throw when RS256 config is missing publicKey', () => {
      const config: JwtConfig = {
        algorithm: 'RS256',
        privateKey: 'private-key-data',
      }
      expect(() => getJwtKeyFromConfig(config)).to.throw(
        'RS256 algorithm requires privateKey and publicKey',
      )
    })

    it('should throw when RS256 config is missing both keys', () => {
      const config: JwtConfig = {
        algorithm: 'RS256',
      }
      expect(() => getJwtKeyFromConfig(config)).to.throw(
        'RS256 algorithm requires privateKey and publicKey',
      )
    })

    it('should not include keyId in RS256 result when not provided', () => {
      const config: JwtConfig = {
        algorithm: 'RS256',
        privateKey: 'private-key-data',
        publicKey: 'public-key-data',
      }
      const result = getJwtKeyFromConfig(config)
      expect(result.keyId).to.be.undefined
    })
  })
})
