import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'
import { verifyTurnstileToken } from '../../../src/libs/utils/turnstile-verification'
import { Logger } from '../../../src/libs/services/logger.service'

describe('turnstile-verification', () => {
  let axiosPostStub: sinon.SinonStub
  let logger: Logger

  beforeEach(() => {
    axiosPostStub = sinon.stub(axios, 'post')
    logger = {
      warn: sinon.stub(),
      debug: sinon.stub(),
      error: sinon.stub(),
      info: sinon.stub(),
    } as unknown as Logger
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('verifyTurnstileToken', () => {
    it('should return false when token is undefined', async () => {
      const result = await verifyTurnstileToken(undefined, 'secret-key')
      expect(result).to.be.false
    })

    it('should return false when token is empty string (falsy)', async () => {
      const result = await verifyTurnstileToken('', 'secret-key')
      expect(result).to.be.false
    })

    it('should log a warning when token is missing and logger is provided', async () => {
      await verifyTurnstileToken(undefined, 'secret-key', undefined, logger)
      expect((logger.warn as sinon.SinonStub).calledOnce).to.be.true
      expect(
        (logger.warn as sinon.SinonStub).calledWith(
          'Turnstile token is missing',
        ),
      ).to.be.true
    })

    it('should return true when secretKey is undefined (skip verification)', async () => {
      const result = await verifyTurnstileToken('some-token', undefined)
      expect(result).to.be.true
    })

    it('should log a warning when secretKey is not configured and logger is provided', async () => {
      await verifyTurnstileToken('some-token', undefined, undefined, logger)
      expect((logger.warn as sinon.SinonStub).calledOnce).to.be.true
      expect(
        (logger.warn as sinon.SinonStub).firstCall.args[0],
      ).to.include('Turnstile secret key is not configured')
    })

    it('should return true on successful verification', async () => {
      axiosPostStub.resolves({
        data: {
          success: true,
          challenge_ts: '2024-01-01T00:00:00Z',
          hostname: 'example.com',
        },
      })

      const result = await verifyTurnstileToken(
        'valid-token',
        'secret-key',
      )
      expect(result).to.be.true
    })

    it('should call the correct Cloudflare URL', async () => {
      axiosPostStub.resolves({ data: { success: true } })

      await verifyTurnstileToken('token', 'secret')
      expect(axiosPostStub.calledOnce).to.be.true
      expect(axiosPostStub.firstCall.args[0]).to.equal(
        'https://challenges.cloudflare.com/turnstile/v0/siteverify',
      )
    })

    it('should send secret and response in the request body', async () => {
      axiosPostStub.resolves({ data: { success: true } })

      await verifyTurnstileToken('my-token', 'my-secret')
      const requestData = axiosPostStub.firstCall.args[1]
      expect(requestData.secret).to.equal('my-secret')
      expect(requestData.response).to.equal('my-token')
    })

    it('should include remoteip when userIp is provided', async () => {
      axiosPostStub.resolves({ data: { success: true } })

      await verifyTurnstileToken('token', 'secret', '1.2.3.4')
      const requestData = axiosPostStub.firstCall.args[1]
      expect(requestData.remoteip).to.equal('1.2.3.4')
    })

    it('should not include remoteip when userIp is not provided', async () => {
      axiosPostStub.resolves({ data: { success: true } })

      await verifyTurnstileToken('token', 'secret')
      const requestData = axiosPostStub.firstCall.args[1]
      expect(requestData).not.to.have.property('remoteip')
    })

    it('should log debug info on successful verification when logger is provided', async () => {
      axiosPostStub.resolves({
        data: {
          success: true,
          challenge_ts: '2024-01-01T00:00:00Z',
          hostname: 'example.com',
        },
      })

      await verifyTurnstileToken('token', 'secret', undefined, logger)
      expect((logger.debug as sinon.SinonStub).calledOnce).to.be.true
    })

    it('should return false on failed verification', async () => {
      axiosPostStub.resolves({
        data: {
          success: false,
          'error-codes': ['invalid-input-response'],
        },
      })

      const result = await verifyTurnstileToken('bad-token', 'secret')
      expect(result).to.be.false
    })

    it('should log a warning on failed verification when logger is provided', async () => {
      axiosPostStub.resolves({
        data: {
          success: false,
          'error-codes': ['invalid-input-response'],
        },
      })

      await verifyTurnstileToken('bad-token', 'secret', undefined, logger)
      expect((logger.warn as sinon.SinonStub).calledOnce).to.be.true
      expect(
        (logger.warn as sinon.SinonStub).firstCall.args[0],
      ).to.include('Turnstile verification failed')
    })

    it('should return false when axios throws an error', async () => {
      axiosPostStub.rejects(new Error('Network error'))

      const result = await verifyTurnstileToken('token', 'secret')
      expect(result).to.be.false
    })

    it('should log error when axios throws and logger is provided', async () => {
      axiosPostStub.rejects(new Error('Network error'))

      await verifyTurnstileToken('token', 'secret', undefined, logger)
      expect((logger.error as sinon.SinonStub).calledOnce).to.be.true
      expect(
        (logger.error as sinon.SinonStub).firstCall.args[0],
      ).to.include('Error verifying Turnstile token')
    })

    it('should not log when no logger is provided', async () => {
      axiosPostStub.resolves({ data: { success: true } })
      // Just ensure no error is thrown when logger is not provided
      const result = await verifyTurnstileToken('token', 'secret')
      expect(result).to.be.true
    })
  })
})
