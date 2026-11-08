import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'
import { Logger } from '../../../src/libs/services/logger.service'
import { findArrayLengthViolation, readPositiveInt } from '../../../src/libs/utils/array-length-guard'
import { createPatTokenSchema } from '../../../src/modules/oauth_provider/validators/pat.validators'

/**
 * knowledgeLimit.apps is a real request array with no .max(): without the guard
 * zod records one issue per bad element (GHSA-mrhx-c7xc-m3w5).
 */
async function run(body: unknown, options?: { maxArrayLength?: number }) {
  const req: any = { headers: {}, body, query: {}, params: {}, path: '/t', method: 'POST' }
  const next = sinon.stub()
  await ValidationMiddleware.validate(createPatTokenSchema, options)(req, {} as any, next)
  expect(next.calledOnce).to.equal(true)
  return next.firstCall.args[0]
}

describe('ValidationMiddleware array length guard', () => {
  beforeEach(() => {
    const l = Logger.getInstance()
    sinon.stub(l, 'error')
    sinon.stub(l, 'debug')
  })
  afterEach(() => sinon.restore())

  it('rejects a 2M-element array of bad items quickly and with bounded memory', async () => {
    const apps = new Array(2_000_000).fill(123)
    if (global.gc) global.gc()
    const heapBefore = process.memoryUsage().heapUsed
    const started = Date.now()
    const err = await run({ name: 'n', knowledgeLimit: { apps } })
    const elapsedMs = Date.now() - started
    const heapGrowthMb = (process.memoryUsage().heapUsed - heapBefore) / 1e6

    expect(err.statusCode).to.equal(400)
    expect(err.errors).to.have.length(1)
    expect(err.errors[0].field).to.equal('body.knowledgeLimit.apps')
    expect(err.errors[0].code).to.equal('TOO_BIG')
    expect(err.errors[0].message).to.equal('body.knowledgeLimit.apps can have at most 10000 items.')
    expect(elapsedMs).to.be.below(1000)
    expect(heapGrowthMb).to.be.below(50)
  })

  it('still validates an array of exactly the limit normally', async () => {
    const err = await run({ name: 'n', knowledgeLimit: { apps: new Array(10_000).fill(123) } })
    expect(err.errors.length).to.be.greaterThan(1)
    expect(err.errors[0].field).to.match(/^body\.knowledgeLimit\.apps\.\d+$/)
  })

  it('catches an array nested inside objects and arrays, naming the path', async () => {
    const err = await run({ name: 'n', knowledgeLimit: { apps: [] }, extra: [{ deep: { list: [[], new Array(6).fill(1)] } }] }, { maxArrayLength: 5 })
    expect(err.errors).to.have.length(1)
    expect(err.errors[0].field).to.equal('body.extra.0.deep.list.1')
  })

  it('applies to query and params too', () => {
    const v = findArrayLengthViolation({ body: {}, query: { id: new Array(4).fill('a') }, params: {} }, { maxArrayLength: 3 })
    expect(v).to.deep.equal({ path: 'query.id', reason: 'array_too_long', limit: 3 })
  })

  it('rejects data nested deeper than the depth bound without recursing', () => {
    let deep: any = {}
    const root = deep
    for (let i = 0; i < 200_000; i++) {
      deep.a = {}
      deep = deep.a
    }
    const v = findArrayLengthViolation({ body: root })
    expect(v?.reason).to.equal('too_deep')
    expect(v?.limit).to.equal(64)
  })

  it('answers with the TOO_DEEP error through the middleware', async () => {
    let deep: any = {}
    const root = deep
    for (let i = 0; i < 100; i++) {
      deep.a = {}
      deep = deep.a
    }
    const err = await run({ name: 'n', root })
    expect(err.statusCode).to.equal(400)
    expect(err.errors[0].code).to.equal('TOO_DEEP')
  })

  it('accepts a request with no oversized array', async () => {
    const req: any = { headers: {}, body: { name: 'ok' }, query: {}, params: {}, path: '/t', method: 'POST' }
    const next = sinon.stub()
    await ValidationMiddleware.validate(createPatTokenSchema)(req, {} as any, next)
    expect(next.calledOnceWithExactly()).to.equal(true)
  })
})

describe('array guard: uploads, record bodies and limits', () => {
  beforeEach(() => {
    const l = Logger.getInstance()
    sinon.stub(l, 'error')
    sinon.stub(l, 'debug')
    sinon.stub(l, 'warn')
  })
  afterEach(() => sinon.restore())

  it('does not walk Buffers or typed arrays (a 30 MB upload costs next to nothing)', () => {
    const body = { fileBuffers: [{ buffer: Buffer.alloc(30 * 1024 * 1024) }], other: new Uint8Array(30 * 1024 * 1024), when: new Date() }
    const started = process.hrtime.bigint()
    const v = findArrayLengthViolation({ body })
    const ms = Number(process.hrtime.bigint() - started) / 1e6
    expect(v).to.equal(undefined)
    expect(ms).to.be.below(20)
  })

  it('refuses an object with too many keys (a 600k-key record) with a 400, not a 500', async () => {
    const tools: Record<string, number> = {}
    for (let i = 0; i < 600_000; i++) tools[`k${i}`] = 1
    const req: any = { headers: {}, body: { tools }, query: {}, params: {}, path: '/t', method: 'PUT' }
    const next = sinon.stub()
    await ValidationMiddleware.validate(createPatTokenSchema)(req, {} as any, next)
    const err = next.firstCall.args[0]
    expect(err.statusCode).to.equal(400)
    expect(err.errors[0].field).to.equal('body.tools')
    expect(err.errors[0].message).to.equal('body.tools can have at most 10000 fields.')
  })

  it('refuses a request whose arrays are each under the cap but too big together', () => {
    const body = Array.from({ length: 30 }, () => new Array(9_999).fill(1))
    const v = findArrayLengthViolation({ body }, { maxTotalItems: 100_000 })
    expect(v?.reason).to.equal('too_many_items')
    expect(findArrayLengthViolation({ body: body.slice(0, 5) }, { maxTotalItems: 100_000 })).to.equal(undefined)
  })

  it('reads limits strictly: plain decimal integers of at least 1, anything else warns and uses the default', () => {
    const warn = Logger.getInstance().warn as sinon.SinonStub
    const read = (value: string | undefined) => readPositiveInt('X', 10_000, value === undefined ? {} : { X: value })
    expect(read(undefined)).to.equal(10_000)
    expect(read('250')).to.equal(250)
    for (const bad of ['0', '-5', 'NaN', ' ', '10.5', 'abc', '0x10', '1e3', '1e20', ' 5', '5 ', '+5', '007']) {
      expect(read(bad), bad).to.equal(10_000)
    }
    expect(warn.callCount).to.equal(13)
  })
})
