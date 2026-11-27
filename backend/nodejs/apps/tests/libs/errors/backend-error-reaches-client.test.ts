import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import type { NextFunction, Request, Response } from 'express'
import { handleBackendError } from '../../../src/libs/errors/backend-error'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { BadRequestError, InternalServerError } from '../../../src/libs/errors/http.errors'
import { KafkaError } from '../../../src/libs/errors/kafka.errors'
import express from 'express'
import http from 'node:http'
import { once } from 'node:events'
import type { AddressInfo } from 'node:net'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'
import { HttpMethod } from '../../../src/libs/enums/http-methods.enum'
import { ConnectorServiceCommand } from '../../../src/libs/commands/connector_service/connector.service.command'

/**
 * Every message this code writes must survive the error middleware.
 *
 * The middleware replaces a 5xx whose wording it can't vouch for, which is what
 * keeps our internals off a screen — and which silently swallowed this module's
 * own copy once already. Each case below builds an error the way production
 * does, sends it through the real middleware, and checks the person gets the
 * sentence we wrote rather than the generic line.
 */

const GENERIC = "Something went wrong on PipesHub's side"

/** What the middleware writes: `{ error: { code, message, requestId? } }`. */
interface ErrorBody {
  error: { code: string; message: string; requestId?: string; reasonCode?: string }
}

type ResponseDouble = Pick<
  Response,
  'headersSent' | 'status' | 'json' | 'send' | 'setHeader' | 'getHeader'
>
type RequestDouble = Pick<
  Request,
  'headers' | 'path' | 'method' | 'query' | 'params' | 'get'
>

interface MiddlewareLogger {
  error: sinon.SinonStub
  warn: sinon.SinonStub
  info: sinon.SinonStub
  debug: sinon.SinonStub
}

function sendThroughMiddleware(error: Error): {
  message: string
  headers: Record<string, string>
  status: number | undefined
  error: ErrorBody['error']
} {
  const headers: Record<string, string> = {}
  let body: ErrorBody | undefined
  let status: number | undefined

  const res = {
    headersSent: false,
    status(code: number): ResponseDouble {
      status = code
      return res
    },
    json(payload: ErrorBody): ResponseDouble {
      body = payload
      return res
    },
    send(): ResponseDouble {
      return res
    },
    setHeader(name: string, value: string | number | readonly string[]): ResponseDouble {
      headers[name.toLowerCase()] = String(value)
      return res
    },
    getHeader(): undefined {
      return undefined
    },
  } as unknown as ResponseDouble & { headersSent: boolean }

  const req = {
    headers: {},
    path: '/t',
    method: 'GET',
    query: {},
    params: {},
    get: () => undefined,
  } as unknown as RequestDouble

  // The middleware logs through a static it owns; swap it so a failing case
  // doesn't print a wall of stack traces, and put it back afterwards.
  const middleware = ErrorMiddleware as unknown as { logger: MiddlewareLogger }
  const originalLogger = middleware.logger
  middleware.logger = {
    error: sinon.stub(),
    warn: sinon.stub(),
    info: sinon.stub(),
    debug: sinon.stub(),
  }
  try {
    ErrorMiddleware.handleError()(
      error,
      req as Request,
      res as Response,
      sinon.stub() as unknown as NextFunction,
    )
  } finally {
    middleware.logger = originalLogger
  }

  if (!body) throw new Error('the middleware sent no JSON body')
  return { message: body.error.message, headers, status, error: body.error }
}

