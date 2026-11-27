/**
 * A store read that fails while the auth middleware checks a client_credentials token is no verdict
 * on the token: the store's own error reaches the error middleware, never a 401.
 *
 * Real Express app, real AuthMiddleware, real ErrorMiddleware, real /mcp router (origin guard,
 * rejected-token limiter, WWW-Authenticate handler). Only the stores are faked, through the
 * repositories' own test setters, and only the OAuth token service's verification is scripted.
 * The error shapes are the ones the Mongo driver, Mongoose and the PostgreSQL store layer throw.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express, { Request, Response } from 'express'
import jwt from 'jsonwebtoken'
import { AddressInfo } from 'net'
import { Container } from 'inversify'
import { MongoNetworkError, MongoServerSelectionError } from 'mongodb'
import mongoose from 'mongoose'
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../src/libs/services/authtoken.service'
import { Logger } from '../../../src/libs/services/logger.service'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { createMCPRouter } from '../../../src/modules/mcp/routes/mcp.routes'
import { OAuthAuthMiddleware } from '../../../src/modules/oauth_provider/middlewares/oauth.auth.middleware'
import { NO_APP_CREATOR, setOAuthAppRepository } from '../../../src/libs/repositories/oauth-app'
import { setOrgRepository } from '../../../src/libs/repositories/org'
import { setUserRepository } from '../../../src/libs/repositories/user'
import { mapPgStoreError } from '../../../src/libs/repositories/pg'
import { ConnectionLost, DbReadOnly, DbTimeout } from '../../../src/libs/db/errors'
import { ExpiredTokenError } from '../../../src/libs/errors/oauth.errors'
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../src/libs/errors/backend-error'
import { AUTH_ATTEMPTS_METRIC } from '../../../src/libs/services/telemetry/modules/auth-metrics'
import { sampleValue } from '../../helpers/metric-samples'

const ISSUER = 'https://pipeshub.test'
const ORG = '65a000000000000000000001'
const APP = 'app-client-1'
const OWNER = '65a0000000000000000000aa'

/** A JWT the middleware's peek reads as an OAuth token; its verification is the scripted service below. */
const oauthBearer = `Bearer ${jwt.sign({ tokenType: 'oauth', client_id: APP, iss: ISSUER }, 'peek-only')}`

/** A client_credentials token (userId === client_id), as verifyAccessToken returns it. */
const clientCredentials = (extra: Record<string, unknown> = {}) => ({
  userId: APP, orgId: ORG, client_id: APP, scope: 'kb:read mcp:use', ...extra,
})

const socketError = (code: string, message: string) => Object.assign(new Error(message), { code })

/** What a query throws while Mongo is down: server selection gives up, the refusal two causes deep. */
const mongoDown = () =>
  new MongoServerSelectionError('connect ECONNREFUSED 10.0.0.5:27017', {
    error: new MongoNetworkError('connect ECONNREFUSED 10.0.0.5:27017', {
      cause: socketError('ECONNREFUSED', 'connect ECONNREFUSED 10.0.0.5:27017'),
    }),
  } as never)

interface Failure {
  name: string
  error: () => unknown
  status: number
  code: string
  message?: string
  retryAfter?: string
}

/** Each failure a lookup can see after the token read worked, and what the error middleware answers for it. */
const FAILURES: Failure[] = [
  { name: 'Mongo down (server selection fails)', error: mongoDown, status: 500, code: 'INTERNAL_ERROR' },
  {
    name: 'Mongoose buffering timed out',
    error: () => new mongoose.Error('Operation `oauthapps.findOne()` buffering timed out after 10000ms'),
    status: 500,
    code: 'INTERNAL_ERROR',
  },
  {
    name: 'Mongo connection dropped mid-query',
    error: () =>
      new MongoNetworkError('connection 3 to 10.0.0.5:27017 closed', {
        cause: socketError('ECONNRESET', 'read ECONNRESET'),
      }),
    status: 503,
    code: 'HTTP_SERVICE_UNAVAILABLE',
    message: SERVICE_UNAVAILABLE_MESSAGE,
  },
  {
    name: 'PostgreSQL connection lost',
    error: () => mapPgStoreError(new ConnectionLost(false, socketError('ECONNREFUSED', 'connect ECONNREFUSED'))),
    status: 503,
    code: 'HTTP_SERVICE_UNAVAILABLE',
    message: 'Database is unavailable',
  },
  {
    name: 'PostgreSQL statement timeout',
    error: () => mapPgStoreError(new DbTimeout()),
    status: 504,
    code: 'HTTP_GATEWAY_TIMEOUT',
    message: 'Database timed out',
  },
  {
    name: 'a typed store error keeps its status and Retry-After',
    error: () => mapPgStoreError(new DbReadOnly()),
    status: 503,
    code: 'HTTP_SERVICE_UNAVAILABLE',
    message: 'The document store is read-only for a moment',
    retryAfter: '5',
  },
]
const byName = (name: string): Failure => FAILURES.find((f) => f.name === name)!
const MONGO_DOWN = byName('Mongo down (server selection fails)')
const MONGO_DROPPED = byName('Mongo connection dropped mid-query')
const PG_LOST = byName('PostgreSQL connection lost')
const TYPED = byName('a typed store error keeps its status and Retry-After')

