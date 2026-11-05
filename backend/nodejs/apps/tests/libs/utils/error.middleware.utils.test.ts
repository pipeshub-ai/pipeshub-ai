import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  extractErrorData,
  jsonResponse,
  logError,
} from '../../../src/libs/utils/error.middleware.utils'
import { Logger } from '../../../src/libs/services/logger.service'
import { InternalServerError } from '../../../src/libs/errors/http.errors'
import { redactSensitiveQueryParams } from '../../../src/libs/utils/log-redaction.utils'
import axios, { AxiosError } from 'axios'
import http from 'node:http'
import { AddressInfo } from 'node:net'

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

    // A warning is an answer that went as intended: no stack, whatever shape
    // the error has.
    for (const [name, make] of [
      ['a generic Error', () => new Error('plain')],
      ['an axios-style error', () => Object.assign(new Error('Request failed'), {
        response: { status: 404, statusText: 'Not Found', data: { detail: 'gone' } },
      })],
      // A real one is summarised by extractErrorData, not through its own toJSON.
      ['a real AxiosError', () => new AxiosError(
        'Request failed with status code 404', 'ERR_BAD_REQUEST', undefined, undefined,
        { status: 404, statusText: 'Not Found', data: { detail: 'gone' }, headers: {}, config: {} } as never,
      )],
    ] as const) {
      it(`logs ${name} at warn without its stack, and at error with it`, () => {
        const logger = { warn: sinon.stub(), error: sinon.stub() } as unknown as Logger

        logError(logger, 'Answered', make(), undefined, 'warn')
        logError(logger, 'Failed', make())

        expect((logger.warn as sinon.SinonStub).firstCall.args[1].error).to.not.have.property('stack')
        expect((logger.error as sinon.SinonStub).firstCall.args[1].error).to.have.property('stack')
      })
    }

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

// Every value below is fake. A failed axios call carries its whole request in
// `config`, with the body already a string, so no key-based redaction sees it.
describe('error.middleware.utils: a failed axios call is logged without its request', () => {
  const TOKEN_IN_ERROR = 'AIzaSyFAKE0Echoed0Token0000000000000000'
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
  ]
  const expectNoSecret = (logged: unknown): void => {
    const text = JSON.stringify(logged)
    for (const secret of SECRETS) expect(text, `leaked ${secret}`).to.not.include(secret)
  }

  // A fake OAuth token endpoint that refuses every call.
  const answers: Record<string, (res: http.ServerResponse) => void> = {
    '/token': (res) => {
      res.writeHead(400, { 'content-type': 'application/json', 'set-cookie': 'sid=RESP-BODY-333' })
      res.end(JSON.stringify({ error: 'invalid_grant', error_description: 'Bad', echoed: 'RESP-BODY-333' }))
    },
    '/token-shaped': (res) => {
      res.writeHead(400, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: TOKEN_IN_ERROR }))
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
  }
  let server: http.Server
  let base: string

  before(async () => {
    server = http.createServer((req, res) => {
      req.resume()
      req.on('end', () => {
        const path = (req.url ?? '').split('?')[0]
        // A request that never gets an answer, without depending on the host's network.
        if (path === '/hang-up') return req.socket.destroy()
        ;(answers[path] ?? answers['/token'])(res)
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
  const stubLogger = () =>
    ({ error: sinon.stub(), warn: sinon.stub() }) as unknown as Logger & {
      error: sinon.SinonStub
      warn: sinon.SinonStub
    }
  const loggedError = (error: unknown, level: 'error' | 'warn' = 'error') => {
    const logger = stubLogger()
    logError(logger, 'Unhandled error', error, undefined, level)
    return logger[level].firstCall.args[1].error
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

  for (const [name, send] of [
    ['a token-shaped string', () => axios.post(`${base}/token-shaped`)],
    ['longer than 64 characters', () => axios.post(`${base}/too-long`)],
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

  describe('through the real Logger (what reaches winston)', () => {
    let winstonLog: sinon.SinonStub
    beforeEach(() => {
      winstonLog = sinon.stub((Logger.getInstance() as unknown as { logger: { log: () => void } }).logger, 'log')
    })
    afterEach(() => sinon.restore())

    it('logError writes no secret', async () => {
      logError(Logger.getInstance(), 'Unhandled error', await jsonTokenCall())
      expect(winstonLog.callCount).to.equal(1)
      expectNoSecret(winstonLog.firstCall.args[0])
    })

    // Guards: these were already safe, through sanitizeForLogging's Error branch.
    it('an axios error handed to the logger directly, as a cause, or inside BaseError metadata writes no secret', async () => {
      const logger = Logger.getInstance()
      const error = await jsonTokenCall()
      const wrapped = Object.assign(new Error('wrap'), { cause: error })
      logger.error('as meta', error)
      logger.error('under error', { error })
      logger.error('under err', { err: error })
      logger.error('as a cause', wrapped)
      logError(logger, 'cause via logError', wrapped)
      logError(logger, 'in metadata', new InternalServerError('x', { error }))
      expect(winstonLog.callCount).to.equal(6)
      for (const call of winstonLog.getCalls()) expectNoSecret(call.args[0])
    })
  })
})
