import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { getEventListeners } from 'node:events'
import http from 'node:http'
import { AddressInfo } from 'node:net'
import { BaseCommand, ICommand } from '../../../src/libs/commands/command.interface'
import { ConnectorServiceCommand } from '../../../src/libs/commands/connector_service/connector.service.command'
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command'
import { ConfigurationManagerServiceCommand } from '../../../src/libs/commands/configuration_manager/cm.service.command'
import { IAMServiceCommand } from '../../../src/libs/commands/iam/iam.service.command'
import { handleBackendError } from '../../../src/libs/errors/backend-error'
import { BaseError } from '../../../src/libs/errors/base.error'
import { classifyRequestError } from '../../../src/libs/utils/error-class'
import { runWithRequestContext } from '../../../src/libs/context/request-context'
import { HttpMethod } from '../../../src/libs/enums/http-methods.enum'
import { InternalServerError } from '../../../src/libs/errors/http.errors'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'

// Concrete implementation for testing the abstract BaseCommand
class TestCommand extends BaseCommand<string> {
  public executeFn: () => Promise<string>

  constructor(
    uri: string,
    queryParams?: Record<string, string | number | boolean>,
    headers?: Record<string, string>,
  ) {
    super(uri, queryParams, headers)
    this.executeFn = async () => 'default'
  }

  public async execute(): Promise<string> {
    return this.executeFn()
  }

  // Expose protected methods for testing
  public testBuildUrl(): string {
    return this.buildUrl()
  }

  public testSanitizeBody(body: any): any {
    return this.sanitizeBody(body)
  }

  public testSanitizeHeaders(
    headers: Record<string, string>,
  ): Record<string, string> {
    return this.sanitizeHeaders(headers)
  }

  public testFetchWithRetry<T>(
    fn: (attempt: number) => Promise<T>,
    retries?: number,
    backoff?: number,
    shouldRetry?: (error: unknown) => boolean,
    signal?: AbortSignal,
  ): Promise<T> {
    return this.fetchWithRetry(fn, retries, backoff, shouldRetry, signal)
  }
}

