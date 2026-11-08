import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import http from 'http'
import { gunzipSync } from 'zlib'
import { Container } from 'inversify'
import { createGraphViewRouter } from '../../../../src/modules/enterprise_search/routes/graph-view.routes'
import { createCodeGraphRouter } from '../../../../src/modules/enterprise_search/routes/code-graph.routes'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { xssSanitizationMiddleware } from '../../../../src/libs/middlewares/xss-sanitization.middleware'
import { UnauthorizedError } from '../../../../src/libs/errors/http.errors'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'

type Who = { mode: 'ok'; user: any } | { mode: 'deny' }
let who: Who = { mode: 'ok', user: { userId: 'u1', orgId: 'o1' } }

function build(): express.Express {
  const container = new Container()
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
    authenticate: (req: any, _res: any, next: any) => {
      if (who.mode === 'deny') return next(new UnauthorizedError('No token provided'))
      req.user = who.user
      next()
    },
  } as any)
  container.bind<AppConfig>('AppConfig').toConstantValue({ aiBackend: 'http://query:8000' } as any)
  const app = express()
  app.use(express.json({ limit: '10mb' }))
  app.use(xssSanitizationMiddleware)
  app.use('/api/v1/graph', createGraphViewRouter(container))
  app.use('/api/v1/code-graph', createCodeGraphRouter(container))
  app.use(ErrorMiddleware.handleError())
  return app
}

function call(app: express.Express, path: string, body: unknown, headers: Record<string, string> = {}, raw?: string) {
  return new Promise<{ status: number; headers: http.IncomingHttpHeaders; body: Buffer }>((resolve, reject) => {
    const server = app.listen(0, () => {
      const data = raw ?? JSON.stringify(body)
      const req = http.request(
        { port: (server.address() as any).port, path, method: 'POST', headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data), ...headers } },
        (res) => {
          const chunks: Buffer[] = []
          res.on('data', (c) => chunks.push(c))
          res.on('end', () => {
            server.close()
            resolve({ status: res.statusCode!, headers: res.headers, body: Buffer.concat(chunks) })
          })
        },
      )
      req.on('error', (e) => { server.close(); reject(e) })
      req.end(data)
    })
  })
}

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const BODY = { source: 'code', nodeIds: ['block:9f2c'] }
const text = (r: { body: Buffer }) => r.body.toString()

