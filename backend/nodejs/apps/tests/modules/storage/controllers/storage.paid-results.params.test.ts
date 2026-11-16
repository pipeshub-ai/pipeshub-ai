import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'

// A repeated or split request parameter reaches a handler as an array. Each paid-result handler must refuse it
// with a 400 before it builds a storage key or a response from it.
describe('paid-result handlers: a parameter that is not a single value', () => {
  const controller = new StorageController(
    { storageType: 'local', endpoint: 'http://127.0.0.1:1' } as never,
    { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() } as never,
    {} as never,
    'scoped-jwt-secret',
  )

  const run = async (
    handler: 'putPaidResultFile' | 'getPaidResultFile' | 'listPaidResults' | 'deletePaidResults' | 'purgePaidResultsOfRecord',
    req: Record<string, unknown>,
  ) => {
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub(), set: sinon.stub(), end: sinon.stub() }
    const next = sinon.stub()
    await (controller[handler] as (...a: unknown[]) => Promise<void>)(
      { tokenPayload: { orgId: 'org-1' }, params: {}, query: {}, body: Buffer.from('x'), ...req },
      res,
      next,
    )
    expect(res.json.called, 'no response body is built').to.equal(false)
    expect(next.calledOnce).to.equal(true)
    return next.firstCall.args[0] as unknown
  }

  const handlers = [
    'putPaidResultFile',
    'getPaidResultFile',
    'listPaidResults',
    'deletePaidResults',
    'purgePaidResultsOfRecord',
  ] as const

  for (const handler of handlers) {
    it(`${handler} refuses a record id given twice`, async () => {
      const error = await run(handler, { params: { recordId: ['rec-1', 'rec-2'], 0: 'ocr_page/a.json' }, body: handler === 'putPaidResultFile' ? Buffer.from('x') : { paths: [] } })
      expect(error).to.be.instanceOf(BadRequestError)
    })
  }

  for (const handler of ['putPaidResultFile', 'getPaidResultFile'] as const) {
    it(`${handler} refuses a path given as an array`, async () => {
      const error = await run(handler, { params: { recordId: 'rec-1', 0: ['ocr_page/a.json', 'ocr_page/b.json'] } })
      expect(error).to.be.instanceOf(BadRequestError)
    })
  }

  for (const [label, body] of [
    ['an array', ['a', 'b']],
    ['a string', 'text'],
    ['a plain object', { length: 3 }],
  ] as const) {
    it(`putPaidResultFile refuses a body that is ${label}`, async () => {
      const error = await run('putPaidResultFile', { params: { recordId: 'rec-1', 0: 'ocr_page/a.json' }, body })
      expect(error).to.be.instanceOf(BadRequestError)
    })
  }

  for (const name of ['prefix', 'after'] as const) {
    it(`listPaidResults refuses ${name} given twice`, async () => {
      const error = await run('listPaidResults', { params: { recordId: 'rec-1' }, query: { [name]: ['a', 'b'] } })
      expect(error).to.be.instanceOf(BadRequestError)
    })
  }
})