describe('command.interface', () => {
  let clock: sinon.SinonFakeTimers

  beforeEach(() => {
    registerUpstreams({ test: ['http://example.com', 'http://api.local'] })
  })

  afterEach(() => {
    sinon.restore()
    registerUpstreams({})
  })

  describe('BaseCommand constructor', () => {
    it('should set uri, queryParams, and headers from arguments', () => {
      const cmd = new TestCommand(
        'http://example.com',
        { page: '1' },
        { authorization: 'Bearer tok' },
      )
      expect(cmd.testBuildUrl()).to.include('http://example.com')
      expect(cmd.testBuildUrl()).to.include('page=1')
    })

    it('should default headers to an empty object when not provided', () => {
      const cmd = new TestCommand('http://example.com')
      // sanitizeHeaders adds content-type when none present
      const sanitized = cmd.testSanitizeHeaders({})
      expect(sanitized).to.have.property('content-type', 'application/json')
    })
  })

  describe('buildUrl', () => {
    it('should return the uri as-is when no queryParams are provided', () => {
      const cmd = new TestCommand('http://api.local/items')
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items')
    })

    it('should return the uri as-is when queryParams is undefined', () => {
      const cmd = new TestCommand('http://api.local/items', undefined)
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items')
    })

    it('should append string query params', () => {
      const cmd = new TestCommand('http://api.local/items', {
        search: 'hello',
      })
      expect(cmd.testBuildUrl()).to.equal(
        'http://api.local/items?search=hello',
      )
    })

    it('should convert number query params to strings', () => {
      const cmd = new TestCommand('http://api.local/items', { page: 2 })
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items?page=2')
    })

    it('should convert boolean query params to strings', () => {
      const cmd = new TestCommand('http://api.local/items', { active: true })
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items?active=true')
    })

    it('should handle multiple query params', () => {
      const cmd = new TestCommand('http://api.local/items', {
        page: 1,
        limit: 10,
        active: true,
      })
      const url = cmd.testBuildUrl()
      expect(url).to.include('page=1')
      expect(url).to.include('limit=10')
      expect(url).to.include('active=true')
      expect(url).to.match(/^http:\/\/api\.local\/items\?/)
    })

    it('should handle empty queryParams object', () => {
      const cmd = new TestCommand('http://api.local/items', {})
      // Empty params still produces ? with empty string
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items?')
    })

    for (const [name, uri, reason] of [
      ['an unregistered origin', 'http://169.254.169.254/latest/meta-data/', 'not a registered PipesHub service'],
      ['embedded credentials', 'http://user:pass@api.local/items', 'must not contain embedded credentials'],
      ['a non-http protocol', 'ftp://api.local/items', 'Blocked request: URL scheme must be http or https'],
      ['an unparsable URI', 'not a url', 'URL is not valid'],
    ] as const) {
      it(`refuses ${name} with an InternalServerError that does not echo the URI`, () => {
        const build = (): string => new TestCommand(uri, { q: 'v' }).testBuildUrl()

        expect(build).to.throw(InternalServerError, reason)
        try {
          build()
        } catch (error) {
          expect((error as Error).message).to.not.include('169.254').and.not.include('api.local').and.not.include('not a url').and.not.include('ftp')
        }
      })
    }
  })

  describe('a command to an unregistered service', () => {
    it('is refused before its retry loop: no attempt and one rejection', async () => {
      const fetchStub = sinon.stub(globalThis, 'fetch').resolves(new Response('{}', { status: 200 }))
      const retry = sinon.spy(BaseCommand.prototype as any, 'fetchWithRetry')

      const outcome = await new ConnectorServiceCommand({
        uri: 'http://169.254.169.254/latest/meta-data/',
        method: HttpMethod.GET,
        headers: {},
      })
        .execute()
        .then(
          () => 'sent',
          (error: unknown) => error,
        )

      expect(outcome).to.be.instanceOf(InternalServerError)
      expect(fetchStub.called, 'fetch reached').to.equal(false)
      expect(retry.called, 'retry loop entered').to.equal(false)
    })
  })

  describe('sanitizeBody', () => {
    it('should return a string body as-is', () => {
      const cmd = new TestCommand('http://example.com')
      expect(cmd.testSanitizeBody('raw text')).to.equal('raw text')
    })

    it('should JSON-stringify an object body', () => {
      const cmd = new TestCommand('http://example.com')
      const obj = { key: 'value', num: 42 }
      expect(cmd.testSanitizeBody(obj)).to.equal(JSON.stringify(obj))
    })

    it('should JSON-stringify an array body', () => {
      const cmd = new TestCommand('http://example.com')
      const arr = [1, 2, 3]
      expect(cmd.testSanitizeBody(arr)).to.equal(JSON.stringify(arr))
    })

    it('should return FormData-like objects as-is (detected by _boundary)', () => {
      const cmd = new TestCommand('http://example.com')
      const formLike = { _boundary: '--boundary123', append: () => {} }
      const result = cmd.testSanitizeBody(formLike)
      expect(result).to.equal(formLike)
    })

    it('should JSON-stringify null', () => {
      const cmd = new TestCommand('http://example.com')
      expect(cmd.testSanitizeBody(null)).to.equal('null')
    })

    it('should return undefined as-is (typeof undefined is not string or object with _boundary)', () => {
      const cmd = new TestCommand('http://example.com')
      // undefined is not a string and JSON.stringify(undefined) returns undefined
      const result = cmd.testSanitizeBody(undefined)
      expect(result).to.be.undefined
    })

    it('should JSON-stringify boolean values', () => {
      const cmd = new TestCommand('http://example.com')
      expect(cmd.testSanitizeBody(true)).to.equal('true')
    })

    it('should JSON-stringify numbers', () => {
      const cmd = new TestCommand('http://example.com')
      expect(cmd.testSanitizeBody(42)).to.equal('42')
    })
  })

  describe('sanitizeHeaders', () => {
    it('keeps the /mcp relay header, which the role lookup needs', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({ 'X-Pipeshub-Mcp-Relay': 'relay-1' })
      expect(result).to.have.property('x-pipeshub-mcp-relay', 'relay-1')
    })

    it('keeps the record content-open header, which makes the connector service refuse a deleted or disabled connector', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({ 'X-Record-Content-Open': '1' })
      expect(result).to.have.property('x-record-content-open', '1')
    })

    it('should add content-type application/json when no content-type is present', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({})
      expect(result).to.have.property('content-type', 'application/json')
    })

    it('should preserve existing content-type header (lowercase)', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'content-type': 'text/plain',
      })
      expect(result).to.have.property('content-type', 'text/plain')
    })

    it('should normalize Content-Type to lowercase content-type', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'Content-Type': 'text/html',
      })
      expect(result).to.have.property('content-type', 'text/html')
      expect(result).not.to.have.property('Content-Type')
    })

    it('should collapse duplicate content-type case variants to a single header', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'content-type': 'application/json',
        'Content-Type': 'application/json',
      })
      expect(result).to.deep.equal({ 'content-type': 'application/json' })
    })

    it('should keep authorization header', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        authorization: 'Bearer token123',
      })
      expect(result).to.have.property('authorization', 'Bearer token123')
    })

    it('should drop x-is-admin header', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'x-is-admin': 'true',
      })
      expect(result).not.to.have.property('x-is-admin')
    })

    it('should remove headers that are not in the allowed set', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'x-custom-header': 'some-value',
        'cache-control': 'no-cache',
      })
      expect(result).not.to.have.property('x-custom-header')
      expect(result).not.to.have.property('cache-control')
      // Should still add content-type
      expect(result).to.have.property('content-type', 'application/json')
    })

    it('should keep x-request-id header for trace propagation', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'x-request-id': '12345',
      })
      expect(result).to.have.property('x-request-id', '12345')
    })

    it('should normalize allowed headers to lowercase keys', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        Authorization: 'Bearer xyz',
        'X-Is-Admin': 'false',
      })
      expect(result).to.have.property('authorization', 'Bearer xyz')
      expect(result).not.to.have.property('x-is-admin')
      expect(result).not.to.have.property('Authorization')
      expect(result).not.to.have.property('X-Is-Admin')
    })
  })

  describe('fetchWithRetry', () => {
    it('should return the result on first successful attempt', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub().resolves('success')
      const result = await cmd.testFetchWithRetry(fn, 3, 10)
      expect(result).to.equal('success')
      expect(fn.calledOnce).to.be.true
    })

    it('should retry on failure and return result on subsequent success', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub()
      fn.onFirstCall().rejects(new Error('fail1'))
      fn.onSecondCall().resolves('recovered')

      const result = await cmd.testFetchWithRetry(fn, 3, 1)
      expect(result).to.equal('recovered')
      expect(fn.calledTwice).to.be.true
    })

    it('should throw after exhausting all retries', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub().rejects(new Error('persistent failure'))

      try {
        await cmd.testFetchWithRetry(fn, 3, 1)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('persistent failure')
      }
      expect(fn.calledThrice).to.be.true
    })

    it('should use default values of 3 retries and 300ms backoff', async () => {
      clock = sinon.useFakeTimers()
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub()
      fn.onFirstCall().rejects(new Error('fail'))
      fn.onSecondCall().rejects(new Error('fail'))
      fn.onThirdCall().resolves('ok')

      const result = cmd.testFetchWithRetry(fn)
      await clock.runAllAsync()
      expect(await result).to.equal('ok')
      expect(fn.calledThrice).to.be.true
      expect(clock.now).to.equal(600 + 1200)
      clock.restore()
    })

    it('should throw the last error when all retries are exhausted', async () => {
      const cmd = new TestCommand('http://example.com')
      const errors = [
        new Error('error1'),
        new Error('error2'),
        new Error('error3'),
      ]
      const fn = sinon.stub()
      fn.onFirstCall().rejects(errors[0])
      fn.onSecondCall().rejects(errors[1])
      fn.onThirdCall().rejects(errors[2])

      try {
        await cmd.testFetchWithRetry(fn, 3, 1)
        expect.fail('Should have thrown')
      } catch (err: any) {
        // Should throw the last error
        expect(err.message).to.equal('error3')
      }
    })

    it('should retry only the specified number of times', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub().rejects(new Error('fail'))

      try {
        await cmd.testFetchWithRetry(fn, 2, 1)
        expect.fail('Should have thrown')
      } catch {
        // expected
      }
      expect(fn.callCount).to.equal(2)
    })

    it('should succeed on the last retry attempt', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub()
      fn.onFirstCall().rejects(new Error('fail'))
      fn.onSecondCall().rejects(new Error('fail'))
      fn.onThirdCall().resolves('last-chance')

      const result = await cmd.testFetchWithRetry(fn, 3, 1)
      expect(result).to.equal('last-chance')
    })
  })

  describe('fetchWithRetry attempt numbers', () => {
    it('passes each try its 1-based attempt number', async () => {
      const cmd = new TestCommand('http://example.com')
      const seen: number[] = []
      const fn = async (attempt: number) => {
        seen.push(attempt)
        if (attempt < 3) throw new Error('again')
        return 'ok'
      }
      expect(await cmd.testFetchWithRetry(fn, 3, 1)).to.equal('ok')
      expect(seen).to.deep.equal([1, 2, 3])
    })
  })

  // Callers pass the inbound req.headers to a command: the service must get the sanitised root, not the client's id.
  describe('a command whose redirect is refused', () => {
    let server: http.Server
    let origin: string
    let received: string[]
    let flaky: number

    before(async () => {
      server = http.createServer((req, res) => {
        req.resume()
        received.push(`${req.method ?? ''} ${req.url ?? ''}`)
        // .invalid never resolves, so nothing leaves the machine on any tree.
        if (req.url === '/submit') return void res.writeHead(303, { location: 'http://unregistered.invalid/x' }).end()
        if (req.url === '/malformed') return void res.writeHead(302, { location: 'http://[' }).end()
        if (req.url === '/flaky' && (flaky += 1) === 1) return void req.socket.destroy()
        res.writeHead(200, { 'content-type': 'application/json' }).end('{}')
      })
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
      origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    })

    beforeEach(() => {
      received = []
      flaky = 0
      registerUpstreams({ query: [origin] })
    })

    after(async () => {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    })

    const post = (path: string): Promise<unknown> =>
      new AIServiceCommand({ uri: `${origin}${path}`, method: HttpMethod.POST, headers: {}, body: { q: 1 } })
        .execute()
        .then(
          () => 'answered',
          (error: unknown) => error,
        )

    it('sends the POST once and answers the refusal of a 303 to an unregistered URL', async () => {
      const error = await post('/submit')

      expect(error).to.be.instanceOf(InternalServerError)
      expect((error as Error).message).to.equal('Blocked request: not a registered PipesHub service')
      expect(received).to.deep.equal(['POST /submit'])
    })

    it('refuses a malformed Location like an invalid URL, once, with no cause and without its text', async () => {
      const error = await post('/malformed')

      expect(error).to.be.instanceOf(InternalServerError)
      expect((error as Error).message).to.equal('Blocked request: URL is not valid')
      expect((error as { cause?: unknown }).cause).to.equal(undefined)
      expect(received).to.deep.equal(['POST /malformed'])
    })

    it('still retries a transport failure', async () => {
      expect(await post('/flaky')).to.equal('answered')
      expect(received).to.deep.equal(['POST /flaky', 'POST /flaky'])
    })
  })

  describe('the request id a command sends', () => {
    let server: http.Server
    let origin: string
    let received: Array<string | undefined>

    before(async () => {
      server = http.createServer((req, res) => {
        received.push(req.headers['x-request-id'] as string | undefined)
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end('{}')
      })
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
      origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    })

    beforeEach(() => {
      registerUpstreams({ connector: [origin] })
    })

    after(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    })

    it("is the context's root even when the caller's headers carry the client's raw id", async () => {
      received = []
      await runWithRequestContext({ rootId: 'rqbad' }, async () => {
        for (const name of ['x-request-id', 'X-Request-ID']) {
          await new ConnectorServiceCommand({
            uri: `${origin}/api/v1/stats`,
            method: HttpMethod.GET,
            headers: { [name]: 'rq bad!', authorization: 'Bearer t' },
          }).execute()
        }
      })
      expect(received).to.deep.equal(['rqbad', 'rqbad'])
    })
  })

  describe('fetchWithRetry and the caller signal', () => {
    const timedOut = () => new DOMException('The operation was aborted due to timeout', 'TimeoutError')
    // Settles without an unhandled rejection while the fake clock runs.
    const settle = <T>(p: Promise<T>) =>
      p.then(
        (value) => ({ value, error: undefined as unknown }),
        (error: unknown) => ({ value: undefined as T | undefined, error }),
      )

    beforeEach(() => {
      clock = sinon.useFakeTimers()
    })

    afterEach(() => {
      clock.restore()
    })

    it('makes no further attempt and no wait once the signal has aborted', async () => {
      const controller = new AbortController()
      const fn = sinon.stub().callsFake(async () => {
        controller.abort(timedOut())
        throw controller.signal.reason
      })

      const result = settle(new TestCommand('http://x').testFetchWithRetry(fn, 3, 300, undefined, controller.signal))
      await clock.runAllAsync()

      expect(((await result).error as Error).name).to.equal('TimeoutError')
      expect(fn.callCount).to.equal(1)
      expect(clock.now).to.equal(0)
    })

    it('ends a backoff the moment the signal aborts, with its reason', async () => {
      const controller = new AbortController()
      const fn = sinon.stub().rejects(new TypeError('fetch failed'))
      setTimeout(() => controller.abort(timedOut()), 100)

      const result = settle(new TestCommand('http://x').testFetchWithRetry(fn, 3, 300, undefined, controller.signal))
      await clock.runAllAsync()

      expect(((await result).error as Error).name).to.equal('TimeoutError')
      expect(fn.callCount).to.equal(1)
      expect(clock.now).to.equal(100)
      expect(clock.countTimers()).to.equal(0)
    })

    it('leaves no abort listener behind after a backoff that ran its course', async () => {
      const controller = new AbortController()
      const fn = sinon.stub()
      fn.onFirstCall().rejects(new TypeError('fetch failed'))
      fn.onSecondCall().resolves('ok')

      const result = settle(new TestCommand('http://x').testFetchWithRetry(fn, 3, 300, undefined, controller.signal))
      await clock.runAllAsync()

      expect((await result).value).to.equal('ok')
      expect(clock.now).to.equal(600)
      expect(getEventListeners(controller.signal, 'abort')).to.have.length(0)
    })

    it('keeps every attempt and the full backoff while the signal stays live', async () => {
      const controller = new AbortController()
      const fn = sinon.stub().rejects(new TypeError('fetch failed'))

      const result = settle(new TestCommand('http://x').testFetchWithRetry(fn, 3, 300, undefined, controller.signal))
      await clock.runAllAsync()

      expect(((await result).error as Error).message).to.equal('fetch failed')
      expect(fn.callCount).to.equal(3)
      expect(clock.now).to.equal(600 + 1200)
    })

    it('without a signal, waits the plain backoff as before', async () => {
      const fn = sinon.stub().rejects(new TypeError('fetch failed'))

      const result = settle(new TestCommand('http://x').testFetchWithRetry(fn, 2, 300))
      await clock.runAllAsync()

      expect(((await result).error as Error).message).to.equal('fetch failed')
      expect(fn.callCount).to.equal(2)
      expect(clock.now).to.equal(600)
    })

    it('throws the last attempt error with no wait when one attempt is allowed', async () => {
      const controller = new AbortController()
      controller.abort(timedOut())
      const refused = new TypeError('fetch failed')
      const fn = sinon.stub().rejects(refused)

      const result = settle(new TestCommand('http://x').testFetchWithRetry(fn, 1, 300, undefined, controller.signal))

      expect((await result).error).to.equal(refused)
      expect(clock.countTimers()).to.equal(0)
    })
  })

  describe('fetchWithRetry edge case', () => {
    it('should throw InternalServerError when retries is 0 (unreachable guard)', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub().resolves('should-not-run')

      try {
        await cmd.testFetchWithRetry(fn, 0, 1)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('Unexpected error in retry logic.')
      }
      expect(fn.called).to.be.false
    })
  })

  describe('ICommand interface compliance', () => {
    it('should satisfy the ICommand interface by implementing execute()', async () => {
      const cmd: ICommand<string> = new TestCommand('http://example.com')
      const result = await cmd.execute()
      expect(result).to.equal('default')
    })
  })

  // API1-P3 (A-04): a proxy's HTML or an ingress's text in front of a service. The status must survive the body.
  describe('readBody: an error answer that is not JSON keeps its status, on every command', () => {
    const URI = 'http://up.local/a'
    const commands: [string, () => Promise<{ statusCode: number; data?: unknown }>][] = [
      ['connector', () => new ConnectorServiceCommand({ uri: URI, method: HttpMethod.GET, retries: 1 }).execute()],
      ['ai', () => new AIServiceCommand({ uri: URI, method: HttpMethod.GET, headers: {}, maxAttempts: 1 }).execute()],
      ['cm', () => new ConfigurationManagerServiceCommand({ uri: URI, method: HttpMethod.GET, headers: {} }).execute()],
      ['iam', () => new IAMServiceCommand({ uri: URI, method: HttpMethod.GET, headers: {} }).execute()],
    ]
    let fetchStub: sinon.SinonStub
    beforeEach(() => {
      registerUpstreams({ up: ['http://up.local'] })
      fetchStub = sinon.stub(globalThis, 'fetch')
    })

    const answer = (status: number, body: string | null, statusText: string, headers: Record<string, string> = {}) =>
      new Response(body, { status, statusText, headers: { 'content-type': 'text/html', ...headers } })
    const mapped = async (run: () => Promise<unknown>): Promise<BaseError> =>
      handleBackendError(await run(), 'do the thing') as BaseError
    const rejection = async (run: () => Promise<unknown>): Promise<unknown> => {
      try {
        await run()
      } catch (error) {
        return error
      }
      return undefined
    }

    for (const [name, run] of commands) {
      it(`${name}: a proxy's HTML 503 stays 503 and keeps its Retry-After`, async () => {
        fetchStub.resolves(answer(503, '<html>upstream connect error</html>', 'Service Unavailable', { 'retry-after': '5' }))
        const result = await mapped(run)
        expect(result.statusCode).to.equal(503)
        expect(result.metadata?.retryAfter).to.equal('5')
        expect(result.message).to.include('5 seconds')
      })

      it(`${name}: a proxy's HTML 400 stays 400`, async () => {
        fetchStub.resolves(answer(400, '<html>400 Bad Request</html>', 'Bad Request'))
        const result = await mapped(run)
        expect(result.statusCode).to.equal(400)
        expect(result.message).to.equal('Bad Request')
      })

      it(`${name}: nginx's HTML 413 stays 413 in its status text's words, never the page`, async () => {
        fetchStub.resolves(answer(413, '<html>413 Request Entity Too Large</html>', 'Request Entity Too Large'))
        const result = await mapped(run)
        expect(result.statusCode).to.equal(413)
        expect(result.message).to.equal('Request Entity Too Large')
      })

      it(`${name}: a 413 with an empty status text reads the standard reason phrase`, async () => {
        fetchStub.resolves(answer(413, 'too big', ''))
        expect((await mapped(run)).message).to.equal('Payload Too Large')
      })

      it(`${name}: Starlette's text 500 is an upstream 5xx answered 502, not our bug`, async () => {
        fetchStub.resolves(answer(500, 'Internal Server Error', 'Internal Server Error', { 'content-type': 'text/plain' }))
        const result = await mapped(run)
        expect(result.statusCode).to.equal(502)
        expect(classifyRequestError(result, 502)).to.equal('upstream_5xx')
      })

      it(`${name}: a 204 has no data and its body is not read`, async () => {
        const response = new Response(null, { status: 204 })
        const json = sinon.spy(response, 'json')
        fetchStub.resolves(response)
        const result = await run()
        expect(result.statusCode).to.equal(204)
        expect(result.data).to.equal(undefined)
        expect(json.called).to.equal(false)
      })

      it(`${name}: a 200 that is not JSON still fails, so a broken success is not hidden`, async () => {
        fetchStub.resolves(answer(200, '<html>ok</html>', 'OK'))
        expect(await rejection(run)).to.be.instanceOf(SyntaxError)
      })

      it(`${name}: a 4xx whose body cannot be read for another reason still fails`, async () => {
        const response = answer(404, '{}', 'Not Found')
        sinon.stub(response, 'json').rejects(new TypeError('terminated'))
        fetchStub.resolves(response)
        expect(await rejection(run)).to.be.instanceOf(TypeError)
      })
    }
  })
})
