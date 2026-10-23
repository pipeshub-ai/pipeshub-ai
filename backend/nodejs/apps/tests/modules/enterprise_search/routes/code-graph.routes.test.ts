import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createCodeGraphRouter } from '../../../../src/modules/enterprise_search/routes/code-graph.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function reposStack(): any[] {
  const container = new Container()
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
    authenticate: (_req: any, _res: any, next: any) => next(),
  } as any)
  container.bind<AppConfig>('AppConfig').toConstantValue({ aiBackend: 'http://query:8000' } as any)
  const layer = createCodeGraphRouter(container).stack.find(
    (l: any) => l.route?.path === '/repos' && l.route.methods.get,
  ) as any
  // authenticate, requireScopes, handler
  return layer.route.stack.map((l: any) => l.handle)
}

const reposHandler = () => reposStack()[2]
const reposScopeCheck = () => reposStack()[1]

describe('Code-graph dashboard route', () => {
  let fetchStub: sinon.SinonStub

  beforeEach(() => {
    fetchStub = sinon.stub(globalThis, 'fetch')
  })

  afterEach(() => sinon.restore())

  it("lists repos with the caller's own token", async () => {
    const body = { repos: [{ repo: 'acme/shop', graph_status: 'ready' }] }
    fetchStub.resolves(jsonResponse(200, body))
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub() }
    const next = sinon.spy()

    await reposHandler()({ headers: { authorization: 'Bearer user-token' } }, res, next)

    const [url, init] = fetchStub.firstCall.args
    expect(url).to.equal('http://query:8000/api/v1/code-graph/repos')
    expect(init.headers.authorization).to.equal('Bearer user-token')
    expect(res.json.firstCall.args[0]).to.deep.equal(body)
    expect(next.called).to.equal(false)
  })

  it('passes a query-service error on instead of an empty list', async () => {
    fetchStub.resolves(jsonResponse(503, { error: 'graph unavailable' }))
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub() }
    const next = sinon.spy()

    await reposHandler()({ headers: { authorization: 'Bearer user-token' } }, res, next)

    expect(next.calledOnce).to.equal(true)
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
    expect(res.json.called).to.equal(false)
  })

  it('needs code:read on an OAuth token, not semantic:read', () => {
    const check = reposScopeCheck()
    const oauth = (oauthScopes: string[]) => ({ user: { isOAuth: true, oauthScopes } })

    const refused = sinon.spy()
    check(oauth(['semantic:read', 'connector:read']), {}, refused)
    expect(refused.firstCall.args[0]?.message).to.include('code:read')

    const allowed = sinon.spy()
    check(oauth(['code:read']), {}, allowed)
    expect(allowed.firstCall.args).to.deep.equal([])
  })

  it('lets a signed-in dashboard session through without scopes', () => {
    const next = sinon.spy()
    reposScopeCheck()({ user: { userId: 'u1', orgId: 'o1' } }, {}, next)
    expect(next.firstCall.args).to.deep.equal([])
  })
})
