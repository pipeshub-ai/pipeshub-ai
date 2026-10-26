import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'
import { Logger } from '../../../src/libs/services/logger.service'
import { restoreRecordsSchema } from '../../../src/modules/knowledge_base/validators/validators'

/**
 * GHSA-mrhx-c7xc-m3w5: zod before 4.6.6 recorded one issue per failing array
 * element, even for an array that is already over its .max(). A request with a
 * huge array of bad items then cost memory proportional to the array.
 * From 4.6.6 an oversized array is rejected on its size before any element is
 * parsed. Measured with 200 000 numeric items against a max of 100:
 * zod 3.24.1: 200 001 issues, ~1.6 s, ~140 MB RSS; zod 4.6.7: 1 issue, ~7 ms, ~58 MB.
 */
describe('ValidationMiddleware with an oversized array of invalid items', () => {
  beforeEach(() => {
    const l = Logger.getInstance()
    sinon.stub(l, 'error')
    sinon.stub(l, 'debug')
  })
  afterEach(() => sinon.restore())

  // The array guard is lifted here so this exercises zod's own size check.
  it('rejects on size without recording an issue per element', async () => {
    const recordIds = Array.from({ length: 200_000 }, () => 123)
    const req: any = { headers: {}, body: { recordIds }, query: {}, params: {}, path: '/t', method: 'POST' }
    const next = sinon.stub()

    const started = Date.now()
    await ValidationMiddleware.validate(restoreRecordsSchema, { maxArrayLength: 1_000_000, maxTotalItems: 1_000_000 })(req, {} as any, next)
    const elapsedMs = Date.now() - started

    const err = next.firstCall.args[0]
    expect(err).to.be.an('error')
    expect(err.statusCode).to.equal(400)
    expect(err.errors.length).to.be.at.most(5)
    expect(err.errors[0].message).to.equal('Restore at most 100 items at a time.')
    expect(elapsedMs).to.be.below(1000)
  })
})
