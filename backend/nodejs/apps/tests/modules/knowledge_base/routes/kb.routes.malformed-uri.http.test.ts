import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import sinon from 'sinon'
import winston from 'winston'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { INVALID_PATH_SEGMENT_MESSAGE } from '../../../../src/libs/middlewares/safe-path-params.middleware'
import { requestContextMiddleware } from '../../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../../src/libs/middlewares/access-log.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { LogRecord, captureLogRecords, newRequestId, recordsOf } from '../../../helpers/log-capture'
import { KB_ID, KbHarness, MEMBER, sessionToken, startKbHarness } from './kb-http-harness'

// A path with a malformed percent-encoding makes Express throw `URIError: Failed to decode param '<raw>'`
// (status 400) while matching a route, before any handler runs. It is the caller's URL: 400, a fixed
// sentence, one WARN bad_request line, and the raw value on no line.
const MESSAGE = Symbol.for('message')
const FAKE_EMAIL = 'n5nc-fake-ada%40example.com'
const BAD_PATH_ANSWER = { code: 'HTTP_BAD_REQUEST', message: INVALID_PATH_SEGMENT_MESSAGE }

describe('a path Express cannot decode, over HTTP', () => {
  let records: LogRecord[]
  let saved: unknown

  beforeEach(() => {
    saved = (ErrorMiddleware as any).logger
    ;(ErrorMiddleware as any).logger = Logger.getInstance({ service: 'ErrorMiddleware' })
    records = captureLogRecords()
  })
  afterEach(() => {
    ;(ErrorMiddleware as any).logger = saved
    // Keep the kb harness's own stubs (Users.findOne): restore only the capture stubs.
    ;(winston.Logger.prototype.log as any).restore?.()
    ;(winston.Logger.prototype.isLevelEnabled as any).restore?.()
    delete process.env.LOG_FORMAT
    Logger.applyEnv()
  })

  const root = () => (Logger.getInstance() as unknown as { logger: winston.Logger }).logger
  const rendered = (record: LogRecord, format: 'text' | 'json'): string => {
    process.env.LOG_FORMAT = format
    Logger.applyEnv()
    const consoleT = root().transports.find((t) => t instanceof winston.transports.Console)!
    const shaped = root().format.transform({ ...record } as winston.Logform.TransformableInfo, {})
    return String(
      (consoleT.format!.transform(shaped as winston.Logform.TransformableInfo, {}) as Record<symbol, unknown>)[MESSAGE],
    )
  }
  const carriers = (lines: LogRecord[], raw: string) =>
    lines.filter((l) => (['text', 'json'] as const).some((f) => rendered(l, f).includes(raw))).map((l) => String(l.message))

  // Raw http.request: a URL client would refuse or re-encode the malformed `%`.
  const send = async (
    origin: string,
    method: string,
    rawPath: string,
    opts: { token?: string; body?: string; awaitAccessLine?: boolean } = {},
  ) => {
    const requestId = newRequestId('n5nc')
    const answer = await new Promise<{ status: number; text: string }>((resolve, reject) => {
      const req = http.request(
        {
          host: '127.0.0.1',
          port: Number(new URL(origin).port),
          method,
          path: rawPath,
          headers: {
            'x-request-id': requestId,
            ...(opts.token ? { authorization: `Bearer ${opts.token}` } : {}),
            ...(opts.body !== undefined
              ? { 'content-type': 'application/json', 'content-length': String(Buffer.byteLength(opts.body)) }
              : {}),
          },
        },
        (res) => {
          let text = ''
          res.setEncoding('utf8')
          res.on('data', (c: string) => (text += c))
          res.on('end', () => resolve({ status: res.statusCode ?? 0, text }))
        },
      )
      req.on('error', reject)
      req.end(opts.body)
    })
    // The access line is written on 'close', after the client may have the answer.
    const deadline = Date.now() + 5000
    while (
      opts.awaitAccessLine !== false &&
      Date.now() < deadline &&
      !recordsOf(records, requestId).some((r) => r.message === 'http.request.end')
    ) {
      await new Promise((resolve) => setImmediate(resolve))
    }
    const lines = recordsOf(records, requestId)
    return {
      ...answer,
      lines,
      errorLine: lines.find((r) => r.message === 'http.request.error'),
      endLine: lines.find((r) => r.message === 'http.request.end'),
    }
  }

  describe('a knowledge-base route param', () => {
    let h: KbHarness

    before(async () => {
      h = await startKbHarness()
    })
    after(async () => {
      sinon.restore()
      await h.close()
    })

    const kb = (path: string, opts: { token?: string; body?: string } = {}) =>
      send(h.origin, opts.body === undefined ? 'GET' : 'POST', `/api/v1/knowledgeBase${path}`, opts)

    for (const [name, path, signedIn] of [
      ['unauthenticated GET /%E0%A4%A (truncated UTF-8)', '/%E0%A4%A', false],
      ['signed-in GET /%ZZ', '/%ZZ', true],
      ['signed-in GET /<email>%ZZ (a value a person typed)', `/${FAKE_EMAIL}%ZZ`, true],
      ['signed-in GET /:kbId/folder/%E0 (deeper param)', `/${KB_ID}/folder/%E0`, true],
    ] as const) {
      it(`${name}: 400, fixed message, WARN bad_request, raw segment on no line`, async () => {
        const raw = path.split('/').pop() as string
        const r = await kb(path, { token: signedIn ? sessionToken(h, MEMBER) : undefined })
        expect(r.status, 'client status').to.equal(400)
        expect(JSON.parse(r.text).error, 'body').to.include(BAD_PATH_ANSWER)
        expect(r.text, 'body').to.not.include(raw)
        expect(r.errorLine, 'error line').to.include({
          level: 'warning',
          status: 400,
          error_class: 'bad_request',
          error_type: 'BadRequestError',
        })
        expect(r.endLine, 'access line').to.include({ status: 400, error_class: 'bad_request' })
        expect(carriers(r.lines, raw), 'lines carrying the raw segment').to.deep.equal([])
      })
    }

    // The body-parser rejections this mirrors keep their shape.
    it('a malformed JSON body stays 400 bad_request WARN with a fixed message', async () => {
      const r = await kb('/', { token: sessionToken(h, MEMBER), body: '{"kbName": n5nc-secret}' })
      expect(r.status).to.equal(400)
      expect(JSON.parse(r.text).error.message).to.equal('The request body is not valid JSON.')
      expect(r.errorLine).to.include({ level: 'warning', error_class: 'bad_request' })
      expect(carriers(r.lines, 'n5nc-secret')).to.deep.equal([])
    })

    it('a too-large JSON body stays 413 bad_request WARN with a fixed message', async () => {
      const r = await kb('/', { token: sessionToken(h, MEMBER), body: JSON.stringify({ kbName: 'x'.repeat(200_000) }) })
      expect(r.status).to.equal(413)
      expect(JSON.parse(r.text).error.message).to.equal('The request body is too large.')
      expect(r.errorLine).to.include({ level: 'warning', error_class: 'bad_request' })
    })
  })

  // app.ts's SPA fallback `app.get('*')` captures the whole path as a param, so any page URL (and any
  // /api/v1 GET no router matches) with a bad `%` throws the same error, quoting the whole path.
  describe('the SPA fallback route', () => {
    let server: http.Server
    let origin: string

    before(async () => {
      const app = express()
      app.use(requestContextMiddleware)
      app.use(accessLog())
      app.get('*', (_req, res) => {
        res.send('shell')
      })
      app.use(ErrorMiddleware.handleError())
      await new Promise<void>((resolve) => {
        server = app.listen(0, '127.0.0.1', resolve)
      })
      origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    })
    after(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    })

    it('GET /record/<email>%ZZ: 400, fixed message, WARN bad_request, raw path on no line', async () => {
      const raw = `${FAKE_EMAIL}%ZZ`
      // A page path writes no access line; the error line is written before the answer.
      const r = await send(origin, 'GET', `/record/${raw}`, { awaitAccessLine: false })
      expect(r.status, 'client status').to.equal(400)
      expect(JSON.parse(r.text).error, 'body').to.include(BAD_PATH_ANSWER)
      expect(r.text, 'body').to.not.include(raw)
      expect(r.errorLine, 'error line').to.include({
        level: 'warning',
        status: 400,
        error_class: 'bad_request',
        error_type: 'BadRequestError',
      })
      expect(carriers(r.lines, raw), 'lines carrying the raw path').to.deep.equal([])
    })
  })
})
