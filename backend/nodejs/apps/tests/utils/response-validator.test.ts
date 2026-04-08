import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { z } from 'zod'
import { sendValidatedJson } from '../../src/utils/response-validator'
import { Logger } from '../../src/libs/services/logger.service'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
  }
  res.status.returns(res)
  res.json.returns(res)
  res.send.returns(res)
  res.setHeader.returns(res)
  return res
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('sendValidatedJson', () => {
  let loggerInstance: Logger

  beforeEach(() => {
    loggerInstance = Logger.getInstance()
    sinon.stub(loggerInstance, 'error')
    sinon.stub(loggerInstance, 'warn')
    sinon.stub(loggerInstance, 'debug')
    sinon.stub(loggerInstance, 'info')
  })

  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // Happy path — valid payload
  // -----------------------------------------------------------------------
  describe('valid payload', () => {
    it('should call res.status with the provided status code', () => {
      const schema = z.object({ name: z.string() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { name: 'Alice' }, 200)

      expect(res.status.calledOnce).to.be.true
      expect(res.status.firstCall.args[0]).to.equal(200)
    })

    it('should call res.json with the parsed (validated) data', () => {
      const schema = z.object({ name: z.string().trim() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { name: '  Alice  ' }, 200)

      expect(res.json.calledOnce).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ name: 'Alice' })
    })

    it('should return the Response object', () => {
      const schema = z.object({ id: z.number() })
      const res = createMockResponse()

      const result = sendValidatedJson(res, schema, { id: 1 }, 200)

      expect(result).to.equal(res)
    })

    it('should send 201 status for created resources', () => {
      const schema = z.object({ id: z.string() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { id: 'abc' }, 201)

      expect(res.status.firstCall.args[0]).to.equal(201)
    })

    it('should strip unknown fields from valid payload', () => {
      const schema = z.object({ name: z.string() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { name: 'Alice', extra: 'stripped' }, 200)

      expect(res.json.firstCall.args[0]).to.deep.equal({ name: 'Alice' })
      expect(res.json.firstCall.args[0]).to.not.have.property('extra')
    })

    it('should not call logger.warn on success', () => {
      const schema = z.object({ status: z.string() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { status: 'ok' }, 200)

      expect((loggerInstance.warn as sinon.SinonStub).called).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Validation failure — invalid payload
  // -----------------------------------------------------------------------
  describe('invalid payload', () => {
    it('should log a warning when validation fails', () => {
      const schema = z.object({ count: z.number() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { count: 'not-a-number' }, 200)

      expect((loggerInstance.warn as sinon.SinonStub).calledOnce).to.be.true
      const warnArgs = (loggerInstance.warn as sinon.SinonStub).firstCall.args
      expect(warnArgs[0]).to.include('validation failed')
    })

    it('should send the original unvalidated payload when validation fails', () => {
      const schema = z.object({ count: z.number() })
      const res = createMockResponse()
      const payload = { count: 'not-a-number' }

      sendValidatedJson(res, schema, payload, 200)

      expect(res.json.calledOnce).to.be.true
      expect(res.json.firstCall.args[0]).to.equal(payload)
    })

    it('should still use the provided status code when validation fails', () => {
      const schema = z.object({ name: z.string() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { name: 123 }, 422)

      expect(res.status.firstCall.args[0]).to.equal(422)
    })

    it('should return the Response object even when validation fails', () => {
      const schema = z.object({ name: z.string() })
      const res = createMockResponse()

      const result = sendValidatedJson(res, schema, {}, 200)

      expect(result).to.equal(res)
    })

    it('should include formatted zod errors in the warning log', () => {
      const schema = z.object({ name: z.string(), age: z.number() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, {}, 200)

      const warnArgs = (loggerInstance.warn as sinon.SinonStub).firstCall.args
      expect(warnArgs[1]).to.have.property('errors')
      expect(warnArgs[1].errors).to.be.an('array')
      expect(warnArgs[1].errors.length).to.be.greaterThan(0)
    })

    it('should handle missing required fields', () => {
      const schema = z.object({ id: z.string(), name: z.string() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, {}, 200)

      expect((loggerInstance.warn as sinon.SinonStub).calledOnce).to.be.true
      expect(res.json.calledOnce).to.be.true
    })

    it('should handle completely wrong payload type', () => {
      const schema = z.object({ name: z.string() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, 'not-an-object', 200)

      expect((loggerInstance.warn as sinon.SinonStub).calledOnce).to.be.true
      expect(res.json.firstCall.args[0]).to.equal('not-an-object')
    })

    it('should handle null payload', () => {
      const schema = z.object({ name: z.string() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, null, 200)

      expect((loggerInstance.warn as sinon.SinonStub).calledOnce).to.be.true
      expect(res.json.firstCall.args[0]).to.be.null
    })
  })

  // -----------------------------------------------------------------------
  // Schema variety
  // -----------------------------------------------------------------------
  describe('schema variety', () => {
    it('should work with z.literal schema', () => {
      const schema = z.object({ status: z.literal('success') })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { status: 'success' }, 200)

      expect(res.json.firstCall.args[0]).to.deep.equal({ status: 'success' })
      expect((loggerInstance.warn as sinon.SinonStub).called).to.be.false
    })

    it('should work with nested object schema', () => {
      const schema = z.object({
        user: z.object({ id: z.string(), name: z.string() }),
      })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { user: { id: '1', name: 'Alice' } }, 200)

      expect(res.json.firstCall.args[0]).to.deep.equal({ user: { id: '1', name: 'Alice' } })
    })

    it('should work with array schema', () => {
      const schema = z.object({ items: z.array(z.string()) })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { items: ['a', 'b', 'c'] }, 200)

      expect(res.json.firstCall.args[0]).to.deep.equal({ items: ['a', 'b', 'c'] })
    })

    it('should work with optional fields', () => {
      const schema = z.object({ name: z.string(), bio: z.string().optional() })
      const res = createMockResponse()

      sendValidatedJson(res, schema, { name: 'Alice' }, 200)

      expect(res.json.firstCall.args[0]).to.deep.equal({ name: 'Alice' })
      expect((loggerInstance.warn as sinon.SinonStub).called).to.be.false
    })
  })
})
