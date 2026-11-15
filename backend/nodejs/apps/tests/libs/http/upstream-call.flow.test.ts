import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import http from 'node:http'
import { AddressInfo } from 'node:net'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { Logger } from '../../../src/libs/services/logger.service'
import { setContextIdentity } from '../../../src/libs/context/request-context'
import { HttpMethod } from '../../../src/libs/enums/http-methods.enum'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'
import { getConnectorStats } from '../../../src/modules/tokens_manager/controllers/connector.controllers'
import { startAIStream } from '../../../src/modules/enterprise_search/controller/es_controller'

const ORG = '65f0a1b2c3d4e5f6a7b8c9d0'
const USER = '65f0a1b2c3d4e5f6a7b8c9d1'
const BODY_SECRET = 'alice@example.com'
const CONNECTOR_ID = '65f0a1b2c3d4e5f6a7b8c9d2'

type Logged = { level: string; message: string; meta: Record<string, any> }

/**
 * The real request chain (context, access line, error middleware), the real getConnectorStats controller
 * and startAIStream, against a local fake service: what one request logs about its upstream call.
 */
describe('upstream-call: what a request logs about its upstream call', () => {
  let logged: Logged[]
  let upstream: http.Server
  let gateway: http.Server
  let gatewayBase: string
  let mode: 'ok' | 'fail' | 'drop'
  let receivedIds: Array<string | undefined>

  // In the Redis-cluster job another suite leaves the outbox dispatcher running; its ERRORs are not this request's.
  const BACKGROUND_LINES = new Set(['Outbox dispatcher pass failed'])
  const atLeast = (levels: string[]): Logged[] =>
    logged.filter((l) => levels.includes(l.level) && !BACKGROUND_LINES.has(l.message))
  const callLines = (): Logged[] => logged.filter((l) => l.message === 'upstream.call.end')
  const accessLine = async (): Promise<Logged> => {
    for (let i = 0; i < 200; i += 1) {
      const line = logged.find((l) => l.message === 'http.request.end')
      if (line) return line
      await new Promise((resolve) => setTimeout(resolve, 10))
    }
    throw new Error('no access line')
  }
  const request = (method: string, path: string, requestId?: string): Promise<number> =>
    new Promise((resolve, reject) => {
      const req = http.request(
        `${gatewayBase}${path}`,
        { method, agent: false, headers: requestId ? { 'x-request-id': requestId } : {} },
        (res) => {
          res.resume()
          res.on('end', () => resolve(res.statusCode ?? 0))
        },
      )
      req.on('error', reject)
      req.end()
    })

  before(async () => {
    upstream = http.createServer((req, res) => {
      receivedIds.push(req.headers['x-request-id'] as string | undefined)
      if ((req.url ?? '').startsWith('/query/api/v1/chat/stream')) {
        res.writeHead(200, { 'content-type': 'text/event-stream' })
        res.write('data: {"i":0}\n\n')
        setTimeout(() => res.end('data: {"i":1}\n\n'), 20)
        return
      }
      if (mode === 'drop') {
        req.socket.destroy()
        return
      }
      const status = mode === 'fail' ? 500 : 200
      res.writeHead(status, { 'content-type': 'application/json' })
      res.end(JSON.stringify(mode === 'fail' ? { detail: `graph query failed for ${BODY_SECRET}` } : { ok: true }))
    })
    await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve))
    const upstreamOrigin = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`

    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    // Stands in for the auth middleware.
    app.use((req: any, _res, next) => {
      req.user = { userId: USER, orgId: ORG }
      req.headers.authorization = 'Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXNlY3JldA'
      setContextIdentity({ orgId: ORG, userId: USER })
      next()
    })
    app.get('/api/v1/connectors/:connectorId/stats', (req: any, res, next) =>
      getConnectorStats({ connectorBackend: `${upstreamOrigin}/connector` } as any)(req, res, next),
    )
    app.post('/api/v1/conversations/stream', async (req, res, next) => {
      try {
        const stream = await startAIStream(
          { uri: `${upstreamOrigin}/query/api/v1/chat/stream`, method: HttpMethod.POST, headers: req.headers as any, body: {} },
          'Chat Stream',
        )
        stream!.on('error', next)
        stream!.pipe(res)
      } catch (error) {
        next(error)
      }
    })
    app.use(ErrorMiddleware.handleError())
    gateway = http.createServer((req, res) => {
      void app(req, res)
    })
    await new Promise<void>((resolve) => gateway.listen(0, '127.0.0.1', resolve))
    gatewayBase = `http://127.0.0.1:${(gateway.address() as AddressInfo).port}`
    registerUpstreams({ connector: [`${upstreamOrigin}/connector`], query: [`${upstreamOrigin}/query`] })
  })

  after(async () => {
    registerUpstreams({})
    for (const server of [gateway, upstream]) {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })

  beforeEach(() => {
    logged = []
    receivedIds = []
    // Every logger, whatever root it was created from: module loggers keep the instance they got at load.
    sinon.stub(Logger.prototype as any, 'logWithLevel').callsFake((level: unknown, message: unknown, meta: unknown) => {
      logged.push({ level: String(level), message: String(message), meta: (meta ?? {}) as Record<string, any> })
    })
  })

  afterEach(() => sinon.restore())

  it("an upstream 500 is one WARN call line and reads upstream_5xx on the request's lines", async () => {
    mode = 'fail'
    expect(await request('GET', `/api/v1/connectors/${CONNECTOR_ID}/stats`)).to.equal(500)
    const access = await accessLine()
    expect(callLines().map((l) => [l.level, l.meta.upstream, l.meta.route, l.meta.status, l.meta.error_class])).to.deep.equal([
      ['warning', 'connector', '/connector/api/v1/stats', 500, 'upstream_5xx'],
    ])
    const middleware = logged.filter((l) => l.message === 'http.request.error')
    expect(middleware.map((l) => [l.level, l.meta.error_class])).to.deep.equal([['warning', 'upstream_5xx']])
    expect([access.level, access.meta.error_class]).to.deep.equal(['warning', 'upstream_5xx'])
    // The controller's own catch-and-log line is N5's; it is the only ERROR left.
    expect(atLeast(['error']).map((l) => l.message)).to.deep.equal(['Error getting connector stats'])
    expect(JSON.stringify(logged.map((l) => [l.message, l.meta]))).to.not.include(BODY_SECRET)
  })

  it('a service that drops the connection is one WARN call line per attempt, and no ERROR of the command', async () => {
    mode = 'drop'
    expect(await request('GET', `/api/v1/connectors/${CONNECTOR_ID}/stats`)).to.equal(503)
    await accessLine()
    expect(callLines().map((l) => [l.level, l.meta.attempt, l.meta.error_class, l.meta.network_code])).to.deep.equal([
      ['warning', 1, 'unavailable', 'UND_ERR_SOCKET'],
      ['warning', 2, 'unavailable', 'UND_ERR_SOCKET'],
      ['warning', 3, 'unavailable', 'UND_ERR_SOCKET'],
    ])
    expect(atLeast(['error']).map((l) => l.message)).to.deep.equal(['Error getting connector stats'])
  })

  it('a 200 and a chat stream log exactly their access line at INFO and above', async () => {
    mode = 'ok'
    expect(await request('GET', `/api/v1/connectors/${CONNECTOR_ID}/stats`)).to.equal(200)
    await accessLine()
    expect(atLeast(['info', 'warning', 'error']).map((l) => l.message)).to.deep.equal(['http.request.end'])
    expect(callLines().map((l) => [l.level, l.meta.status])).to.deep.equal([['debug', 200]])

    logged = []
    expect(await request('POST', '/api/v1/conversations/stream')).to.equal(200)
    await accessLine()
    expect(atLeast(['info', 'warning', 'error']).map((l) => l.message)).to.deep.equal(['http.request.end'])
    expect(callLines().map((l) => [l.level, l.meta.upstream, l.meta.status, l.meta.outcome])).to.deep.equal([
      ['debug', 'query', 200, 'success'],
    ])
  })

  it("the service gets the gateway's sanitised request id, not the client's raw one", async () => {
    mode = 'ok'
    await request('GET', `/api/v1/connectors/${CONNECTOR_ID}/stats`, 'rq bad!')
    await accessLine()
    expect(receivedIds).to.deep.equal(['rqbad'])
  })
})
