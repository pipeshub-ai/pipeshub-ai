import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import type { NextFunction, Request, Response } from 'express'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { UnauthorizedError } from '../../../src/libs/errors/http.errors'

/**
 * What the middleware sends must be what the published spec promises.
 *
 * The error schema sets `additionalProperties: false`, so a field added to the
 * body without being added to the spec makes every response-validation test
 * fail at once - which is how `requestId` took out 127 of them, and only on the
 * nightly, hours after it merged. This checks the same thing on every run.
 */

const SPEC = join(
  __dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml',
)

interface ErrorSchema {
  additionalProperties?: boolean
  required?: string[]
  properties?: Record<string, { type?: string }>
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

/**
 * Every error payload in the spec, not just the shared one.
 *
 * Routes declare their own error envelopes inline as well as referencing
 * `ErrorResponse`, and each carries its own `additionalProperties: false`. A
 * check against the shared schema alone would pass while an inline one still
 * rejected the body.
 */
function errorPayloadSchemas(): Array<{ where: string; schema: ErrorSchema }> {
  const spec = yaml.load(readFileSync(SPEC, 'utf8'))
  const found: Array<{ where: string; schema: ErrorSchema }> = []

  const walk = (node: unknown, path: string): void => {
    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(item, `${path}[${i}]`))
      return
    }
    if (!isRecord(node)) return

    const properties = node.properties
    if (
      node.additionalProperties === false &&
      isRecord(properties) &&
      'code' in properties &&
      'message' in properties
    ) {
      found.push({ where: path, schema: node as ErrorSchema })
    }
    for (const [key, value] of Object.entries(node)) walk(value, `${path}.${key}`)
  }

  walk(spec, '')
  return found
}

function bodyFromMiddleware(error: Error, requestId?: string): Record<string, unknown> {
  let body: { error: Record<string, unknown> } | undefined
  const res = {
    headersSent: false,
    status: () => res,
    json: (payload: { error: Record<string, unknown> }) => {
      body = payload
      return res
    },
    send: () => res,
    setHeader: () => res,
    getHeader: () => undefined,
  } as unknown as Response

  const req = {
    headers: {}, path: '/t', method: 'GET', query: {}, params: {},
    get: () => undefined,
    ...(requestId ? { context: { requestId } } : {}),
  } as unknown as Request

  const middleware = ErrorMiddleware as unknown as { logger: Record<string, sinon.SinonStub> }
  const original = middleware.logger
  middleware.logger = {
    error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub(),
  }
  try {
    ErrorMiddleware.handleError()(
      error, req, res, sinon.stub() as unknown as NextFunction,
    )
  } finally {
    middleware.logger = original
  }
  if (!body) throw new Error('the middleware sent no JSON body')
  return body.error
}

describe('the error body matches the published spec', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('sends no field any error schema in the spec forbids', () => {
    const schemas = errorPayloadSchemas()
    expect(schemas.length, 'the spec should still declare error payloads').to.be.greaterThan(0)

    const body = bodyFromMiddleware(new UnauthorizedError('No token provided'), 'req-123')
    const sent = Object.keys(body)

    const rejected = schemas.flatMap(({ where, schema }) =>
      sent
        .filter((key) => !Object.keys(schema.properties ?? {}).includes(key))
        .map((key) => `${where}: ${key}`),
    )

    expect(
      rejected,
      `add it to that schema in ${SPEC.split('src/')[1]} before shipping it`,
    ).to.deep.equal([])
  })

  it('declares the right type for every field it sends', () => {
    const body = bodyFromMiddleware(new UnauthorizedError('No token provided'), 'req-123')

    const wrong = errorPayloadSchemas().flatMap(({ where, schema }) =>
      Object.entries(body)
        .filter(([key, value]) => {
          const declared = schema.properties?.[key]?.type
          return declared !== undefined && declared !== typeof value
        })
        .map(([key, value]) => `${where}: ${key} is ${typeof value}`),
    )

    expect(wrong).to.deep.equal([])
  })

  it('sends everything every error schema requires', () => {
    const sent = Object.keys(bodyFromMiddleware(new UnauthorizedError('No token provided')))
    const missing = errorPayloadSchemas().flatMap(({ where, schema }) =>
      (schema.required ?? []).filter((key) => !sent.includes(key)).map((key) => `${where}: ${key}`),
    )
    expect(missing).to.deep.equal([])
  })

  it('still names the request when one was assigned', () => {
    const sent = bodyFromMiddleware(new UnauthorizedError('No token provided'), 'req-123')
    expect(sent.requestId).to.equal('req-123')
  })
})
