import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../src/libs/services/authtoken.service'
import { Logger } from '../../../src/libs/services/logger.service'
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema'
import { Users } from '../../../src/modules/user_management/schema/users.schema'
import { OrgContext, currentOrgContext, orgContextStorage } from '../../../src/libs/context/orgContext'
import { runWithRequestContext } from '../../../src/libs/context/request-context'
import * as seam from '../../../src/libs/services/structured-logging'

const query = (v: unknown) => {
  const q: any = { select: sinon.stub(), sort: sinon.stub(), lean: sinon.stub(), exec: sinon.stub().resolves(v) }
  q.select.returns(q); q.sort.returns(q); q.lean.returns(q)
  return q
}

describe('AuthMiddleware binds the OrgContext', () => {
  let mw: AuthMiddleware
  let tokens: sinon.SinonStubbedInstance<AuthTokenService>

  beforeEach(() => {
    tokens = sinon.createStubInstance(AuthTokenService)
    mw = new AuthMiddleware(sinon.createStubInstance(Logger) as unknown as Logger, tokens as unknown as AuthTokenService, sinon.stub().returns(null))
  })
  afterEach(() => sinon.restore())

  /** Resolves with what the next handler sees, including after an await. */
  const run = (call: (next: (e?: unknown) => void) => Promise<void>) =>
    new Promise<{ before: OrgContext | undefined; after?: OrgContext; err?: unknown }>((resolve) => {
      call(async (err?: unknown) => {
        const before = orgContextStorage.getStore()
        await new Promise((r) => setTimeout(r, 1))
        resolve({ before, after: orgContextStorage.getStore(), err })
      })
    })

  it('a session token binds its own org, never the query or header', async () => {
    sinon.stub(jwt, 'decode').returns({ userId: 'u1', orgId: 'org_a' })
    sinon.stub(Users, 'findOne').returns(query({ _id: 'u1', orgId: 'org_a', isDeleted: false }))
    sinon.stub(UserActivities, 'findOne').returns(query(null))
    tokens.verifyToken.resolves({ userId: 'u1', orgId: 'org_a', role: 'member', iat: Math.floor(Date.now() / 1000) } as any)
    const req: any = {
      headers: { authorization: 'Bearer t', 'x-org-id': 'org_b' }, query: { orgId: 'org_b' }, body: { orgId: 'org_b' },
      get: sinon.stub(),
    }
    const seen = await run((next) => mw.authenticate(req, {} as any, next as any))
    expect(seen.err).to.equal(undefined)
    for (const c of [seen.before, seen.after]) {
      expect(c).to.include({ orgId: 'org_a', userId: 'u1', kind: 'user', source: 'jwt' })
    }
    expect(() => currentOrgContext()).to.throw() // not leaked outside the request
  })

  it('a scoped token binds the token org as a service context', async () => {
    tokens.verifyScopedToken.resolves({ userId: 'svc', orgId: 'org_a', iat: Math.floor(Date.now() / 1000) } as any)
    const req: any = { headers: { authorization: 'Bearer t' } }
    const seen = await run((next) => mw.scopedTokenValidator('mail:send')(req, {} as any, next as any))
    expect(seen.after).to.include({ orgId: 'org_a', kind: 'service', source: 'scoped_jwt' })
  })

  it('a pre-org scoped token (no orgId) stays unbound and fails closed', async () => {
    tokens.verifyScopedToken.resolves({ email: 'a@b.c', iat: Math.floor(Date.now() / 1000) } as any)
    const req: any = { headers: { authorization: 'Bearer t' } }
    const seen = await run((next) => mw.scopedTokenValidator('mail:send')(req, {} as any, next as any))
    expect(seen.before).to.equal(undefined)
  })

  it('interleaved requests keep their own org', async () => {
    tokens.verifyScopedToken.callsFake(async (t: string) => ({ userId: 's', orgId: `org_${t}` }) as any)
    const results = await Promise.all(
      Array.from({ length: 300 }, (_, i) => {
        const req: any = { headers: { authorization: `Bearer ${i % 5}` } }
        return run((next) => mw.scopedTokenValidator('x')(req, {} as any, next as any)).then((s) => s.after?.orgId === `org_${i % 5}`)
      }),
    )
    expect(results.every(Boolean)).to.equal(true)
  })

  it('binds nothing on a failed authentication', async () => {
    tokens.verifyToken.rejects(new Error('bad'))
    const next = sinon.stub()
    await mw.authenticate({ headers: { authorization: 'Bearer t' } } as any, {} as any, next)
    expect(next.firstCall.args[0]).to.be.instanceOf(Error)
    expect(orgContextStorage.getStore()).to.equal(undefined)
  })
})

