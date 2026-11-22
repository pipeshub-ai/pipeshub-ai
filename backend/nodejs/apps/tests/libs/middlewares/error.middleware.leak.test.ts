import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import winston from 'winston'
import axios, { AxiosError } from 'axios'
import http from 'node:http'
import { AddressInfo } from 'node:net'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { Logger } from '../../../src/libs/services/logger.service'
import { withCause } from '../../../src/libs/errors/base.error'
import { BadRequestError, InternalServerError } from '../../../src/libs/errors/http.errors'
import { redactSensitiveQueryParams } from '../../../src/libs/utils/log-redaction.utils'

const MESSAGE = Symbol.for('message')

// Ported from error.middleware.utils.test.ts (#5428): the same vectors, now through the middleware's own line.
describe('ErrorMiddleware: a failed axios call is logged without its request (record, text and JSON lines)', () => {
  const TOKEN_IN_ERROR = 'AIzaSyFAKE0Echoed0Token0000000000000000'
  const LOWERCASE_TOKEN_IN_ERROR = 'abcdefghijklmnopqrstuvwxyz_abcdefghij'
  const SECRETS = [
    'RT-SECRET-123', // refresh_token
    'CS-SECRET-456', // client_secret
    'AUTH-CODE-789', // authorization code in the body
    'QUERY-CODE-000', // authorization code in the query string
    'PW-SECRET-444', // password in the URL's userinfo
    'BASIC-CRED-111', // Authorization: Basic
    'X-CUSTOM-222', // a header no redaction list names
    'RESP-BODY-333', // a value the upstream echoed in its answer
    TOKEN_IN_ERROR,
    LOWERCASE_TOKEN_IN_ERROR,
  ]
  const expectNoSecret = (logged: unknown): void => {
    const text = JSON.stringify(logged)
    for (const secret of SECRETS) expect(text, `leaked ${secret}`).to.not.include(secret)
  }

  // A fake OAuth token endpoint that refuses every call.
  const tokenHandler = (res: http.ServerResponse): void => {
    res.writeHead(400, { 'content-type': 'application/json', 'set-cookie': 'sid=RESP-BODY-333' })
    res.end(JSON.stringify({ error: 'invalid_grant', error_description: 'Bad', echoed: 'RESP-BODY-333' }))
  }
  // A Map, so a request path such as `/__proto__` cannot reach Object.prototype.
  const answers = new Map(Object.entries<(res: http.ServerResponse) => void>({
    '/token': tokenHandler,
    '/token-shaped': (res) => {
      res.writeHead(400, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: TOKEN_IN_ERROR }))
    },
    '/lowercase-token': (res) => {
      res.writeHead(400, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: LOWERCASE_TOKEN_IN_ERROR }))
    },
    '/invalid-client': (res) => {
      res.writeHead(401, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'invalid_client' }))
    },
    '/too-long': (res) => {
      res.writeHead(400, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: 'a'.repeat(65) }))
    },
    '/object': (res) => {
      res.writeHead(400, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: { code: 400, status: 'INVALID_ARGUMENT', message: 'RESP-BODY-333' } }))
    },
    '/text': (res) => {
      res.writeHead(400, { 'content-type': 'text/plain' })
      res.end('refresh_token RESP-BODY-333 was rejected')
    },
  }))
  let server: http.Server
  let base: string

  before(async () => {
    server = http.createServer((req, res) => {
      req.resume()
      req.on('end', () => {
        const path = (req.url ?? '').split('?')[0]
        // A request that never gets an answer, without depending on the host's network.
        if (path === '/hang-up') return req.socket.destroy()
        const handler = answers.get(path)
        ;(typeof handler === 'function' ? handler : tokenHandler)(res)
      })
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  after(() => new Promise<void>((resolve) => server.close(() => resolve())))

  const failedCall = async (send: () => Promise<unknown>): Promise<AxiosError> => {
    try {
      await send()
    } catch (error) {
      if (axios.isAxiosError(error)) return error
      throw error
    }
    throw new Error('expected the call to fail')
  }
  const oauthBody = { refresh_token: 'RT-SECRET-123', client_secret: 'CS-SECRET-456', code: 'AUTH-CODE-789' }
  const tokenUrl = () => `http://client:PW-SECRET-444@${base.slice('http://'.length)}/token?code=QUERY-CODE-000&state=s`
  const jsonTokenCall = (url = tokenUrl()) =>
    failedCall(() =>
      axios.post(url, oauthBody, {
        headers: { Authorization: 'Basic BASIC-CRED-111', 'X-Custom-Cred': 'X-CUSTOM-222' },
      }),
    )
  // The real path: the error middleware's one line through the real Logger, then rendered
  // by the console formats (text and LOG_FORMAT=json), the sinks an operator reads.
  const root = () => Logger.getInstance() as unknown as { logger: winston.Logger }
  let winstonLog: sinon.SinonStub
  let savedLevel: string
  let middlewareLogger: unknown
  beforeEach(() => {
    // Another file may have replaced the root since ErrorMiddleware took its logger at load.
    middlewareLogger = (ErrorMiddleware as any).logger
    ;(ErrorMiddleware as any).logger = Logger.getInstance({ service: 'ErrorMiddleware' })
    savedLevel = root().logger.level
    winstonLog = sinon.stub(root().logger, 'log')
  })
  afterEach(() => {
    ;(ErrorMiddleware as any).logger = middlewareLogger
    sinon.restore()
    delete process.env.LOG_FORMAT
    Logger.applyEnv()
    root().logger.level = savedLevel
  })
  const consoleLine = (record: Record<string, unknown>, format: 'text' | 'json'): string => {
    process.env.LOG_FORMAT = format
    Logger.applyEnv()
    const console = root().logger.transports.find((t) => t instanceof winston.transports.Console)!
    const shaped = root().logger.format.transform({ ...record }, {}) as Record<string | symbol, unknown>
    return String((console.format!.transform(shaped, {}) as Record<symbol, unknown>)[MESSAGE])
  }
  const response = (): any => {
    const res: any = { headersSent: false, status: sinon.stub(), json: sinon.stub(), setHeader: sinon.stub() }
    res.status.returns(res)
    return res
  }
  // The axios allowlist's output is the link that names the request method.
  const axiosIn = (logged: any): any => (!logged || logged.method !== undefined ? logged : axiosIn(logged.cause))
  /** The axios error as the middleware logged it; at warn it is the cause of the 4xx we answered. */
  const loggedError = (error: unknown, level: 'error' | 'warn' = 'error') => {
    const thrown = level === 'error' ? error : withCause(new BadRequestError('Bad request'), error)
    // The stub is on the shared root, and the awaited HTTP call let timers other files left running log on it.
    winstonLog.resetHistory()
    ErrorMiddleware.handleError()(thrown as Error, { method: 'POST', params: {} } as any, response(), sinon.stub())
    expect(winstonLog.callCount).to.equal(1)
    const record = winstonLog.firstCall.args[0]
    for (const format of ['text', 'json'] as const) expectNoSecret(consoleLine(record, format))
    expectNoSecret(record)
    return axiosIn(record.metadata.error)
  }

  it('drops the JSON body and headers, and keeps status, method, redacted url and the upstream code', async () => {
    const url = tokenUrl()
    const logged = loggedError(await jsonTokenCall(url))

    expectNoSecret(logged)
    expect(logged).to.include({
      name: 'AxiosError',
      code: 'ERR_BAD_REQUEST',
      status: 400,
      method: 'post',
      upstreamError: 'invalid_grant',
      url: redactSensitiveQueryParams(url),
    })
    expect(logged.url).to.include('client:').and.to.include('/token')
    expect(logged).to.not.have.any.keys('config', 'request', 'response', 'headers', 'data')
    expect(logged.stack).to.be.a('string')
  })

  for (const [name, body] of [
    ['a form body (URLSearchParams)', new URLSearchParams(oauthBody)],
    ['a text body', 'refresh_token=RT-SECRET-123&client_secret=CS-SECRET-456'],
    ['a Buffer body', Buffer.from(JSON.stringify(oauthBody))],
  ] as const) {
    it(`drops ${name}`, async () => {
      expectNoSecret(loggedError(await failedCall(() => axios.post(`${base}/token`, body))))
    })
  }

  it('drops the request of an AxiosError rethrown with the original config, request and response', async () => {
    const original = await jsonTokenCall()
    const rethrown = new AxiosError('Failed to create user', original.code, original.config, original.request, original.response)
    const logged = loggedError(rethrown)
    expectNoSecret(logged)
    expect(logged).to.include({ message: 'Failed to create user', status: 400 })
  })

  it('redacts a credential the message carries', async () => {
    // No request body, so only the message can leak.
    const original = await failedCall(() => axios.post(`${base}/token`))
    const rethrown = new AxiosError(
      'Redirect to https://app.example/cb?code=AUTH-CODE-789 failed',
      original.code, original.config, original.request, original.response,
    )
    const logged = loggedError(rethrown)
    expectNoSecret(logged)
    expect(logged.message).to.include('https://app.example/cb')
  })

  it('redacts a quoted JSON credential the message carries', async () => {
    // No request body, so only the message can leak.
    const original = await failedCall(() => axios.post(`${base}/token`))
    const rethrown = new AxiosError(
      'Token endpoint answered {"access_token":"RT-SECRET-123","error":"invalid_grant"}',
      original.code, original.config, original.request, original.response,
    )
    const logged = loggedError(rethrown)
    expectNoSecret(logged)
    expect(logged.message).to.include('"access_token":"[REDACTED]"').and.to.include('"error":"invalid_grant"')
  })

  it('keeps status, code, method and url when the error has no message', async () => {
    const original = await jsonTokenCall()
    const bare = new AxiosError(undefined, original.code, original.config, original.request, original.response)
    const logged = loggedError(bare)
    expectNoSecret(logged)
    expect(logged).to.include({ code: 'ERR_BAD_REQUEST', status: 400, method: 'post' })
    expect(logged.url).to.include('/token')
  })

  it('logs a call that got no answer by its code, without its body', async () => {
    const logged = loggedError(await failedCall(() => axios.post(`${base}/hang-up`, oauthBody)))
    expectNoSecret(logged)
    expect(logged.code).to.be.a('string').and.not.equal('ERR_BAD_REQUEST')
    expect(logged).to.include({ method: 'post' })
    expect(logged.status).to.equal(undefined)
  })

  it('at warn level, logs neither the request nor the stack', async () => {
    const logged = loggedError(await jsonTokenCall(), 'warn')
    expectNoSecret(logged)
    expect(logged).to.not.have.property('stack')
  })

  it('keeps a standard OAuth error code as upstreamError', async () => {
    const logged = loggedError(await failedCall(() => axios.post(`${base}/invalid-client`)))
    expect(logged).to.include({ status: 401, upstreamError: 'invalid_client' })
  })

  for (const [name, send] of [
    ['a token-shaped string', () => axios.post(`${base}/token-shaped`)],
    ['a lowercase credential-shaped string', () => axios.post(`${base}/lowercase-token`)],
    ['an unlisted 65-character code', () => axios.post(`${base}/too-long`)],
    ['an object', () => axios.post(`${base}/object`)],
    ['a text body', () => axios.post(`${base}/text`)],
    ['a Buffer body', () => axios.post(`${base}/token`, undefined, { responseType: 'arraybuffer' })],
  ] as const) {
    it(`keeps no upstream code when the answer's error is ${name}`, async () => {
      const logged = loggedError(await failedCall(send))
      expectNoSecret(logged)
      expect(logged).to.not.have.property('upstreamError')
    })
  }

  // Guards: these were already safe, through sanitizeForLogging's Error branch.
  it('an axios error handed to the logger directly, as a cause, or inside BaseError metadata writes no secret', async () => {
    const logger = Logger.getInstance()
    const error = await jsonTokenCall()
    const wrapped = Object.assign(new Error('wrap'), { cause: error })
    winstonLog.resetHistory()
    logger.error('as meta', error)
    logger.error('under error', { error })
    logger.error('under err', { err: error })
    logger.error('as a cause', wrapped)
    ErrorMiddleware.handleError()(wrapped, { method: 'GET', params: {} } as any, response(), sinon.stub())
    ErrorMiddleware.handleError()(new InternalServerError('x', { error }), { method: 'GET', params: {} } as any, response(), sinon.stub())
    expect(winstonLog.callCount).to.equal(6)
    for (const call of winstonLog.getCalls()) {
      expectNoSecret(call.args[0])
      for (const format of ['text', 'json'] as const) expectNoSecret(consoleLine(call.args[0], format))
    }
  })
})