describe('what this module writes reaches the client', () => {
  afterEach(() => {
    sinon.restore()
  })

  const cases: Array<[string, unknown, string]> = [
    [
      'a service that refused the connection',
      Object.assign(new Error('fetch failed'), { cause: { code: 'ECONNREFUSED' } }),
      'trouble reaching one of its services',
    ],
    [
      'a service that sent no response',
      { request: {} },
      'trouble reaching one of its services',
    ],
    [
      'a body reporting ECONNREFUSED without a status',
      { data: { detail: 'ECONNREFUSED' } },
      'trouble reaching one of its services',
    ],
    ['a 500', { statusCode: 500, data: { detail: 'KeyError: llm' } }, 'Something went wrong while PipesHub tried to'],
    ['a 502', { statusCode: 502, data: {} }, 'Something went wrong while PipesHub tried to'],
    ['an unrecognised status', { statusCode: 599, data: {} }, 'Something went wrong while PipesHub tried to'],
    ['a value that is not an object', 'just a string', 'Something went wrong while PipesHub tried to'],
    ['a 503 with no detail', { statusCode: 503, data: {} }, 'briefly unavailable'],
    ['a 504 with no detail', { statusCode: 504, data: {} }, 'took longer than expected'],
  ]

  for (const [label, input, expected] of cases) {
    it(`shows our own words for ${label}`, () => {
      const mapped = handleBackendError(input, 'list connectors')
      const { message } = sendThroughMiddleware(mapped)

      expect(message).to.equal(mapped.message)
      expect(message).to.include(expected)
      expect(message).to.not.include(GENERIC)
    })
  }

  it('keeps the retry hint and still sets Retry-After', () => {
    const mapped = handleBackendError(
      { statusCode: 503, data: {}, headers: { 'retry-after': '5' } },
      'search',
    )
    const { message, headers } = sendThroughMiddleware(mapped)

    expect(message).to.equal('This part of PipesHub is briefly unavailable. Please try again in 5 seconds.')
    expect(headers['retry-after']).to.equal('5')
  })

  it('passes a sentence another service wrote for the reader', () => {
    const detail = "We couldn't confirm your sign-in just now. Please try again in a few seconds."
    const mapped = handleBackendError({ statusCode: 503, data: { detail } }, 'upload file')

    expect(sendThroughMiddleware(mapped).message).to.equal(detail)
  })

  it("passes search's sentence for a permission read that failed", () => {
    // Without it the reader sees "briefly unavailable" and no reason why
    // search came back empty.
    const detail =
      "We couldn't check which documents you have access to just now, so no results are shown. Please try again in a minute."
    const mapped = handleBackendError({ statusCode: 503, data: { detail } }, 'Search')

    expect(sendThroughMiddleware(mapped).message).to.equal(detail)
  })

  for (const message of [
    'The search index took too long to respond, so no results are shown. Please try again in a minute.',
    "We couldn't reach the search index just now, so no results are shown. Please try again in a minute.",
    'This search took too long, so it was stopped and no results are shown. Try again, or narrow it with filters or a shorter date range.',
    // An embedding provider account with no credit: "briefly unavailable, try again" would be wrong advice.
    "Search can't run because the embedding model's provider account is out of credit. An admin can add credit with the provider, or choose another embedding model in Workspace > AI Models.",
    "Search is busy right now because the embedding model's provider is limiting requests, so no results are shown. Please try again in a minute.",
  ]) {
    it(`passes search's sentence for a vector store that did not answer: ${message}`, () => {
      // Search sends it as `message` in its own body, not as `detail`.
      const mapped = handleBackendError({ statusCode: 503, data: { message } }, 'Search')

      expect(sendThroughMiddleware(mapped).message).to.equal(message)
    })
  }

  // Messages other modules already write for readers. They are HttpErrors our
  // own code builds, and each one merged in its own PR; a future tightening of
  // the middleware must not quietly flatten them.
  const writtenElsewhere: Array<[string, string]> = [
    [
      'chat (chat-error-messages.ts)',
      'Something went wrong while answering. Please send your message again, and if it keeps happening, contact your workspace admin.',
    ],
    [
      'file storage (storage/constants.ts)',
      "We couldn't save this file right now. Please try again in a moment; if it keeps failing, ask your admin to check the storage settings.",
    ],
    [
      'sign-in (userAccount.controller.ts)',
      "We couldn't save your account details. Please try again.",
    ],
  ]

  for (const [where, message] of writtenElsewhere) {
    it(`leaves the message ${where} writes alone`, () => {
      expect(sendThroughMiddleware(new InternalServerError(message)).message).to.equal(
        message,
      )
    })
  }

  it('replaces an infrastructure failure, which names the machine', () => {
    const { message } = sendThroughMiddleware(
      new KafkaError('Error publishing to Kafka topic records'),
    )

    expect(message).to.not.match(/kafka/i)
    expect(message).to.include(GENERIC)
  })

  it('still replaces a 5xx nobody vouched for', () => {
    const mapped = handleBackendError(
      { statusCode: 503, data: { detail: 'connector-service connection refused at 10.0.0.4' } },
      'search',
    )
    const { message } = sendThroughMiddleware(mapped)

    expect(message).to.not.include('10.0.0.4')
    expect(message).to.include('briefly unavailable')
  })

  // H3 RL-8: a Python refusal reaches the browser typed, not as JSON text.
  it('RL-8: a relayed 400 arrives with its status, the sentence and reasonCode', () => {
    const mapped = handleBackendError(
      {
        statusCode: 400,
        data: { detail: { reasonCode: 'runner_egress_invalid', message: "egress host 'x!' is not a host name" } },
      },
      'Update MCP server runner settings',
    )
    const sent = sendThroughMiddleware(mapped)
    expect(sent.status).to.equal(400)
    expect(sent.error).to.deep.equal({
      code: 'HTTP_BAD_REQUEST',
      message: "egress host 'x!' is not a host name",
      reasonCode: 'runner_egress_invalid',
    })
  })

  it('RL-8: a relayed 502 stays a 502 with the code and our own sentence', () => {
    const mapped = handleBackendError(
      { statusCode: 502, data: { detail: { reasonCode: 'discovery_failed', message: 'connect to 10.0.0.4 refused' } } },
      'Get MCP server tool manifest',
    )
    const sent = sendThroughMiddleware(mapped)
    expect(sent.status).to.equal(502)
    expect(sent.error.code).to.equal('HTTP_BAD_GATEWAY')
    expect(sent.error.reasonCode).to.equal('discovery_failed')
    expect(sent.message).to.include('Something went wrong while PipesHub tried to get MCP server tool manifest')
    expect(sent.message).to.not.include('10.0.0.4')
  })

  it('RL-9: no reasonCode from metadata that is not a machine code, nor on infrastructure failures', () => {
    expect(sendThroughMiddleware(new BadRequestError('no', { reasonCode: 'Not A Code' })).error).to.not.have.property('reasonCode')
    expect(sendThroughMiddleware(new BadRequestError('no', { reasonCode: 7 })).error).to.not.have.property('reasonCode')
    const plumbing = new KafkaError('Error publishing to Kafka topic records', { reasonCode: 'kafka_down' })
    expect(sendThroughMiddleware(plumbing).error).to.not.have.property('reasonCode')
  })
})

