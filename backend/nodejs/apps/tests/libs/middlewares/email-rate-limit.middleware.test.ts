import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  addressRateLimitPerMinute,
  createEmailRateLimiter,
  lazyRedisCounter,
} from '../../../src/libs/middlewares/email-rate-limit.middleware'
import { TooManyRequestsError } from '../../../src/libs/errors/http.errors'

const logger = { warn: sinon.stub(), error: sinon.stub() } as any

/** A counter that behaves like Redis INCR: one value per key. */
function counter() {
  const seen = new Map<string, number>()
  return {
    seen,
    increment: sinon.spy(async (key: string) => {
      seen.set(key, (seen.get(key) ?? 0) + 1)
      return seen.get(key) as number
    }),
  }
}

const run = async (mw: any, email?: string, res: any = { setHeader: sinon.stub() }): Promise<unknown> => {
  let result: unknown = 'pending'
  await mw({ body: { email } }, res, (e?: unknown) => (result = e))
  return result
}

describe('per-address rate limiter', () => {
  // Date only, fixed 20 s into a minute: the counter is per clock minute, and a rollover mid-test would split it
  beforeEach(() => {
    sinon.useFakeTimers({ now: 1_700_000_000_000, toFake: ['Date'] })
  })
  afterEach(() => {
    sinon.restore()
  })

  it('lets the first requests about an address through and refuses the ones over the limit', async () => {
    const redis = counter()
    const mw = createEmailRateLimiter(redis as any, logger, { prefix: 'address-lookup', maxPerMinute: 3, emailOf: (r) => r.body?.email })
    for (let i = 0; i < 3; i++) expect(await run(mw, 'a@example.com')).to.equal(undefined)
    expect(await run(mw, 'a@example.com')).to.be.instanceOf(TooManyRequestsError)
    // another address has its own count
    expect(await run(mw, 'b@example.com')).to.equal(undefined)
  })

  it('answers a request over the limit with the route\'s own answer when onLimit is given, and does not continue', async () => {
    const redis = counter()
    const onLimit = sinon.stub()
    const mw = createEmailRateLimiter(redis as any, logger, { prefix: 'p', maxPerMinute: 1, emailOf: (r) => r.body?.email, onLimit })
    const res = { setHeader: sinon.stub() }
    expect(await run(mw, 'a@example.com', res)).to.equal(undefined)
    expect(await run(mw, 'a@example.com', res)).to.equal('pending')
    expect(onLimit.calledOnceWithExactly(res)).to.equal(true)
    expect(res.setHeader.called).to.equal(false)
  })

  it('tells the client when to come back: Retry-After is the seconds left in the minute', async () => {
    const redis = counter()
    const mw = createEmailRateLimiter(redis as any, logger, { prefix: 'p', maxPerMinute: 1, emailOf: (r) => r.body?.email })
    await run(mw, 'a@example.com')
    const res = { setHeader: sinon.stub() }
    expect(await run(mw, 'a@example.com', res)).to.be.instanceOf(TooManyRequestsError)
    expect(res.setHeader.calledOnce).to.equal(true)
    expect(res.setHeader.firstCall.args[0]).to.equal('Retry-After')
    expect(Number(res.setHeader.firstCall.args[1])).to.be.within(1, 60)
  })

  it('counts an address once whatever its case or spacing, and stores only a hash of it', async () => {
    const redis = counter()
    const mw = createEmailRateLimiter(redis as any, logger, { prefix: 'p', maxPerMinute: 1, emailOf: (r) => r.body?.email })
    await run(mw, ' Mixed@Example.com ')
    expect(await run(mw, 'mixed@example.com')).to.be.instanceOf(TooManyRequestsError)
    for (const key of redis.seen.keys()) {
      expect(key).to.match(/^rl:email:p:[0-9a-f]{32}:\d+$/)
      expect(key).to.not.include('example')
    }
    expect(redis.increment.firstCall.args[1]).to.deep.equal({ ttl: 120 })
  })

  it('passes a request that names no address, and does not count it', async () => {
    const redis = counter()
    const mw = createEmailRateLimiter(redis as any, logger, { prefix: 'p', maxPerMinute: 1, emailOf: (r) => r.body?.email })
    expect(await run(mw, undefined)).to.equal(undefined)
    expect(redis.increment.called).to.equal(false)
  })

  it('does not count, and does not hang on, an address that is not a string', async () => {
    const redis = counter()
    const mw = createEmailRateLimiter(redis as any, logger, { prefix: 'p', maxPerMinute: 1, emailOf: (r) => r.body?.email })
    for (const email of [123, ['a@example.com'], { $ne: 1 }, true]) expect(await run(mw, email as any)).to.equal(undefined)
    expect(redis.increment.called).to.equal(false)
  })

  it('lets the request through when the counter cannot be reached', async () => {
    const mw = createEmailRateLimiter(
      lazyRedisCounter(() => {
        throw new Error('no redis')
      }),
      logger,
      { prefix: 'p', maxPerMinute: 1, emailOf: (r) => r.body?.email },
    )
    expect(await run(mw, 'a@example.com')).to.equal(undefined)
  })

  it('reads the limit from ADDRESS_RATE_LIMIT_PER_MINUTE, 60 when unset or not a positive number', () => {
    const saved = process.env.ADDRESS_RATE_LIMIT_PER_MINUTE
    try {
      delete process.env.ADDRESS_RATE_LIMIT_PER_MINUTE
      expect(addressRateLimitPerMinute()).to.equal(60)
      process.env.ADDRESS_RATE_LIMIT_PER_MINUTE = '5'
      expect(addressRateLimitPerMinute()).to.equal(5)
      process.env.ADDRESS_RATE_LIMIT_PER_MINUTE = '-1'
      expect(addressRateLimitPerMinute()).to.equal(60)
    } finally {
      if (saved === undefined) delete process.env.ADDRESS_RATE_LIMIT_PER_MINUTE
      else process.env.ADDRESS_RATE_LIMIT_PER_MINUTE = saved
    }
  })
})