describe('AuthMiddleware puts the verified org and user on later log lines', () => {
  let mw: AuthMiddleware
  let tokens: sinon.SinonStubbedInstance<AuthTokenService>
  let oauth: { verifyAccessToken: sinon.SinonStub }
  let log: sinon.SinonStub

  beforeEach(() => {
    ;(Logger as any).instance = null
    sinon.stub(seam, 'getProvider').returns(
      Object.assign(new seam.DefaultLoggingProvider(), { fileLoggingEnabled: () => false }),
    )
    log = sinon.stub((Logger.getInstance() as any).logger, 'log')
    tokens = sinon.createStubInstance(AuthTokenService)
    oauth = { verifyAccessToken: sinon.stub() }
    mw = new AuthMiddleware(sinon.createStubInstance(Logger) as unknown as Logger, tokens as unknown as AuthTokenService, () => oauth as any)
  })
  afterEach(() => {
    sinon.restore()
    ;(Logger as any).instance = null
  })

  /** The record of a line the next handler logs, inside a request context as in the app. */
  const lineAfter = (call: (next: (e?: unknown) => void) => Promise<void>) =>
    new Promise<{ record: Record<string, unknown>; err?: unknown }>((resolve) => {
      runWithRequestContext({ rootId: 'req-1' }, () => {
        void call((err?: unknown) => {
          setTimeout(() => {
            Logger.getInstance({ service: 'Handler' }).info('handler line')
            resolve({ record: log.lastCall.args[0], err })
          }, 1)
        })
      })
    })
  const iat = () => Math.floor(Date.now() / 1000)

  it('a session token', async () => {
    sinon.stub(jwt, 'decode').returns({ userId: 'u1', orgId: 'org_a' })
    sinon.stub(Users, 'findOne').returns(query({ _id: 'u1', orgId: 'org_a', isDeleted: false }))
    sinon.stub(UserActivities, 'findOne').returns(query(null))
    tokens.verifyToken.resolves({ userId: 'u1', orgId: 'org_a', role: 'member', email: 'a@b.c', iat: iat() } as any)
    const seen = await lineAfter((next) => mw.authenticate({ headers: { authorization: 'Bearer t' }, get: sinon.stub() } as any, {} as any, next as any))
    expect(seen.err).to.equal(undefined)
    expect(seen.record).to.include({ org_id: 'org_a', user_id: 'u1', service: 'Handler', request_id: 'req-1' })
    expect(JSON.stringify(seen.record)).to.not.contain('a@b.c')
  })

  it('a refused session still says whose it was (the signature is valid)', async () => {
    tokens.verifyToken.resolves({ userId: 'u1', orgId: 'org_a', iat: iat() } as any) // no role: refused
    sinon.stub(jwt, 'decode').returns({ userId: 'u1', orgId: 'org_a' })
    const seen = await lineAfter((next) => mw.authenticate({ headers: { authorization: 'Bearer t' } } as any, {} as any, next as any))
    expect(seen.err).to.be.instanceOf(Error)
    expect(seen.record).to.include({ org_id: 'org_a', user_id: 'u1' })
  })

  it('an invalid signature binds nothing', async () => {
    tokens.verifyToken.rejects(new Error('invalid signature'))
    sinon.stub(jwt, 'decode').returns({ userId: 'u1', orgId: 'org_a' })
    const seen = await lineAfter((next) => mw.authenticate({ headers: { authorization: 'Bearer t' } } as any, {} as any, next as any))
    expect(seen.err).to.be.instanceOf(Error)
    expect(seen.record).to.not.have.any.keys('org_id', 'user_id')
  })

  it('an OAuth access token or PAT', async () => {
    sinon.stub(jwt, 'decode').returns({ tokenType: 'oauth', client_id: 'c1', iss: 'https://example.com' })
    oauth.verifyAccessToken.resolves({ userId: 'u2', orgId: 'org_b', client_id: 'c1', scope: 'kb:read' })
    sinon.stub(Users, 'findOne').returns(query({ email: 'x@y.z', fullName: 'X', role: 'member' }))
    const seen = await lineAfter((next) =>
      mw.authenticate({ headers: { authorization: 'Bearer t' }, baseUrl: '', path: '/api/v1/kb', method: 'GET' } as any, {} as any, next as any),
    )
    expect(seen.err).to.equal(undefined)
    expect(seen.record).to.include({ org_id: 'org_b', user_id: 'u2' })
  })

  it('a scoped service token', async () => {
    tokens.verifyScopedToken.resolves({ userId: 'svc', orgId: 'org_a', iat: iat() } as any)
    const seen = await lineAfter((next) => mw.scopedTokenValidator('mail:send')({ headers: { authorization: 'Bearer t' } } as any, {} as any, next as any))
    expect(seen.record).to.include({ org_id: 'org_a', user_id: 'svc' })
  })

  it('a pre-org scoped token carries no org, and never its email', async () => {
    tokens.verifyScopedToken.resolves({ email: 'a@b.c', iat: iat() } as any)
    const seen = await lineAfter((next) => mw.scopedTokenValidator('mail:send')({ headers: { authorization: 'Bearer t' } } as any, {} as any, next as any))
    expect(seen.record).to.not.have.any.keys('org_id', 'user_id')
    expect(JSON.stringify(seen.record)).to.not.contain('a@b.c')
  })
})
