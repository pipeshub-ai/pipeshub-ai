import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import rateLimit from 'express-rate-limit'
import sinon from 'sinon'
import { z } from 'zod'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import { smtpConfigSchema } from '../../../src/modules/configuration_manager/validator/validators'
import { createPatTokenSchema } from '../../../src/modules/oauth_provider/validators/pat.validators'
import { tokenSchema } from '../../../src/modules/oauth_provider/validators/oauth.validators'
import { oauthTokenValidationErrorHandler } from '../../../src/modules/oauth_provider/middlewares/oauth.token-validation-error.middleware'
import { LogRecord, captureLogRecords, newRequestId, recordsOf } from '../../helpers/log-capture'

// N5k: a request the schema refuses is the caller's mistake. The error middleware already writes its one
// failure line (`http.request.error`, WARNING `bad_request`, with the field, rule and request id); the
// validation middleware must not add a second line at ERROR. The body and query values the caller sent stay
// out of every line. `POST /oauth2/token` answers its refusals itself (RFC 6749 body), so the error middleware
// never runs there: its handler writes the refusal's one line, DEBUG `oauth.request.refused`.

const PASSWORD = 'N5K-FAKE-pw-0123456789'
const FROM = 'n5k-fake-ada@example.com'
const CLIENT_SECRET = 'N5K-FAKE-client-secret-0123456789'
const PARAM = 'n5k-fake-param@example.com'

