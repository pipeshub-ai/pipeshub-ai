import 'reflect-metadata'
import { expect } from 'chai'
import { AxiosError, AxiosHeaders } from 'axios'
import {
  classifyError,
  classifyRequestError,
  ERROR_CLASSES,
  levelFor,
} from '../../../src/libs/utils/error-class'
import {
  BadGatewayError,
  BadRequestError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../src/libs/errors/http.errors'
import { withCause } from '../../../src/libs/errors/base.error'
import { MongoNetworkError, MongoServerSelectionError } from 'mongodb'

const axiosAnswered = (status: number): AxiosError =>
  new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_RESPONSE', undefined, {}, {
    status,
    statusText: '',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: {},
  })
const fetchRefused = (): TypeError =>
  Object.assign(new TypeError('fetch failed'), {
    cause: Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:8000'), { code: 'ECONNREFUSED' }),
  })

/** What the driver throws when our own Mongo is down: MongoServerSelectionError -> MongoNetworkError -> ECONNREFUSED. */
const mongoDown = (): MongoServerSelectionError => {
  const refused = Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:27017'), { code: 'ECONNREFUSED' })
  const network = new MongoNetworkError('connect ECONNREFUSED 127.0.0.1:27017', { cause: refused })
  return new MongoServerSelectionError('connect ECONNREFUSED 127.0.0.1:27017', { error: network } as never)
}

