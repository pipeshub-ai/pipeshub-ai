import 'reflect-metadata'
import express from 'express'
import type { Server } from 'http'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware'
import { requireScopes } from '../../../src/libs/middlewares/require-scopes.middleware'
import { AuthTokenService } from '../../../src/libs/services/authtoken.service'
import { Logger } from '../../../src/libs/services/logger.service'
import { Users } from '../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema'
import { OAuthScopeNames } from '../../../src/libs/enums/oauth-scopes.enum'
import {
  setAgentCallRepository,
  type AgentCallRepository,
  type NewAgentCall,
} from '../../../src/libs/repositories/agent-call-event'

const TOKEN = 'secret-token-value-123'
const EMAIL = 'someone@example.com'

/** Stands in for the store: keeps what was recorded, folding roll-up calls the way the real stores do. */
class FakeRepo implements AgentCallRepository {
  rows: (NewAgentCall & { count: number })[] = []
  fail = false
  hang = false
  async record(call: NewAgentCall): Promise<void> {
    if (this.hang) return new Promise(() => undefined)
    if (this.fail) throw new Error('database is down')
    const same = call.rollup
      ? this.rows.find((r) => r.rollup && r.tokenId === call.tokenId && r.method === call.method && r.route === call.route)
      : undefined
    if (same) same.count += 1
    else this.rows.push({ ...call, count: 1 })
  }
  async list(): Promise<never[]> {
    return []
  }
}

const query = (value: unknown) => {
  const q: any = { select: sinon.stub(), lean: sinon.stub(), sort: sinon.stub(), exec: sinon.stub().resolves(value) }
  q.select.returns(q)
  q.lean.returns(q)
  q.sort.returns(q)
  return q
}

