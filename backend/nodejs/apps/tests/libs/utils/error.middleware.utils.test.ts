import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  extractErrorData,
  jsonResponse,
  logError,
} from '../../../src/libs/utils/error.middleware.utils'
import { Logger } from '../../../src/libs/services/logger.service'

describe('error.middleware.utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('extractErrorData', () => {
    it('should return null for null error', () => {
      expect(extractErrorData(null)).to.be.null
    })

    it('should return null for undefined error', () => {
      expect(extractErrorData(undefined)).to.be.null
    })

    it('should return null for empty string (falsy)', () => {
      expect(extractErrorData('')).to.be.null
    })

    it('should use toJSON method when available', () => {
      const error = {
        toJSON: (includeStack: boolean) => ({
          name: 'CustomError',
          message: 'Something went wrong',
          stack: includeStack ? 'stack-trace' : undefined,
        }),
      }
      const result = extractErrorData(error)
      expect(result.name).to.equal('CustomError')
      expect(result.message).to.equal('Something went wrong')
      expect(result.stack).to.equal('stack-trace')
    })

    it('should handle axios-like error with response.data.detail', () => {
      const error = {
        response: {
          data: { detail: 'Detailed error message' },
          status: 400,
          statusText: 'Bad Request',
        },
        stack: 'at line 1',
      }
      const result = extractErrorData(error)
      expect(result.detail).to.equal('Detailed error message')
      expect(result.status).to.equal(400)
      expect(result.statusText).to.equal('Bad Request')
      expect(result.stack).to.equal('at line 1')
    })

    it('should handle axios-like error with response.data.reason', () => {
      const error = {
        response: {
          data: { reason: 'Some reason' },
          status: 422,
          statusText: 'Unprocessable Entity',
        },
        stack: 'stack',
      }
      const result = extractErrorData(error)
      expect(result.detail).to.equal('Some reason')
    })

    it('should handle axios-like error with response.data.message', () => {
      const error = {
        response: {
          data: { message: 'Error message from server' },
          status: 500,
          statusText: 'Internal Server Error',
        },
        stack: 'stack',
      }
      const result = extractErrorData(error)
      expect(result.detail).to.equal('Error message from server')
    })

    it('should use Unknown error when response.data has no detail/reason/message', () => {
      const error = {
        response: {
          data: {},
          status: 500,
          statusText: 'Server Error',
        },
        stack: 'stack',
      }
      const result = extractErrorData(error)
      expect(result.detail).to.equal('Unknown error')
    })

    it('should extract standard Error properties', () => {
      const error = new Error('Standard error')
      ;(error as any).code = 'ERR_CODE'
      const result = extractErrorData(error)
      expect(result.message).to.equal('Standard error')
      expect(result.code).to.equal('ERR_CODE')
      expect(result.name).to.equal('Error')
      expect(result.stack).to.be.a('string')
    })

    it('should handle extraction failures gracefully', () => {
      const error = {
        get toJSON() {
          throw new Error('toJSON throws')
        },
        get response() {
          throw new Error('response throws')
        },
        get message() {
          throw new Error('message throws')
        },
      }
      const result = extractErrorData(error)
      expect(result.message).to.equal('Error processing error data')
      expect(result.originalError).to.be.a('string')
    })
  })

  describe('jsonResponse', () => {
    it('should call res.status().json() with provided data', () => {
      const jsonStub = sinon.stub()
      const statusStub = sinon.stub().returns({ json: jsonStub })
      const res = { status: statusStub } as any

      jsonResponse(res, 200, { success: true })
      expect(statusStub.calledWith(200)).to.be.true
      expect(jsonStub.calledWith({ success: true })).to.be.true
    })

    it('should send 500 with SERIALIZATION_ERROR when json() throws', () => {
      const fallbackJsonStub = sinon.stub()
      const res = {
        status: sinon.stub().callsFake((code: number) => {
          if (code === 200) {
            return {
              json: sinon.stub().throws(new Error('circular ref')),
            }
          }
          return { json: fallbackJsonStub }
        }),
      } as any

      jsonResponse(res, 200, { bad: 'data' })
      expect(res.status.calledWith(500)).to.be.true
      const fallbackCall = fallbackJsonStub.firstCall
      expect(fallbackCall.args[0].error.code).to.equal('SERIALIZATION_ERROR')
    })

    it('should send plain text when both json() calls fail', () => {
      const sendStub = sinon.stub()
      const res = {
        status: sinon.stub().returns({
          json: sinon.stub().throws(new Error('json fails')),
          send: sendStub,
        }),
      } as any

      jsonResponse(res, 200, { bad: 'data' })
      expect(sendStub.calledWith('Internal server error')).to.be.true
    })
  })

  describe('logError', () => {
    it('should call logger.error with extracted error data', () => {
      const logger = {
        error: sinon.stub(),
      } as unknown as Logger

      const error = new Error('test error')
      logError(logger, 'Something failed', error, { extra: 'context' })

      expect((logger.error as sinon.SinonStub).calledOnce).to.be.true
      const call = (logger.error as sinon.SinonStub).firstCall
      expect(call.args[0]).to.equal('Something failed')
      expect(call.args[1].error).to.have.property('message', 'test error')
      expect(call.args[1].context).to.deep.equal({ extra: 'context' })
    })

    it('should fall back to console.error when logger.error throws', () => {
      const consoleStub = sinon.stub(console, 'error')
      const logger = {
        error: sinon.stub().throws(new Error('logger broken')),
      } as unknown as Logger

      logError(logger, 'Log fail test', new Error('original error'))

      expect(consoleStub.called).to.be.true
      // First console.error call: 'Error logging failed:'
      expect(consoleStub.firstCall.args[0]).to.equal('Error logging failed:')
    })

    it('should handle context being undefined', () => {
      const logger = {
        error: sinon.stub(),
      } as unknown as Logger

      logError(logger, 'No context', new Error('err'))
      expect((logger.error as sinon.SinonStub).calledOnce).to.be.true
      const call = (logger.error as sinon.SinonStub).firstCall
      expect(call.args[1].context).to.be.undefined
    })
  })
})