describe('error-class', () => {
  describe('classifyError(status): the table shared with Python', () => {
    const table: [number, string][] = [
      [400, 'bad_request'], [401, 'auth_expired'], [402, 'bad_request'], [403, 'forbidden'], [404, 'not_found'],
      [408, 'timeout'], [409, 'conflict'], [410, 'not_found'], [413, 'bad_request'], [422, 'bad_request'],
      [429, 'rate_limited'], [500, 'upstream_5xx'], [502, 'upstream_5xx'], [503, 'unavailable'], [504, 'timeout'],
      [599, 'upstream_5xx'], [600, 'upstream_5xx'],
    ]
    for (const [status, cls] of table) {
      it(`${status} -> ${cls}`, () => expect(classifyError(status)).to.equal(cls))
    }
    it('a status below 400 or not an integer is internal', () => {
      for (const status of [200, 399, 0, -1, 404.5, Number.NaN]) expect(classifyError(status)).to.equal('internal')
    })
  })

  describe('classifyError(error): the cause chain decides', () => {
    it('our 4xx answers by their status', () => {
      expect(classifyError(new UnauthorizedError('x'))).to.equal('auth_expired')
      expect(classifyError(new NotFoundError('x'))).to.equal('not_found')
      expect(classifyError(new ServiceUnavailableError('x'))).to.equal('unavailable')
    })

    it('our 500 is internal unless its cause says more', () => {
      expect(classifyError(new InternalServerError('x'))).to.equal('internal')
      expect(classifyError(withCause(new InternalServerError('x'), axiosAnswered(502)))).to.equal('upstream_5xx')
    })

    it('an unreachable service is unavailable, also as fetch\'s TypeError with an ECONNREFUSED cause', () => {
      expect(classifyError(fetchRefused())).to.equal('unavailable')
      expect(classifyError(Object.assign(new Error('x'), { code: 'ECONNREFUSED', request: {} }))).to.equal('unavailable')
    })

    it('a timed-out call is a timeout (AbortSignal.timeout and axios)', () => {
      expect(classifyError(new DOMException('timed out', 'TimeoutError'))).to.equal('timeout')
      expect(classifyError(Object.assign(new Error('x'), { code: 'ECONNABORTED', request: {} }))).to.equal('timeout')
    })

    it('a cancelled call is cancelled', () => {
      expect(classifyError(new DOMException('aborted', 'AbortError'))).to.equal('cancelled')
      expect(classifyError(Object.assign(new Error('canceled'), { name: 'CanceledError', code: 'ERR_CANCELED' }))).to.equal('cancelled')
    })

    it('a bug of ours is internal and stops the walk', () => {
      expect(classifyError(new TypeError("Cannot read properties of undefined (reading 'x')"))).to.equal('internal')
      expect(classifyError(new RangeError('Invalid array length'))).to.equal('internal')
    })

    it('an axios answer, http-errors and gaxios-style errors classify by their status', () => {
      expect(classifyError(axiosAnswered(429))).to.equal('rate_limited')
      expect(classifyError(axiosAnswered(502))).to.equal('upstream_5xx')
      expect(classifyError(Object.assign(new Error('bad param'), { status: 400 }))).to.equal('bad_request')
      expect(classifyError(Object.assign(new Error('gone'), { statusCode: 410 }))).to.equal('not_found')
    })

    it('terminates on a cause cycle and on a deep chain, and anything odd is internal', () => {
      const a: any = new InternalServerError('a')
      const b: any = new InternalServerError('b')
      withCause(a, b)
      withCause(b, a)
      expect(classifyError(a)).to.equal('internal')
      let deep: Error = axiosAnswered(429)
      for (let i = 0; i < 9; i += 1) deep = withCause(new InternalServerError(`w${i}`), deep)
      expect(classifyError(deep)).to.equal('internal')
      expect(classifyError(undefined)).to.equal('internal')
      expect(classifyError('fetch failed')).to.equal('internal')
      const hostile = new Proxy({}, { get: () => { throw new Error('no') } })
      expect(classifyError(hostile)).to.equal('internal')
    })
  })

  describe('classifyRequestError: agrees with the status we answered', () => {
    it('a 500 of ours never takes a 4xx class from its cause', () => {
      expect(classifyRequestError(withCause(new InternalServerError('x'), axiosAnswered(404)), 500)).to.equal('internal')
      expect(classifyRequestError(Object.assign(new Error('bad'), { status: 400 }), 500)).to.equal('internal')
    })

    it('a 500 of ours takes an upstream class from its cause', () => {
      expect(classifyRequestError(withCause(new InternalServerError('x'), axiosAnswered(502)), 500)).to.equal('upstream_5xx')
      expect(classifyRequestError(withCause(new InternalServerError('x'), fetchRefused()), 500)).to.equal('unavailable')
    })

    it('a driver error nobody wrapped is ours, even with ECONNREFUSED in its chain (our own database down)', () => {
      expect(classifyError(mongoDown())).to.equal('unavailable')
      expect(classifyRequestError(mongoDown(), 500)).to.equal('internal')
      expect(classifyRequestError(fetchRefused(), 500)).to.equal('internal')
      expect(classifyRequestError(new DOMException('timed out', 'TimeoutError'), 500)).to.equal('internal')
      expect(classifyRequestError(withCause(new InternalServerError('x'), mongoDown()), 500)).to.equal('unavailable')
    })

    it('any other status decides by itself; a failure after a 2xx is ours', () => {
      expect(classifyRequestError(new BadRequestError('x'), 400)).to.equal('bad_request')
      expect(classifyRequestError(new BadGatewayError('x'), 502)).to.equal('upstream_5xx')
      expect(classifyRequestError(new Error('stream broke'), 200)).to.equal('internal')
      expect(classifyRequestError(undefined, 404)).to.equal('not_found')
    })
  })

  describe('levelFor: the one level policy', () => {
    it('our fault is ERROR, cancelled INFO, everything else WARN', () => {
      const levels = Object.fromEntries(ERROR_CLASSES.map((cls) => [cls, levelFor(cls)]))
      expect(levels).to.deep.equal({
        auth_expired: 'warn', forbidden: 'warn', not_found: 'warn', rate_limited: 'warn', timeout: 'warn',
        upstream_5xx: 'warn', bad_request: 'warn', conflict: 'warn', unavailable: 'warn',
        cancelled: 'info', misconfigured: 'error', internal: 'error',
      })
      expect(levelFor('made_up' as never)).to.equal('error')
    })
  })
})