describe('Graph relay through Express', () => {
  let fetchStub: sinon.SinonStub
  beforeEach(() => {
    who = { mode: 'ok', user: { userId: 'u1', orgId: 'o1' } }
    fetchStub = sinon.stub(globalThis, 'fetch')
  })
  afterEach(() => sinon.restore())

  it('forwards only the schema fields, whatever else the caller puts in the body', async () => {
    fetchStub.resolves(json(200, { nodes: [] }))
    await call(build(), '/api/v1/graph/expand', { ...BODY, orgId: 'other', userId: 'u9', user: { orgId: 'x' }, org_id: 'x', limit: 3 })
    expect(JSON.parse(fetchStub.firstCall.args[1].body)).to.deep.equal({ ...BODY, limit: 3 })
  })

  it('does not forward unknown fields on the code tool relays either', async () => {
    fetchStub.resolves(json(200, { ok: 1 }))
    await call(build(), '/api/v1/code-graph/read', { repo: 'a/b', file_path: 'x.py', orgId: 'other', userId: 'u9', user_id: 'u9' })
    expect(JSON.parse(fetchStub.firstCall.args[1].body)).to.deep.equal({ repo: 'a/b', file_path: 'x.py' })
  })

  it('has no way in without authentication, and never reaches the query service', async () => {
    who = { mode: 'deny' }
    for (const path of ['/api/v1/graph/expand', '/api/v1/graph/revalidate', '/api/v1/code-graph/read', '/api/v1/code-graph/search']) {
      const res = await call(build(), path, path.includes('graph/') && !path.includes('code') ? BODY : { pattern: 'x', repo: 'a/b' })
      expect(res.status, path).to.equal(401)
    }
    expect(fetchStub.called).to.equal(false)
  })

  it('answers 401, 403 and 400 with Cache-Control private, no-store', async () => {
    who = { mode: 'deny' }
    expect((await call(build(), '/api/v1/graph/expand', BODY)).headers['cache-control'], '401').to.equal('private, no-store')
    who = { mode: 'ok', user: { isOAuth: true, oauthScopes: ['semantic:read'], userId: 'u1', orgId: 'o1' } }
    const forbidden = await call(build(), '/api/v1/graph/expand', BODY)
    expect(forbidden.status).to.equal(403)
    expect(forbidden.headers['cache-control'], '403').to.equal('private, no-store')
    who = { mode: 'ok', user: { userId: 'u1', orgId: 'o1' } }
    const invalid = await call(build(), '/api/v1/graph/expand', { ...BODY, limit: 1000 })
    expect(invalid.status).to.equal(400)
    expect(invalid.headers['cache-control'], '400').to.equal('private, no-store')
    expect(fetchStub.called).to.equal(false)
  })

  it('a code:read token passes the scope check and a bad source is a 400', async () => {
    who = { mode: 'ok', user: { isOAuth: true, oauthScopes: ['code:read'], userId: 'u1', orgId: 'o1' } }
    fetchStub.resolves(json(200, { nodes: [] }))
    expect((await call(build(), '/api/v1/graph/expand', BODY)).status).to.equal(200)
    for (const source of ['__proto__', 'constructor', 'toString', 'files']) {
      const res = await call(build(), '/api/v1/graph/expand', { ...BODY, source })
      expect(res.status, source).to.equal(400)
    }
  })

  for (const [status, detail] of [[404, 'No readable node matches the request.'], [400, 'Invalid cursor.'], [422, 'The limit names too much to check.']] as const) {
    it(`keeps a ${status} a ${status} with the service's words, and no-store`, async () => {
      fetchStub.resolves(json(status, { detail }))
      const res = await call(build(), '/api/v1/graph/expand', BODY)
      expect(res.status).to.equal(status)
      expect(text(res)).to.include(detail)
      expect(res.headers['cache-control']).to.equal('private, no-store')
    })
  }

  it('maps the service 503 to a 503 and its 500 to a 500 without its words', async () => {
    fetchStub.resolves(json(503, { detail: 'Graph unavailable.' }))
    const unavailable = await call(build(), '/api/v1/graph/expand', BODY)
    expect(unavailable.status).to.equal(503)
    expect(unavailable.headers['cache-control']).to.equal('private, no-store')
    fetchStub.resolves(json(500, { detail: 'secret internals block:abc' }))
    const failed = await call(build(), '/api/v1/graph/revalidate', BODY)
    expect(failed.status).to.equal(500)
    expect(text(failed)).to.not.include('secret internals')
  })

  it('answers an unreachable service as 503, not as an empty graph', async () => {
    fetchStub.rejects(new TypeError('fetch failed'))
    const res = await call(build(), '/api/v1/graph/expand', BODY)
    expect(res.status).to.equal(503)
  })

  describe('gzip', () => {
    const big = { nodes: Array.from({ length: 300 }, (_, i) => ({ id: `block:${i}`, label: `symbol_${i}` })) }
    const variants: Array<[string | undefined, boolean]> = [
      [undefined, false], ['gzip', true], ['gzip, deflate, br', true], ['*', true], ['identity', false],
      ['gzip;q=0', false], ['br', false], ['deflate', false], ['GZIP', true], ['x-gzip', false], ['gzip;q=0.0, identity', false],
    ]
    for (const [accept, zipped] of variants) {
      it(`Accept-Encoding ${JSON.stringify(accept)} -> ${zipped ? 'gzip' : 'plain'}`, async () => {
        fetchStub.callsFake(async () => json(200, big))
        const res = await call(build(), '/api/v1/graph/expand', BODY, accept === undefined ? {} : { 'accept-encoding': accept })
        expect(res.status).to.equal(200)
        expect(res.headers['cache-control']).to.equal('private, no-store')
        expect(res.headers['vary']).to.match(/Accept-Encoding/i)
        expect(res.headers['content-encoding'] === 'gzip').to.equal(zipped)
        const raw = zipped ? gunzipSync(res.body).toString() : text(res)
        expect(JSON.parse(raw)).to.deep.equal(big)
        expect(res.headers['content-type']).to.match(/application\/json/)
      })
    }
  })

  for (const [status, words] of [[404, 'No indexed repository matches "acme/shop".'], [409, 'More than one repository matches "shop": a/shop, b/shop.']] as const) {
    it(`the code tool relays keep the words of a ${status} the code-graph routes answer as {"error": ...}`, async () => {
      fetchStub.callsFake(async () => json(status, { error: words }))
      for (const [path, body] of [
        ['/api/v1/code-graph/search', { pattern: 'x', repo: 'acme/shop' }],
        ['/api/v1/code-graph/read', { repo: 'acme/shop', file_path: 'a.py' }],
        ['/api/v1/code-graph/outline', { repo: 'acme/shop', file_path: 'a.py' }],
        ['/api/v1/code-graph/semantic-search', { query: 'x', repo: 'acme/shop' }],
      ] as const) {
        const res = await call(build(), path, body)
        expect(res.status, path).to.equal(status)
        expect(JSON.parse(text(res)).error.message, path).to.equal(words)
      }
    })
  }
})
