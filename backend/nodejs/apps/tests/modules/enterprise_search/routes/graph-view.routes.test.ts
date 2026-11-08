import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { gunzipSync } from 'zlib'
import { Container } from 'inversify'
import { createGraphViewRouter } from '../../../../src/modules/enterprise_search/routes/graph-view.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const BODY = { source: 'code', nodeIds: ['block:9f2c'] }

/** noStore, authenticate, validate, source scope, handler */
function stack(path: string): any[] {
  const container = new Container()
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
    authenticate: (_req: any, _res: any, next: any) => next(),
  } as any)
  container.bind<AppConfig>('AppConfig').toConstantValue({ aiBackend: 'http://query:8000' } as any)
  const layer = createGraphViewRouter(container).stack.find(
    (l: any) => l.route?.path === path && l.route.methods.post,
  ) as any
  return layer.route.stack.map((l: any) => l.handle)
}

function fakeRes(): any {
  const res: any = { headers: {} as Record<string, unknown>, sent: undefined as unknown }
  res.status = sinon.stub().returnsThis()
  res.type = sinon.stub().returnsThis()
  res.setHeader = (k: string, v: unknown) => {
    res.headers[k] = v
    return res
  }
  res.send = (b: unknown) => {
    res.sent = b
    return res
  }
  return res
}

const request = (body: unknown, acceptsGzip = false): any => ({
  headers: { authorization: 'Bearer user-token' },
  body,
  acceptsEncodings: (...enc: string[]) => (acceptsGzip && enc.includes('gzip') ? 'gzip' : false),
})

const validate = (path: string, body: unknown) =>
  new Promise<unknown>((resolve) => {
    const req: any = { body, query: {}, params: {}, headers: {} }
    stack(path)[2](req, {}, (err?: unknown) => resolve(err))
  })