// API1-P3: a real service (or the proxy in front of it) answering a real gateway route, through the real middleware.
describe('a failed service call reaches the client with the status the service sent', () => {
  let answer: { status: number; type: string; body: string; headers?: Record<string, string> }
  let upstream: http.Server
  let gateway: http.Server
  const listen = (server: http.Server) =>
    new Promise<string>((resolve) => {
      server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${(server.address() as AddressInfo).port}`))
    })

  before(async () => {
    upstream = http.createServer((_req, res) => {
      res.writeHead(answer.status, { 'content-type': answer.type, ...answer.headers })
      res.end(answer.body)
    })
    const service = await listen(upstream)
    registerUpstreams({ connector: [service] })
    const app = express()
    app.use(requestContextMiddleware)
    app.get('/t', (_req, res, next) => {
      new ConnectorServiceCommand({ uri: `${service}/x`, method: HttpMethod.GET, retries: 1 }).execute().then(
        (reply) => {
          if (reply.statusCode !== 200) next(handleBackendError(reply, 'list connectors'))
          else res.json(reply.data)
        },
        (error: unknown) => next(handleBackendError(error, 'list connectors')),
      )
    })
    app.use(ErrorMiddleware.handleError())
    gateway = app.listen(0, '127.0.0.1')
    await once(gateway, 'listening')
  })
  beforeEach(() => {
    // The middleware's own lines are not under test here.
    sinon.stub(ErrorMiddleware as unknown as { logger: MiddlewareLogger }, 'logger').value({
      error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub(),
    })
  })
  afterEach(() => sinon.restore())
  after(() => {
    registerUpstreams({})
    upstream.close()
    gateway.close()
  })

  const get = async () => {
    const res = await fetch(`http://127.0.0.1:${(gateway.address() as AddressInfo).port}/t`)
    return { status: res.status, retryAfter: res.headers.get('retry-after'), text: await res.text() }
  }

  it("answers Starlette's text 500 with a 502 in our words and the request id", async () => {
    answer = { status: 500, type: 'text/plain', body: 'Traceback: KeyError at 10.0.0.4' }
    const { status, text } = await get()
    const body = JSON.parse(text) as ErrorBody
    expect(status).to.equal(502)
    expect(body.error.code).to.equal('HTTP_BAD_GATEWAY')
    expect(body.error.message).to.include('Something went wrong while PipesHub tried to list connectors')
    expect(body.error.requestId).to.be.a('string')
    expect(text).to.not.include('10.0.0.4')
  })

  it("answers a proxy's HTML 503 with a 503 and its Retry-After, never the page", async () => {
    answer = { status: 503, type: 'text/html', body: '<html>upstream connect error</html>', headers: { 'retry-after': '5' } }
    const { status, retryAfter, text } = await get()
    expect(status).to.equal(503)
    expect(retryAfter).to.equal('5')
    expect(text).to.not.include('<html')
  })

  it("answers a 413 with the service's words", async () => {
    answer = { status: 413, type: 'application/json', body: JSON.stringify({ detail: 'At most 500 files per call.' }) }
    const { status, text } = await get()
    expect(status).to.equal(413)
    expect((JSON.parse(text) as ErrorBody).error).to.include({ code: 'HTTP_PAYLOAD_TOO_LARGE', message: 'At most 500 files per call.' })
  })
})