const SITES = ['app', 'org'] as const
type Site = (typeof SITES)[number]
const SITE_TITLE: Record<Site, string> = {
  app: 'the OAuth app lookup',
  org: 'the org lookup (token without accountType)',
}

interface Answer { status: number; body: any; wwwAuthenticate: string | null; retryAfter: string | null }

describe('AuthMiddleware: a store read that fails while checking a token is not a 401', () => {
  let logger: sinon.SinonStubbedInstance<Logger>
  let verifyAccessToken: sinon.SinonStub
  let oauthConfigured: boolean
  let findLiveIdentity: sinon.SinonStub
  let getAccountType: sinon.SinonStub
  let authMiddleware: AuthMiddleware
  const tokenService = new AuthTokenService('outage-jwt-secret-0123456789abcdef', 'outage-scoped-secret-0123456789abc')

  const lookup = (site: Site): sinon.SinonStub => (site === 'app' ? findLiveIdentity : getAccountType)

  beforeEach(() => {
    logger = sinon.createStubInstance(Logger)
    verifyAccessToken = sinon.stub().resolves(clientCredentials())
    oauthConfigured = true
    findLiveIdentity = sinon.stub().resolves({ createdBy: OWNER })
    getAccountType = sinon.stub().resolves({ accountType: 'enterprise' })
    setOAuthAppRepository({ findLiveIdentity } as any)
    setOrgRepository({ getAccountType } as any)
    setUserRepository({
      getAuthSnapshot: sinon.stub().resolves({ _id: OWNER, email: 'owner@example.com', role: 'member', kind: 'human' }),
    } as any)
    authMiddleware = new AuthMiddleware(
      logger as unknown as Logger,
      tokenService,
      () => (oauthConfigured ? ({ verifyAccessToken } as any) : null),
    )
  })

  afterEach(() => {
    setOAuthAppRepository()
    setOrgRepository()
    setUserRepository()
    sinon.restore()
  })

  /** The app as app.ts wires it: request context, body parser, the routers, the error middleware last. */
  async function serve<T>(fn: (base: string) => Promise<T>): Promise<T> {
    const app = express()
    app.use(requestContextMiddleware)
    app.use(express.json())
    const api = express.Router()
    api.get('/records', authMiddleware.authenticate, (_req: Request, res: Response) => {
      res.json({ ok: true })
    })
    app.use('/api/v1/knowledgeBase', api)
    const container = new Container()
    container.bind('AuthMiddleware').toConstantValue(authMiddleware)
    container.bind('AppConfig').toConstantValue({ oauthIssuer: ISSUER, frontendUrl: ISSUER, maxRequestsPerMinute: 2 } as any)
    container.bind('Logger').toConstantValue(logger)
    container.bind('OAuthTokenService').toConstantValue({ mcpResource: `${ISSUER}/mcp` } as any)
    app.use('/mcp', createMCPRouter(container))
    app.use(ErrorMiddleware.handleError())
    const server = app.listen(0, '127.0.0.1')
    await new Promise((resolve) => server.once('listening', resolve))
    try {
      const { port } = server.address() as AddressInfo
      return await fn(`http://127.0.0.1:${port}`)
    } finally {
      await new Promise((resolve) => server.close(resolve))
    }
  }

  async function call(url: string, init: RequestInit = {}): Promise<Answer> {
    const res = await fetch(url, init)
    const text = await res.text()
    let body: any = text
    try { body = JSON.parse(text) } catch { /* not JSON */ }
    return { status: res.status, body, wwwAuthenticate: res.headers.get('www-authenticate'), retryAfter: res.headers.get('retry-after') }
  }

  const getRecords = (base: string, authorization = oauthBearer) =>
    call(`${base}/api/v1/knowledgeBase/records`, { headers: { authorization } })

  const postMcp = (base: string, authorization: string | null = oauthBearer) =>
    call(`${base}/mcp`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json, text/event-stream',
        ...(authorization === null ? {} : { authorization }),
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
    })

  /** Status, code, Retry-After and no WWW-Authenticate, exactly; the message too when the store wrote it. */
  function expectAnswered(answer: Answer, failure: Failure): void {
    expect(
      [answer.status, answer.body?.error?.code, answer.retryAfter, answer.wwwAuthenticate],
      JSON.stringify(answer.body),
    ).to.deep.equal([failure.status, failure.code, failure.retryAfter ?? null, null])
    if (failure.message) expect(answer.body.error.message).to.equal(failure.message)
  }

  const attempts = (outcome: 'error' | 'failure') =>
    sampleValue(AUTH_ATTEMPTS_METRIC, { outcome, method: 'oauth_token' })

  // -------------------------------------------------------------------------------------------------
  for (const site of SITES) {
    describe(`/api/v1: ${SITE_TITLE[site]} fails after the token read worked`, () => {
      for (const failure of FAILURES) {
        it(`${failure.name}: answers ${failure.status} ${failure.code}, not 401`, async () => {
          lookup(site).rejects(failure.error())
          await serve(async (base) => expectAnswered(await getRecords(base), failure))
        })
      }

      it('writes no line of its own at any level: the error middleware writes the one line', async () => {
        lookup(site).rejects(mongoDown())
        await serve(async (base) => { await getRecords(base) })
        for (const level of ['error', 'warn', 'info'] as const) {
          expect(logger[level].called, `AuthMiddleware logger.${level}`).to.equal(false)
        }
      })

      for (const failure of [MONGO_DOWN, PG_LOST]) {
        it(`${failure.name}: counts the attempt as an error of the check, not a refused credential`, async () => {
          lookup(site).rejects(failure.error())
          const errorBefore = await attempts('error')
          const failureBefore = await attempts('failure')
          await serve(async (base) => { await getRecords(base) })
          expect(await attempts('error') - errorBefore).to.equal(1)
          expect(await attempts('failure') - failureBefore).to.equal(0)
        })
      }
    })

    describe(`/mcp: ${SITE_TITLE[site]} fails: the client keeps its token and the address is not counted`, () => {
      it('Mongo down: 500 without WWW-Authenticate, every time (limit 2/min)', async () => {
        lookup(site).rejects(mongoDown())
        await serve(async (base) => {
          const answers = [await postMcp(base), await postMcp(base), await postMcp(base)]
          expect(answers.map((a) => [a.status, a.wwwAuthenticate])).to.deep.equal([[500, null], [500, null], [500, null]])
        })
      })
    })
  }

  describe('/mcp: the store answers for itself', () => {
    it('Mongo connection dropped mid-query at the app lookup: 503 without WWW-Authenticate, every time', async () => {
      findLiveIdentity.rejects(MONGO_DROPPED.error())
      await serve(async (base) => {
        const answers = [await postMcp(base), await postMcp(base), await postMcp(base)]
        expect(answers.map((a) => [a.status, a.wwwAuthenticate])).to.deep.equal([[503, null], [503, null], [503, null]])
      })
    })

    it('a typed store error at the org lookup keeps its 503 and Retry-After, no WWW-Authenticate', async () => {
      getAccountType.rejects(TYPED.error())
      await serve(async (base) => {
        const answer = await postMcp(base)
        expect([answer.status, answer.retryAfter, answer.wwwAuthenticate]).to.deep.equal([503, '5', null])
      })
    })
  })

  describe('/api/v1: our own failures stay 500', () => {
    it('a bug of ours inside the app lookup (TypeError) is a 500, not a 503 "try again"', async () => {
      findLiveIdentity.resolves({ get createdBy(): string { throw new TypeError('boom') } })
      await serve(async (base) => {
        const answer = await getRecords(base)
        expect([answer.status, answer.body.error.code]).to.deep.equal([500, 'INTERNAL_ERROR'])
      })
    })

    for (const site of SITES) {
      it(`a bug of ours thrown by ${SITE_TITLE[site]} itself (TypeError) is exactly 500 INTERNAL_ERROR`, async () => {
        lookup(site).rejects(new TypeError('boom'))
        await serve(async (base) => {
          const answer = await getRecords(base)
          expect([answer.status, answer.body.error.code]).to.deep.equal([500, 'INTERNAL_ERROR'])
        })
      })
    }

    it('OAuth token checking is not set up on this server: 500 with the sentence, not 401', async () => {
      oauthConfigured = false
      const errorBefore = await attempts('error')
      const failureBefore = await attempts('failure')
      await serve(async (base) => {
        const answer = await getRecords(base)
        expect([answer.status, answer.body.error.message, answer.wwwAuthenticate]).to.deep.equal([
          500, 'OAuth authentication is not configured', null,
        ])
      })
      expect(await attempts('error') - errorBefore).to.equal(1)
      expect(await attempts('failure') - failureBefore).to.equal(0)
    })
  })

  // -------------------------------------------------------------------------------------------------
  describe('a real refusal is still a 401', () => {
    it('/api/v1: a session token signed with another key', async () => {
      const forged = jwt.sign({ userId: OWNER, orgId: ORG, role: 'member' }, 'not-the-server-secret-0123456789ab')
      await serve(async (base) => {
        const answer = await getRecords(base, `Bearer ${forged}`)
        expect([answer.status, answer.body.error.message]).to.deep.equal([401, 'Invalid token'])
      })
    })

    it('/api/v1: no token', async () => {
      await serve(async (base) => {
        const answer = await call(`${base}/api/v1/knowledgeBase/records`)
        expect([answer.status, answer.body.error.message]).to.deep.equal([401, 'No token provided'])
      })
    })

    it('/api/v1: the OAuth app is gone or revoked', async () => {
      findLiveIdentity.resolves(null)
      await serve(async (base) => {
        const answer = await getRecords(base)
        expect([answer.status, answer.body.error.message]).to.deep.equal([401, 'OAuth app not found or revoked'])
      })
    })

    it('/api/v1: the app now acts as another identity than the token was minted for', async () => {
      verifyAccessToken.resolves(clientCredentials({ createdBy: 'someone-else' }))
      await serve(async (base) => {
        const answer = await getRecords(base)
        expect([answer.status, answer.body.error.message]).to.deep.equal([
          401, 'This token was issued for an identity the application no longer acts as',
        ])
      })
    })

    it('/api/v1: an app no person created', async () => {
      findLiveIdentity.resolves({ createdBy: NO_APP_CREATOR })
      await serve(async (base) => {
        const answer = await getRecords(base)
        expect([answer.status, answer.body.error.message]).to.deep.equal([401, 'This application does not act as a user'])
      })
    })

    it('/api/v1: a refusal counts as failure, not error', async () => {
      findLiveIdentity.resolves(null)
      const before = await attempts('failure')
      await serve(async (base) => { await getRecords(base) })
      expect(await attempts('failure') - before).to.equal(1)
    })

    it('/api/v1: both lookups answer, and an org with no row is fine', async () => {
      getAccountType.resolves(null)
      await serve(async (base) => {
        const answer = await getRecords(base)
        expect([answer.status, answer.body]).to.deep.equal([200, { ok: true }])
      })
    })

    it('/mcp: an expired token gets WWW-Authenticate and counts against the address (limit 2/min)', async () => {
      verifyAccessToken.rejects(new ExpiredTokenError('Access token has expired'))
      await serve(async (base) => {
        const answers = [await postMcp(base), await postMcp(base), await postMcp(base)]
        expect(answers.map((a) => a.status)).to.deep.equal([401, 401, 429])
        expect(answers[0]!.wwwAuthenticate).to.equal(
          `Bearer resource_metadata="${ISSUER}/.well-known/oauth-protected-resource/mcp"`,
        )
      })
    })

    it('/mcp: the app is gone: 401 with WWW-Authenticate', async () => {
      findLiveIdentity.resolves(null)
      await serve(async (base) => {
        const answer = await postMcp(base)
        expect(answer.status).to.equal(401)
        expect(answer.wwwAuthenticate).to.contain('resource_metadata=')
      })
    })

    it('/mcp: no token is the discovery probe: 401 with WWW-Authenticate, never counted', async () => {
      await serve(async (base) => {
        const answers = [await postMcp(base, null), await postMcp(base, null), await postMcp(base, null)]
        expect(answers.map((a) => a.status)).to.deep.equal([401, 401, 401])
        expect(answers[2]!.wwwAuthenticate).to.contain('resource_metadata=')
      })
    })
  })

  describe('the OAuth provider path (OAuthAuthMiddleware) does not share this code and answers 503', () => {
    it('a store failure while checking the token is 503 temporarily_unavailable', async () => {
      const oauthMiddleware = new OAuthAuthMiddleware(
        logger as unknown as Logger,
        { verifyAccessToken: sinon.stub().rejects(MONGO_DROPPED.error()) } as any,
        {} as any,
      )
      const app = express()
      app.get('/userinfo', oauthMiddleware.authenticate, (_req: Request, res: Response) => { res.json({ ok: true }) })
      const server = app.listen(0, '127.0.0.1')
      await new Promise((resolve) => server.once('listening', resolve))
      try {
        const { port } = server.address() as AddressInfo
        const answer = await call(`http://127.0.0.1:${port}/userinfo`, { headers: { authorization: 'Bearer x.y.z' } })
        expect([answer.status, answer.body.error]).to.deep.equal([503, 'temporarily_unavailable'])
      } finally {
        await new Promise((resolve) => server.close(resolve))
      }
    })
  })
})
