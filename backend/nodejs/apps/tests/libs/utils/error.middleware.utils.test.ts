import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { jsonResponse, logLevelFor } from '../../../src/libs/utils/error.middleware.utils'
import {
  BadRequestError,
  InternalServerError,
  ServiceUnavailableError,
} from '../../../src/libs/errors/http.errors'

describe('error.middleware.utils', () => {
  afterEach(() => {
    sinon.restore()
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

  describe('logLevelFor', () => {
    it('logs a caught error at the level the shared policy gives its class', () => {
      expect(logLevelFor(new BadRequestError('x'))).to.equal('warn')
      expect(logLevelFor(new InternalServerError('x'))).to.equal('error')
      expect(logLevelFor(new ServiceUnavailableError('x'))).to.equal('warn')
      expect(logLevelFor(new Error('boom'))).to.equal('error')
      expect(logLevelFor(new DOMException('aborted', 'AbortError'))).to.equal('info')
    })
  })
})
