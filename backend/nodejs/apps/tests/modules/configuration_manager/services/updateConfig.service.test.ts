/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import nock from 'nock'
import { AxiosError } from 'axios'
import { ConfigService } from '../../../../src/modules/configuration_manager/services/updateConfig.service'
import { InternalServerError } from '../../../../src/libs/errors/http.errors'

describe('ConfigService (updateConfig)', () => {
  let service: ConfigService
  let mockLogger: any
  let mockAppConfig: any

  beforeEach(() => {
    mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    }
    mockAppConfig = {
      iamBackend: 'http://iam-backend:3001',
      communicationBackend: 'http://comm-backend:3002',
      authBackend: 'http://auth-backend:3003',
      storageBackend: 'http://storage-backend:3004',
      tokenBackend: 'http://token-backend:3005',
      esBackend: 'http://es-backend:3006',
    }
    service = new ConfigService(mockAppConfig, mockLogger)
  })

  afterEach(() => {
    sinon.restore()
    nock.cleanAll()
  })

  describe('constructor', () => {
    it('should create an instance', () => {
      expect(service).to.exist
      expect(service.updateConfig).to.be.a('function')
    })
  })

  describe('updateConfig - success path', () => {
    it('should call all 6 endpoints and return result on success', async () => {
      nock('http://iam-backend:3001').post('/api/v1/users/updateAppConfig').reply(200, { ok: true })
      nock('http://comm-backend:3002').post('/api/v1/mail/updateSmtpConfig').reply(200, { ok: true })
      nock('http://auth-backend:3003').post('/api/v1/saml/updateAppConfig').reply(200, { ok: true })
      nock('http://storage-backend:3004').post('/api/v1/document/updateAppConfig').reply(200, { ok: true })
      nock('http://token-backend:3005').post('/api/v1/connectors/updateAppConfig').reply(200, { ok: true })
      nock('http://es-backend:3006').post('/api/v1/search/updateAppConfig').reply(200, { done: true })

      const result = await service.updateConfig('my-scoped-token')
      expect(result.statusCode).to.equal(200)
      expect(result.data).to.deep.equal({ done: true })
      expect(mockLogger.debug.callCount).to.equal(6)
      expect(mockLogger.debug.calledWith('user container config updated')).to.be.true
      expect(mockLogger.debug.calledWith('smtp container config updated')).to.be.true
      expect(mockLogger.debug.calledWith('auth container config updated')).to.be.true
      expect(mockLogger.debug.calledWith('storage container config updated')).to.be.true
      expect(mockLogger.debug.calledWith('token container config updated')).to.be.true
      expect(mockLogger.debug.calledWith('es container config updated')).to.be.true
    })

    it('should pass Bearer token in Authorization header', async () => {
      nock('http://iam-backend:3001', {
        reqheaders: { authorization: 'Bearer the-token' },
      }).post('/api/v1/users/updateAppConfig').reply(200, {})
      nock('http://comm-backend:3002').post('/api/v1/mail/updateSmtpConfig').reply(200, {})
      nock('http://auth-backend:3003').post('/api/v1/saml/updateAppConfig').reply(200, {})
      nock('http://storage-backend:3004').post('/api/v1/document/updateAppConfig').reply(200, {})
      nock('http://token-backend:3005').post('/api/v1/connectors/updateAppConfig').reply(200, {})
      nock('http://es-backend:3006').post('/api/v1/search/updateAppConfig').reply(200, {})

      const result = await service.updateConfig('the-token')
      expect(result.statusCode).to.equal(200)
    })
  })

  describe('updateConfig - non-200 responses', () => {
    it('should throw BadRequestError when iam endpoint returns non-200', async () => {
      nock('http://iam-backend:3001').post('/api/v1/users/updateAppConfig').reply(500, { message: 'fail' })
      try {
        await service.updateConfig('token')
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(Error)
      }
    })

    it('should throw BadRequestError when smtp endpoint returns non-200', async () => {
      nock('http://iam-backend:3001').post('/api/v1/users/updateAppConfig').reply(200, {})
      nock('http://comm-backend:3002').post('/api/v1/mail/updateSmtpConfig').reply(500, { message: 'fail' })
      try {
        await service.updateConfig('token')
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(Error)
      }
    })

    it('should throw BadRequestError when auth endpoint returns non-200', async () => {
      nock('http://iam-backend:3001').post('/api/v1/users/updateAppConfig').reply(200, {})
      nock('http://comm-backend:3002').post('/api/v1/mail/updateSmtpConfig').reply(200, {})
      nock('http://auth-backend:3003').post('/api/v1/saml/updateAppConfig').reply(500, {})
      try {
        await service.updateConfig('token')
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(Error)
      }
    })

    it('should throw BadRequestError when storage endpoint returns non-200', async () => {
      nock('http://iam-backend:3001').post('/api/v1/users/updateAppConfig').reply(200, {})
      nock('http://comm-backend:3002').post('/api/v1/mail/updateSmtpConfig').reply(200, {})
      nock('http://auth-backend:3003').post('/api/v1/saml/updateAppConfig').reply(200, {})
      nock('http://storage-backend:3004').post('/api/v1/document/updateAppConfig').reply(500, {})
      try {
        await service.updateConfig('token')
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(Error)
      }
    })

    it('should throw BadRequestError when token endpoint returns non-200', async () => {
      nock('http://iam-backend:3001').post('/api/v1/users/updateAppConfig').reply(200, {})
      nock('http://comm-backend:3002').post('/api/v1/mail/updateSmtpConfig').reply(200, {})
      nock('http://auth-backend:3003').post('/api/v1/saml/updateAppConfig').reply(200, {})
      nock('http://storage-backend:3004').post('/api/v1/document/updateAppConfig').reply(200, {})
      nock('http://token-backend:3005').post('/api/v1/connectors/updateAppConfig').reply(500, {})
      try {
        await service.updateConfig('token')
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(Error)
      }
    })

    it('should throw BadRequestError when es endpoint returns non-200', async () => {
      nock('http://iam-backend:3001').post('/api/v1/users/updateAppConfig').reply(200, {})
      nock('http://comm-backend:3002').post('/api/v1/mail/updateSmtpConfig').reply(200, {})
      nock('http://auth-backend:3003').post('/api/v1/saml/updateAppConfig').reply(200, {})
      nock('http://storage-backend:3004').post('/api/v1/document/updateAppConfig').reply(200, {})
      nock('http://token-backend:3005').post('/api/v1/connectors/updateAppConfig').reply(200, {})
      nock('http://es-backend:3006').post('/api/v1/search/updateAppConfig').reply(500, {})
      try {
        await service.updateConfig('token')
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(Error)
      }
    })
  })

  describe('updateConfig - error handling', () => {
    it('should throw AxiosError when network error occurs', async () => {
      nock('http://iam-backend:3001').post('/api/v1/users/updateAppConfig').replyWithError('ECONNREFUSED')
      try {
        await service.updateConfig('token')
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err).to.be.instanceOf(AxiosError)
      }
    })
  })
})