describe('ValidationMiddleware: a refused request is logged once, at WARNING, without the values sent', () => {
  let server: http.Server
  let port = 0
  let records: LogRecord[]
  let handled = 0

  before(async () => {
    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    app.use(express.json())
    app.use(rateLimit({ windowMs: 60_000, limit: 10_000 })) // test server only; satisfies the missing-rate-limiting check
    const done = (_req: express.Request, res: express.Response) => {
      handled += 1
      res.json({})
    }
    // The oauth.provider.routes.ts order: the schema, the endpoint, then the handler that answers a refusal.
    app.post('/api/v1/oauth2/token', ValidationMiddleware.validate(tokenSchema), done, oauthTokenValidationErrorHandler)
    app.get(
      '/api/v1/things/:thingId',
      ValidationMiddleware.validate(z.object({ params: z.object({ thingId: z.string().regex(/^[0-9a-f]{24}$/) }) })),
      done,
    )
    app.get(
      '/api/v1/things',
      ValidationMiddleware.validate(z.object({ query: z.object({ owner: z.email() }) })),
      done,
    )
    app.post('/api/v1/configurationManager/smtpConfig', ValidationMiddleware.validate(smtpConfigSchema), done)
    app.post('/api/v1/oauth2/pat', ValidationMiddleware.validate(createPatTokenSchema), done)
    app.post(
      '/api/v1/items',
      ValidationMiddleware.validate(z.object({ body: z.object({ ids: z.array(z.string()) }) }), { maxArrayLength: 2 }),
      done,
    )
    app.post(
      '/api/v1/deep',
      // Stands in for zod overflowing the stack on a request with about a million issues.
      ValidationMiddleware.validate(
        z.object({
          body: z.any().refine(() => {
            throw new RangeError('Maximum call stack size exceeded')
          }),
        }),
      ),
      done,
    )
    app.use(ErrorMiddleware.handleError())
    server = http.createServer((req, res) => void app(req, res))
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    port = (server.address() as AddressInfo).port
  })

  after(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  beforeEach(() => {
    records = captureLogRecords()
    handled = 0
  })

  afterEach(() => {
    sinon.restore()
  })

  /** Sends the request and returns the answer and the request's lines once its access line is in. */
  const send = async (method: 'GET' | 'POST', path: string, body?: unknown) => {
    const requestId = newRequestId('n5k')
    const payload = body === undefined ? '' : JSON.stringify(body)
    const answer = await new Promise<{ status: number; text: string }>((resolve, reject) => {
      const req = http.request(
        {
          host: '127.0.0.1',
          port,
          path,
          method,
          headers: {
            'content-type': 'application/json',
            'content-length': Buffer.byteLength(payload),
            'x-request-id': requestId,
          },
        },
        (res) => {
          let text = ''
          res.setEncoding('utf8')
          res.on('data', (chunk: string) => (text += chunk))
          res.on('end', () => resolve({ status: res.statusCode ?? 0, text }))
        },
      )
      req.on('error', reject)
      req.end(payload)
    })
    const deadline = Date.now() + 5000
    while (Date.now() < deadline && !recordsOf(records, requestId).some((r) => r.message === 'http.request.end')) {
      await new Promise((resolve) => setImmediate(resolve))
    }
    return { ...answer, requestId, lines: recordsOf(records, requestId) }
  }
  const post = (path: string, body: unknown) => send('POST', path, body)
  const get = (path: string) => send('GET', path)

  const levelsAndMessages = (lines: LogRecord[]) => lines.map((l) => [l.level, l.message])

  it('a schema refusal writes one failure line, WARNING bad_request, and no ERROR', async () => {
    const r = await post('/api/v1/configurationManager/smtpConfig', {
      host: 'smtp.example.com',
      port: 'twenty-five',
      username: FROM,
      password: PASSWORD,
      fromEmail: FROM,
    })

    expect(r.status).to.equal(400)
    expect(handled).to.equal(0)
    expect(levelsAndMessages(r.lines)).to.deep.equal([
      ['warning', 'http.request.error'],
      ['info', 'http.request.end'],
    ])
  })

  it('the array guard refusal writes no ERROR line', async () => {
    const r = await post('/api/v1/items', { ids: ['a', 'b', 'c'] })

    expect(r.status).to.equal(400)
    expect(levelsAndMessages(r.lines)).to.deep.equal([
      ['warning', 'http.request.error'],
      ['info', 'http.request.end'],
    ])
  })

  it('the stack-overflow safety net writes no ERROR line', async () => {
    const r = await post('/api/v1/deep', { a: 1 })

    expect(r.status).to.equal(400)
    expect(levelsAndMessages(r.lines)).to.deep.equal([
      ['warning', 'http.request.error'],
      ['info', 'http.request.end'],
    ])
  })

  // Controls: what support needs is on the failure line, and the values sent are on no line. Both hold on main
  // and must keep holding after the fix.
  it('the failure line names the route, status, class, request id, field and rule', async () => {
    const r = await post('/api/v1/configurationManager/smtpConfig', { host: 'h', port: 'x', fromEmail: 'f' })

    const failure = r.lines.find((l) => l.message === 'http.request.error')
    expect(failure, 'http.request.error').to.not.equal(undefined)
    expect(failure).to.include({
      method: 'POST',
      route: '/api/v1/configurationManager/smtpConfig',
      status: 400,
      error_class: 'bad_request',
      request_id: r.requestId,
    })
    const errors = failure!.metadata.error.metadata.errors as Array<Record<string, unknown>>
    expect(errors.map((e) => [e.field, e.code])).to.deep.equal([['body.port', 'INVALID_TYPE']])
  })

  it('the values sent are on no line, and the 400 body still names the field', async () => {
    const r = await post('/api/v1/configurationManager/smtpConfig', {
      host: 'smtp.example.com',
      port: 'twenty-five',
      username: FROM,
      password: PASSWORD,
      fromEmail: FROM,
    })

    const text = JSON.stringify(r.lines)
    expect(text).to.not.include(PASSWORD)
    expect(text).to.not.include(FROM)
    expect(text).to.not.include('twenty-five')
    expect(JSON.parse(r.text).error).to.include({ code: 'VALIDATION_ERROR', message: 'Port must be a number.' })
  })

  // Probe, not a requirement: a key the caller made up is quoted by the unknown-key rule, in the 400 body and
  // on the failure line. Pinned so the plan's decision (keep, see PLAN.md Q2) is visible if it changes.
  it('an unknown key the caller sent is named in the body and on the failure line (probe)', async () => {
    const r = await post('/api/v1/oauth2/pat', { name: 'n', knowledgeLimit: { 'n5k-unknown-key': 1 } })

    expect(r.status).to.equal(400)
    expect(JSON.parse(r.text).error.message).to.include('n5k-unknown-key')
    const failure = r.lines.find((l) => l.message === 'http.request.error')
    expect(JSON.stringify(failure)).to.include('n5k-unknown-key')
  })

  // The token endpoint answers a schema refusal itself, so its handler names the refusal: the RFC code, and
  // each field and rule, never a value sent.
  it('a refused token request writes one DEBUG oauth.request.refused naming the code, field and rule', async () => {
    const r = await post('/api/v1/oauth2/token', {
      grant_type: 'password',
      client_id: 'n5k-client',
      client_secret: CLIENT_SECRET,
    })

    expect(r.status).to.equal(400)
    expect(handled).to.equal(0)
    expect(JSON.parse(r.text).error).to.equal('unsupported_grant_type')
    expect(levelsAndMessages(r.lines)).to.deep.equal([
      ['debug', 'oauth.request.refused'],
      ['info', 'http.request.end'],
    ])
    expect(r.lines[0].code).to.equal('unsupported_grant_type')
    expect(r.lines[0].metadata.errors).to.deep.equal([{ field: 'body.grant_type', code: 'INVALID_ENUM' }])
    expect(r.lines[1]).to.include({ status: 400, error_class: 'bad_request' })
    const text = JSON.stringify(r.lines)
    expect(text).to.not.include(CLIENT_SECRET)
    expect(text).to.not.include('password')
  })

  it('a token request refused for another field is invalid_request, named by field and rule', async () => {
    const r = await post('/api/v1/oauth2/token', {
      grant_type: 'authorization_code',
      code_verifier: 'n5k-too-short',
    })

    expect(r.status).to.equal(400)
    expect(JSON.parse(r.text).error).to.equal('invalid_request')
    const refused = r.lines.filter((l) => l.message === 'oauth.request.refused')
    expect(refused.map((l) => [l.code, l.metadata.errors])).to.deep.equal([
      ['invalid_request', [{ field: 'body.code_verifier', code: 'INVALID_STRING' }]],
    ])
    expect(JSON.stringify(r.lines)).to.not.include('n5k-too-short')
  })

  it('a token request refused for two fields names both issues on its one line', async () => {
    const r = await post('/api/v1/oauth2/token', { grant_type: 'n5k-nope', code_verifier: 'n5k-too-short' })

    expect(r.status).to.equal(400)
    const refused = r.lines.filter((l) => l.message === 'oauth.request.refused')
    expect(refused.map((l) => [l.code, l.metadata.errors])).to.deep.equal([
      [
        'unsupported_grant_type',
        [
          { field: 'body.grant_type', code: 'INVALID_ENUM' },
          { field: 'body.code_verifier', code: 'INVALID_STRING' },
        ],
      ],
    ])
  })

  // A refused route param: no ERROR line any more, so its value is no longer in a raw `path`. It is still in
  // `params` on http.request.error, as for every failed request (pinned; changing that is a separate decision).
  it('a refused route param writes no ERROR line; its value is only in params on the failure line', async () => {
    const r = await get(`/api/v1/things/${encodeURIComponent(PARAM)}`)

    expect(r.status).to.equal(400)
    expect(handled).to.equal(0)
    expect(levelsAndMessages(r.lines)).to.deep.equal([
      ['warning', 'http.request.error'],
      ['info', 'http.request.end'],
    ])
    const [failure, end] = r.lines
    expect(failure.route).to.equal('/api/v1/things/:thingId')
    expect(failure.metadata.params).to.deep.equal({ thingId: PARAM })
    expect(JSON.stringify(failure.metadata.error)).to.not.include('n5k-fake-param')
    expect(JSON.stringify(end)).to.not.include('n5k-fake-param')
  })

  it('a refused query value is on no line', async () => {
    const r = await get('/api/v1/things?owner=n5k-not-an-email')

    expect(r.status).to.equal(400)
    expect(levelsAndMessages(r.lines)).to.deep.equal([
      ['warning', 'http.request.error'],
      ['info', 'http.request.end'],
    ])
    const errors = r.lines[0].metadata.error.metadata.errors as Array<Record<string, unknown>>
    expect(errors.map((e) => e.field)).to.deep.equal(['query.owner'])
    expect(JSON.stringify(r.lines)).to.not.include('n5k-not-an-email')
  })
})