describe('Agent call recorder', () => {
  let server: Server | undefined
  let repo: FakeRepo
  let verifyAccessToken: sinon.SinonStub
  let tokenService: sinon.SinonStubbedInstance<AuthTokenService>
  let warn: sinon.SinonStub

  beforeEach(() => {
    repo = new FakeRepo()
    setAgentCallRepository(repo)
    warn = sinon.stub(Logger.prototype, 'warn')
    verifyAccessToken = sinon.stub()
    tokenService = sinon.createStubInstance(AuthTokenService)
    sinon.stub(Users, 'findOne').returns(query({ email: EMAIL, fullName: 'A', role: 'member' }))
  })

  afterEach(async () => {
    sinon.restore()
    setAgentCallRepository()
    if (server) await new Promise<void>((r) => server!.close(() => r()))
    server = undefined
  })

  const oauthClaims = (extra: Record<string, unknown> = {}) => ({
    userId: 'u1', orgId: 'org1', client_id: 'app-1', jti: 'jti-1', tokenRowId: 'tok-1', scope: 'kb:read', ...extra,
  })

  async function start(): Promise<string> {
    const auth = new AuthMiddleware(
      { debug() {}, info() {}, error() {}, warn() {} } as unknown as Logger,
      tokenService as unknown as AuthTokenService,
      () => ({ verifyAccessToken }) as any,
    )
    const app = express()
    app.use(express.json())
    const kb = express.Router()
    kb.use(auth.authenticate)
    kb.get('/:kbId/records', requireScopes(OAuthScopeNames.KB_READ), (_q, s) => s.json({ ok: true }))
    kb.post('/:kbId/records', requireScopes(OAuthScopeNames.KB_WRITE), (_q, s) => s.status(201).json({}))
    kb.get('/:kbId/boom', (_q, _s, next) => next(new Error('nope')))
    kb.get('/:kbId/moved', (_q, s) => s.redirect(302, '/elsewhere'))
    app.use('/api/v1/knowledgeBase', kb)
    const mcp = express.Router()
    mcp.post('/', auth.authenticate, (_q, s) => s.json({ ok: true }))
    app.use('/mcp', mcp)
    app.use((err: any, _req: any, res: any, _next: any) => res.status(err?.statusCode ?? 500).json({})) // eslint-disable-line @typescript-eslint/no-unused-vars
    await new Promise<void>((r) => { server = app.listen(0, () => r()) })
    return `http://127.0.0.1:${(server!.address() as { port: number }).port}`
  }

  const asBearer = (claims: Record<string, unknown> = oauthClaims()) => {
    sinon.stub(jwt, 'decode').returns({ tokenType: 'oauth', client_id: 'app-1', iss: 'x' })
    verifyAccessToken.resolves(claims)
  }
  const settle = () => new Promise((r) => setTimeout(r, 30))
  const call = (url: string, init: RequestInit = {}) =>
    fetch(url, { ...init, headers: { authorization: `Bearer ${TOKEN}`, 'content-type': 'application/json', 'user-agent': 'curl/8.1 (x86_64)', ...(init.headers as object) } })

  it('writes a row for a bearer call with the route template and no query string', async () => {
    asBearer()
    const base = await start()
    const res = await call(`${base}/api/v1/knowledgeBase/kb42/records?q=confidential%20question`)
    expect(res.status).to.equal(200)
    await settle()
    expect(repo.rows).to.have.length(1)
    expect(repo.rows[0]).to.deep.include({
      orgId: 'org1', userId: 'u1', tokenId: 'tok-1', tokenKind: 'oauth', oauthClientId: 'app-1', userAgent: 'curl/8.1',
      method: 'GET', route: '/api/v1/knowledgeBase/:kbId/records', statusCode: 200, scopeDenied: false, rollup: true,
    })
  })

  it('stores no token value, query text, address, email or body', async () => {
    asBearer()
    const base = await start()
    await call(`${base}/api/v1/knowledgeBase/kb42/records?q=confidential%20question`)
    await call(`${base}/api/v1/knowledgeBase/kb42/records`, { method: 'POST', body: JSON.stringify({ title: 'private body text' }) })
    await call(`${base}/mcp`, { method: 'POST', body: JSON.stringify({ method: 'tools/call', params: { name: 'search', arguments: { query: 'private body text' } } }) })
    await settle()
    expect(repo.rows).to.have.length(3)
    const stored = JSON.stringify(repo.rows)
    for (const secret of [TOKEN, 'confidential', 'private body text', EMAIL, '127.0.0.1', 'kb42', '?']) {
      expect(stored, secret).to.not.contain(secret)
    }
  })

  it('writes nothing for a session call', async () => {
    sinon.stub(jwt, 'decode').returns({ userId: 'u1', orgId: 'org1' })
    tokenService.verifyToken.resolves({ userId: 'u1', orgId: 'org1', role: 'member', iat: Math.floor(Date.now() / 1000) })
    sinon.stub(UserActivities, 'findOne').returns(query(null))
    const base = await start()
    const res = await call(`${base}/api/v1/knowledgeBase/kb42/records`)
    expect(res.status).to.equal(200)
    await settle()
    expect(repo.rows).to.have.length(0)
  })

  it('does not fail or wait for a write that fails or never finishes, and warns once for the failure', async () => {
    asBearer()
    const base = await start()
    repo.fail = true
    expect((await call(`${base}/api/v1/knowledgeBase/kb42/records`)).status).to.equal(200)
    await settle()
    expect(warn.callCount).to.equal(1)
    repo.fail = false
    repo.hang = true
    expect((await call(`${base}/api/v1/knowledgeBase/kb42/records`)).status).to.equal(200)
  })

  it('rolls two successful GETs of one minute into one row of count 2', async () => {
    asBearer()
    const base = await start()
    await call(`${base}/api/v1/knowledgeBase/kb1/records`)
    await call(`${base}/api/v1/knowledgeBase/kb2/records`)
    await settle()
    expect(repo.rows).to.have.length(1)
    expect(repo.rows[0]!.count).to.equal(2)
  })

  it('gives a POST its own row each time', async () => {
    asBearer(oauthClaims({ scope: 'kb:write' }))
    const base = await start()
    await call(`${base}/api/v1/knowledgeBase/kb1/records`, { method: 'POST', body: '{}' })
    await call(`${base}/api/v1/knowledgeBase/kb1/records`, { method: 'POST', body: '{}' })
    await settle()
    expect(repo.rows.map((r) => [r.method, r.statusCode, r.rollup])).to.deep.equal([['POST', 201, false], ['POST', 201, false]])
  })

  it('gives a redirected GET its own row: only 2xx GETs are folded', async () => {
    asBearer()
    const base = await start()
    await call(`${base}/api/v1/knowledgeBase/kb1/moved`, { redirect: 'manual' })
    await settle()
    expect(repo.rows[0]).to.deep.include({ route: '/api/v1/knowledgeBase/:kbId/moved', statusCode: 302, rollup: false })
  })

  it('gives a scope denial its own row, flagged', async () => {
    asBearer() // kb:read only
    const base = await start()
    const res = await call(`${base}/api/v1/knowledgeBase/kb1/records`, { method: 'POST', body: '{}' })
    expect(res.status).to.equal(403)
    await settle()
    expect(repo.rows).to.have.length(1)
    expect(repo.rows[0]).to.deep.include({ statusCode: 403, scopeDenied: true, rollup: false, route: '/api/v1/knowledgeBase/:kbId/records' })
  })

  it('keeps the full route template when the handler fails, and records the failure as its own row', async () => {
    asBearer()
    const base = await start()
    await call(`${base}/api/v1/knowledgeBase/kb1/boom`)
    await settle()
    expect(repo.rows[0]).to.deep.include({ route: '/api/v1/knowledgeBase/:kbId/boom', statusCode: 500, rollup: false })
  })

  it('records an MCP tool call by name, one row each', async () => {
    asBearer()
    const base = await start()
    const body = JSON.stringify({ jsonrpc: '2.0', method: 'tools/call', params: { name: 'search', arguments: { query: 'x' } } })
    await call(`${base}/mcp`, { method: 'POST', body })
    await call(`${base}/mcp`, { method: 'POST', body })
    await settle()
    expect(repo.rows).to.have.length(2)
    expect(repo.rows[0]).to.deep.include({ route: '/mcp', mcpTool: 'search', rollup: false })
  })

  it('classifies a personal access token and a service token without exposing the placeholder client', async () => {
    asBearer(oauthClaims({ client_id: 'pat-system:org1' }))
    const base = await start()
    await call(`${base}/api/v1/knowledgeBase/kb1/records`)
    verifyAccessToken.resolves(oauthClaims({ jti: 'jti-2', tokenRowId: 'tok-2', client_id: 'svc-system:org1' }))
    await call(`${base}/api/v1/knowledgeBase/kb1/records`)
    await settle()
    expect(repo.rows.map((r) => [r.tokenKind, r.oauthClientId])).to.deep.equal([['pat', undefined], ['delegated', undefined]])
  })
})
