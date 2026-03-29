import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import crypto from 'crypto'
import { RSAKeyService } from '../../../../src/modules/oauth_provider/services/rsa_key.service'

describe('RSAKeyService', () => {
  let mockLogger: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
      debug: sinon.stub(),
    }
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('constructor - auto-generate keys', () => {
    it('should generate a new key pair when no PEM is provided', () => {
      const service = new RSAKeyService(mockLogger)
      expect(service.getPrivateKey()).to.include('-----BEGIN PRIVATE KEY-----')
      expect(service.getPublicKey()).to.include('-----BEGIN PUBLIC KEY-----')
      expect(service.getKeyId()).to.be.a('string')
    })

    it('should generate a new key pair when empty string is provided', () => {
      const service = new RSAKeyService(mockLogger, '')
      expect(service.getPrivateKey()).to.include('PRIVATE KEY')
    })

    it('should generate a new key pair when invalid PEM is provided', () => {
      const service = new RSAKeyService(mockLogger, 'not-a-pem')
      expect(service.getPrivateKey()).to.include('PRIVATE KEY')
      expect(mockLogger.info.called).to.be.true
    })
  })

  describe('constructor - load existing key', () => {
    it('should load key pair from valid PEM', () => {
      const { privateKey } = crypto.generateKeyPairSync('rsa', {
        modulusLength: 2048,
        publicKeyEncoding: { type: 'spki', format: 'pem' },
        privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
      })

      const service = new RSAKeyService(mockLogger, privateKey)
      expect(service.getPrivateKey()).to.equal(privateKey)
      expect(service.getPublicKey()).to.include('-----BEGIN PUBLIC KEY-----')
    })

    it('should fallback to generation when PEM load fails', () => {
      // Pass a PEM-looking string that's actually invalid
      const badPem = '-----BEGIN PRIVATE KEY-----\ninvalid\n-----END PRIVATE KEY-----'
      const service = new RSAKeyService(mockLogger, badPem)
      expect(service.getPrivateKey()).to.include('PRIVATE KEY')
      expect(mockLogger.warn.called).to.be.true
    })
  })

  describe('getJWK', () => {
    it('should return a valid JWK structure', () => {
      const service = new RSAKeyService(mockLogger)
      const jwk = service.getJWK()
      expect(jwk.kty).to.equal('RSA')
      expect(jwk.use).to.equal('sig')
      expect(jwk.alg).to.equal('RS256')
      expect(jwk.kid).to.be.a('string')
      expect(jwk.n).to.be.a('string')
      expect(jwk.e).to.be.a('string')
    })
  })

  describe('getJWKS', () => {
    it('should return a JWKS with one key', () => {
      const service = new RSAKeyService(mockLogger)
      const jwks = service.getJWKS()
      expect(jwks.keys).to.be.an('array')
      expect(jwks.keys).to.have.lengthOf(1)
      expect(jwks.keys[0].kty).to.equal('RSA')
    })
  })

  describe('exportPrivateKeyPem', () => {
    it('should return the private key PEM', () => {
      const service = new RSAKeyService(mockLogger)
      const pem = service.exportPrivateKeyPem()
      expect(pem).to.include('-----BEGIN PRIVATE KEY-----')
    })
  })

  describe('getKeyId', () => {
    it('should return a hex string key ID', () => {
      const service = new RSAKeyService(mockLogger)
      const kid = service.getKeyId()
      expect(kid).to.match(/^[0-9a-f]+$/)
      expect(kid.length).to.equal(16) // 8 bytes * 2 hex chars
    })
  })

  describe('key pair consistency', () => {
    it('should produce a public key that matches the private key', () => {
      const service = new RSAKeyService(mockLogger)
      const data = 'test-data'
      const sign = crypto.createSign('SHA256')
      sign.update(data)
      const signature = sign.sign(service.getPrivateKey(), 'hex')

      const verify = crypto.createVerify('SHA256')
      verify.update(data)
      expect(verify.verify(service.getPublicKey(), signature, 'hex')).to.be.true
    })
  })
})
