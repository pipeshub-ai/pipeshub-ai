import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import type { NextFunction, Request, Response } from 'express'
import { handleBackendError } from '../../../src/libs/errors/backend-error'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { InternalServerError } from '../../../src/libs/errors/http.errors'
import { KafkaError } from '../../../src/libs/errors/kafka.errors'

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
  error: { code: string; message: string; requestId?: string }
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
} {
  const headers: Record<string, string> = {}
  let body: ErrorBody | undefined

  const res = {
    headersSent: false,
    status(): ResponseDouble {
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
  return { message: body.error.message, headers }
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
})