describe('Graph view routes', () => {
  let fetchStub: sinon.SinonStub

  beforeEach(() => {
    fetchStub = sinon.stub(globalThis, 'fetch')
  })

  afterEach(() => sinon.restore())

  for (const path of ['/expand', '/resolve', '/revalidate']) {
    it(`${path} marks the answer private and no-store before anything can fail`, () => {
      const res = fakeRes()
      const next = sinon.spy()
      stack(path)[0]({}, res, next)
      expect(res.headers['Cache-Control']).to.equal('private, no-store')
      expect(next.calledOnce).to.equal(true)
    })

    it(`${path} needs the source's scope, code:read, on an OAuth token and nothing new`, () => {
      const check = stack(path)[3]
      const as = (oauthScopes: string[]) => ({ body: { source: 'code' }, user: { isOAuth: true, oauthScopes } })

      const refused = sinon.spy()
      check(as(['semantic:read', 'connector:read']), {}, refused)
      expect(refused.firstCall.args[0]?.message).to.include('code:read')

      const allowed = sinon.spy()
      check(as(['code:read']), {}, allowed)
      expect(allowed.firstCall.args).to.deep.equal([])

      const session = sinon.spy()
      check({ body: { source: 'code' }, user: { userId: 'u1', orgId: 'o1' } }, {}, session)
      expect(session.firstCall.args).to.deep.equal([])
    })
  }

  it('relays an expand with the same body and the callers own token, and nothing else', async () => {
    const answer = { nodes: [], edges: [], anchors: [], page: { truncated: false, incomplete: false }, graphStatus: 'ready' }
    fetchStub.resolves(jsonResponse(200, answer))
    const res = fakeRes()
    const next = sinon.spy()

    await stack('/expand')[4](request({ ...BODY, limit: 5 }), res, next)

    const [url, init] = fetchStub.firstCall.args
    expect(url).to.equal('http://query:8000/api/v1/graph/expand')
    expect(init.method).to.equal('POST')
    expect(init.headers.authorization).to.equal('Bearer user-token')
    expect(JSON.parse(init.body)).to.deep.equal({ ...BODY, limit: 5 })
    expect(JSON.parse(res.sent)).to.deep.equal(answer)
    expect(res.headers['Content-Encoding']).to.equal(undefined)
    expect(next.called).to.equal(false)
  })

  it('gzips a large body for a client that accepts it, with Node zlib', async () => {
    const answer = { nodes: Array.from({ length: 400 }, (_, i) => ({ id: `block:${i}`, label: `symbol_${i}` })) }
    fetchStub.callsFake(async () => jsonResponse(200, answer))

    const zipped = fakeRes()
    await stack('/expand')[4](request(BODY, true), zipped, sinon.spy())
    expect(zipped.headers['Content-Encoding']).to.equal('gzip')
    expect(zipped.headers['Vary']).to.equal('Accept-Encoding')
    expect(JSON.parse(gunzipSync(zipped.sent).toString())).to.deep.equal(answer)
    expect(zipped.sent.length).to.be.lessThan(JSON.stringify(answer).length)

    const plain = fakeRes()
    await stack('/expand')[4](request(BODY, false), plain, sinon.spy())
    expect(plain.headers['Content-Encoding']).to.equal(undefined)
    expect(JSON.parse(plain.sent)).to.deep.equal(answer)
  })

  it('does not gzip a small body', async () => {
    fetchStub.resolves(jsonResponse(200, { visible: ['block:1'] }))
    const res = fakeRes()
    await stack('/revalidate')[4](request(BODY, true), res, sinon.spy())
    expect(res.headers['Content-Encoding']).to.equal(undefined)
    expect(JSON.parse(res.sent)).to.deep.equal({ visible: ['block:1'] })
  })

  for (const [status, body] of [
    [404, { detail: 'No readable node matches the request.' }],
    [400, { detail: 'Invalid cursor.' }],
    [503, { detail: 'Graph unavailable.' }],
  ] as const) {
    it(`passes a ${status} on as an error and never as a graph`, async () => {
      fetchStub.resolves(jsonResponse(status, body))
      const res = fakeRes()
      const next = sinon.spy()

      await stack('/expand')[4](request(BODY), res, next)

      expect(next.calledOnce).to.equal(true)
      expect(next.firstCall.args[0]).to.be.instanceOf(Error)
      expect(next.firstCall.args[0].statusCode).to.equal(status)
      expect(res.sent).to.equal(undefined)
    })
  }

  it('answers an unreachable query service as unavailable, not as an empty graph', async () => {
    fetchStub.rejects(new TypeError('fetch failed'))
    const next = sinon.spy()
    await stack('/expand')[4](request(BODY), fakeRes(), next)
    expect(next.firstCall.args[0].statusCode).to.equal(503)
  })

  it('refuses what the query service would refuse, before relaying it', async () => {
    const tooMany = { source: 'code', nodeIds: Array.from({ length: 21 }, (_, i) => `block:${i}`) }
    const cases: Array<[string, unknown]> = [
      ['/expand', tooMany],
      ['/expand', { ...BODY, limit: 101 }],
      ['/expand', { ...BODY, limit: 0 }],
      ['/expand', { ...BODY, nodeIds: [] }],
      ['/expand', { ...BODY, source: 'files' }],
      ['/expand', { ...BODY, direction: 'sideways' }],
      ['/expand', { ...BODY, cursor: 'x'.repeat(257) }],
      ['/expand', { source: 'code' }],
      ['/revalidate', { ...BODY, nodeIds: Array.from({ length: 5001 }, (_, i) => `block:${i}`) }],
      ['/revalidate', { source: 'code', nodeIds: [''] }],
      ['/resolve', { source: 'code' }],
      ['/resolve', { source: 'code', repo: 'acme/shop' }],
      ['/resolve', { source: 'code', filePath: 'a.py' }],
      ['/resolve', { source: 'files', recordId: 'r1' }],
    ]
    for (const [path, body] of cases) {
      expect(await validate(path, body), `${path} ${JSON.stringify(body).slice(0, 60)}`).to.be.instanceOf(Error)
    }
    expect(await validate('/revalidate', { source: 'code', nodeIds: Array.from({ length: 5000 }, (_, i) => `block:${i}`) })).to.equal(undefined)
    expect(await validate('/resolve', { source: 'code', recordId: 'r1', qualifiedName: 'function:x' })).to.equal(undefined)
    expect(await validate('/resolve', { source: 'code', repo: 'acme/shop', filePath: 'a.py' })).to.equal(undefined)
    expect(await validate('/expand', { ...BODY, limit: 100, cursor: 'djE6MQ', includeTests: true })).to.equal(undefined)
  })

  it('drops fields the query service does not take instead of forwarding them', async () => {
    const req: any = { body: { ...BODY, orgId: 'someone-elses', userId: 'u9' }, query: {}, params: {}, headers: {} }
    await new Promise<void>((resolve) => stack('/expand')[2](req, {}, () => resolve()))
    expect(req.body).to.deep.equal(BODY)
  })
})
