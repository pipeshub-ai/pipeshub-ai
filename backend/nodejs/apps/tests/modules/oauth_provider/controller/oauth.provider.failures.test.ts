/**
 * How the OAuth provider answers and logs a failed request (docs/logging.md "Routes that answer their own errors (Node)"):
 * a refusal is the client's and keeps its RFC 6749 code; our own failure, whatever its status, is a 500 (a 503 where the
 * RFC says retry) with a fixed description and one log line. Real controller, request context, access line and Logger;
 * the OAuth services are stubs.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express, { RequestHandler } from 'express'
import http from 'http'
import type { AddressInfo } from 'net'
import { Logger } from '../../../../src/libs/services/logger.service'
import { requestContextMiddleware } from '../../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../../src/libs/middlewares/access-log.middleware'
import { OAuthProviderController } from '../../../../src/modules/oauth_provider/controller/oauth.provider.controller'
import { OAuthAppService } from '../../../../src/modules/oauth_provider/services/oauth.app.service'
import { OAUTH_SERVER_FAILURE } from '../../../../src/modules/oauth_provider/utils/oauth-failure'
import * as platformEvents from '../../../../src/libs/platform-events'
import { setOrgRepository } from '../../../../src/libs/repositories/org'
import {
  AccessDeniedError,
  DeviceGrantError,
  ExpiredTokenError,
  InvalidClientError,
  InvalidGrantError,
  InvalidScopeError,
  InvalidTokenError,
  ServerError,
  UnauthorizedClientError,
  UnsupportedGrantTypeError,
} from '../../../../src/libs/errors/oauth.errors'
import {
  BadRequestError,
  ConflictError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../../src/libs/errors/http.errors'
import { DecryptionError } from '../../../../src/libs/errors/encryption.errors'
import { UniqueConstraintError } from '../../../../src/libs/errors/database.errors'
import { captureLogRecords, newRequestId, recordsOf, LogRecord } from '../../../helpers/log-capture'

const ORG = '64b000000000000000000a01'
// What a driver says when the store is gone: a host and an address that must reach the log, never the client.
const STORE_DOWN = 'connection 3 to 10.20.30.40:27017 closed (mongo-prod-0.internal)'
const CLIENT = { client_id: 'pha_client', client_secret: 'pha_secret' }

const level = (r: LogRecord): string => {
  const l = String(r.level).toLowerCase()
  return l === 'warning' ? 'warn' : l
}

describe('OAuthProviderController failures', function () {
  this.timeout(20000)
  const stubs = () => ({
    app: {
      verifyClientCredentials: sinon.stub(),
      getAppByClientId: sinon.stub(),
      isGrantTypeAllowed: sinon.stub().returns(true),
      validateRedirectUriForApp: sinon.stub(),
    },
    token: {
      revokeAgentToken: sinon.stub().resolves(false),
      revokeToken: sinon.stub(),
      refreshTokens: sinon.stub(),
      introspectToken: sinon.stub(),
      audienceFor: sinon.stub().returns(undefined),
    },
    // The refresh grant caps the app's scopes through allowedScopesOf before it reaches refreshTokens.
    device: {
      createAuthorization: sinon.stub(),
      poll: sinon.stub(),
      allowedScopesOf: sinon.stub().callsFake((a: { allowedScopes?: string[] }) => a.allowedScopes),
    },
    dcr: { resolveAuthorizeClient: sinon.stub() },
  })
  let records: LogRecord[]
  let app: ReturnType<typeof stubs>['app']
  let token: ReturnType<typeof stubs>['token']
  let device: ReturnType<typeof stubs>['device']
  let dcr: ReturnType<typeof stubs>['dcr']
  let servers: http.Server[]

  beforeEach(() => {
    records = captureLogRecords()
    servers = []
    ;({ app, token, device, dcr } = stubs())
  })
  afterEach(async () => {
    sinon.restore()
    setOrgRepository()
    for (const s of servers) {
      s.closeAllConnections()
      await new Promise<void>((resolve) => s.close(() => resolve()))
    }
  })

  const controller = (): OAuthProviderController =>
    new OAuthProviderController(
      Logger.getInstance({ service: 'OAuthProvider' }),
      app as never,
      token as never,
      {} as never,
      {
        parseScopes: sinon.stub().returns([]),
        resolveGrantedScopes: sinon.stub().returns({ granted: [], notGranted: [] }),
      } as never,
      dcr as never,
      device as never,
      {} as never,
      {} as never,
    )

  /** The app.ts order: request context, access line, body parser, the endpoint. */
  const serve = async (method: 'get' | 'post', path: string, ...handlers: RequestHandler[]): Promise<string> => {
    const server = express()
    server.use(requestContextMiddleware)
    server.use(accessLog())
    server.use(express.json())
    // The endpoints are async; each answers its own errors, so nothing is left for express to catch.
    server[method](`/api/v1${path}`, ...handlers.map((h): RequestHandler => (req, res, next) => void h(req, res, next)))
    const listener = server.listen(0, '127.0.0.1')
    servers.push(listener)
    await new Promise<void>((resolve) => listener.once('listening', () => resolve()))
    return `http://127.0.0.1:${(listener.address() as AddressInfo).port}/api/v1${path}`
  }

  interface Answer { status: number; text: string; body: any; lines: LogRecord[] }

  const call = async (url: string, method: string, body?: unknown, headers: Record<string, string> = {}): Promise<Answer> => {
    const id = newRequestId('oauth')
    const res = await fetch(url, {
      method,
      headers: { 'content-type': 'application/json', 'x-request-id': id, ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    const deadline = Date.now() + 3000
    while (Date.now() < deadline && !recordsOf(records, id).some((r) => r.message === 'http.request.end')) {
      await new Promise((resolve) => setTimeout(resolve, 5))
    }
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = undefined
    }
    return { status: res.status, text, body: parsed, lines: recordsOf(records, id) }
  }

  const tokenEndpoint = () => {
    const c = controller()
    return serve('post', '/oauth2/token', c.token.bind(c))
  }
  const revokeEndpoint = () => {
    const c = controller()
    return serve('post', '/oauth2/revoke', c.revoke.bind(c))
  }
  const failed = (a: Answer) => a.lines.filter((r) => r.message === 'oauth.request.failed')
  /** The failure we injected is the one logged, so a test cannot pass on a TypeError from a missing stub. */
  const loggedCause = (a: Answer): string => failed(a)[0]!.metadata.error.message
  const accessLine = (a: Answer) => a.lines.find((r) => r.message === 'http.request.end')!
  const above = (a: Answer, levels: string[]) =>
    a.lines.filter((r) => r.message !== 'http.request.end' && levels.includes(level(r)))

  describe('POST /token', () => {
    it('answers a store outage 500 with a fixed description, and logs it once at ERROR with the driver text', async () => {
      app.verifyClientCredentials.rejects(new Error(STORE_DOWN))
      const a = await call(await tokenEndpoint(), 'POST', { grant_type: 'client_credentials', ...CLIENT })

      expect(a.status).to.equal(500)
      expect(a.body).to.deep.equal({ error: 'server_error', error_description: OAUTH_SERVER_FAILURE })
      expect(a.text).to.not.contain('10.20.30.40')
      expect(failed(a)).to.have.length(1)
      const line = failed(a)[0]!
      expect(level(line)).to.equal('error')
      expect(line.metadata.error.message).to.contain('10.20.30.40')
      expect(line.metadata.error.stack).to.be.a('string')
      expect(accessLine(a).error_class).to.equal('internal')
    })

    // Ours though their status is 4xx: a key change, a lock timeout, a duplicate row.
    for (const [name, error] of [
      ['DecryptionError (400)', new DecryptionError('Decryption failed, could be due to a different secret key')],
      ['ConflictError (409, a lock timeout)', new ConflictError('Another change holds this record; try again')],
      ['UniqueConstraintError (409)', new UniqueConstraintError('duplicate key')],
      ['ServerError (an OAuth 500)', new ServerError('boom')],
      ['TypeError', new TypeError("Cannot read properties of undefined (reading 'x')")],
    ] as const) {
      it(`answers our ${name} 500 server_error, at ERROR, without its text`, async () => {
        app.verifyClientCredentials.rejects(error)
        const a = await call(await tokenEndpoint(), 'POST', { grant_type: 'client_credentials', ...CLIENT })

        expect(a.status).to.equal(500)
        expect(a.body.error).to.equal('server_error')
        expect(a.text).to.not.contain(error.message)
        expect(failed(a).map(level)).to.deep.equal(['error'])
        expect(loggedCause(a)).to.equal(error.message)
      })
    }

    it('logs an outage we wrapped (a 503 BaseError) at WARN, class unavailable', async () => {
      app.verifyClientCredentials.rejects(new ServiceUnavailableError('broker down'))
      const a = await call(await tokenEndpoint(), 'POST', { grant_type: 'client_credentials', ...CLIENT })

      expect(a.status).to.equal(500)
      expect(failed(a).map(level)).to.deep.equal(['warn'])
      expect(accessLine(a).error_class).to.equal('unavailable')
    })

    // RFC 6749 5.2: each refusal keeps its code; server_error is never a refusal's.
    for (const [error, status, code] of [
      [new InvalidGrantError('code expired'), 400, 'invalid_grant'],
      [new InvalidTokenError('Refresh token not found'), 400, 'invalid_grant'],
      [new ExpiredTokenError('Refresh token has expired'), 400, 'invalid_grant'],
      [new NotFoundError('no such grant'), 400, 'invalid_grant'],
      [new InvalidClientError('bad secret'), 401, 'invalid_client'],
      [new InvalidScopeError('too wide'), 400, 'invalid_scope'],
      [new UnsupportedGrantTypeError('not allowed'), 400, 'unsupported_grant_type'],
      [new AccessDeniedError('denied'), 403, 'access_denied'],
      [new UnauthorizedClientError('not this client'), 400, 'unauthorized_client'],
      [new BadRequestError('missing identity'), 400, 'invalid_request'],
    ] as const) {
      it(`answers ${error.constructor.name} ${status} ${code}, with no line above DEBUG`, async () => {
        app.getAppByClientId.resolves({ isConfidential: false })
        token.refreshTokens.rejects(error)
        const a = await call(await tokenEndpoint(), 'POST', {
          grant_type: 'refresh_token', client_id: 'pha_device', refresh_token: 'pha_rt',
        })

        expect(a.status).to.equal(status)
        expect(a.body).to.deep.equal({ error: code, error_description: error.message })
        expect(above(a, ['info', 'warn', 'error'])).to.deep.equal([])
        const refused = a.lines.find((r) => r.message === 'oauth.request.refused')!
        expect(refused.code).to.equal(code)
        // Only a /token schema refusal carries errors; the controller's line is just the code.
        expect(refused.metadata?.errors).to.equal(undefined)
      })
    }

    it('marks a wrong client credential bad_request on the access line', async () => {
      app.verifyClientCredentials.rejects(new InvalidClientError('Invalid client credentials'))
      const a = await call(await tokenEndpoint(), 'POST', { grant_type: 'client_credentials', ...CLIENT })

      expect(a.status).to.equal(401)
      expect(accessLine(a).error_class).to.equal('bad_request')
    })

    it('logs a failure after the answer was sent and does not answer twice', async () => {
      app.verifyClientCredentials.resolves({ allowedScopes: ['org:read'], orgId: ORG })
      const c = controller()
      sinon.stub(c as any, 'handleClientCredentialsGrant').resolves({ access_token: 'a', token_type: 'Bearer' })
      sinon.stub(platformEvents, 'emitPlatformEvent').throws(new TypeError('late failure'))
      const a = await call(await serve('post', '/oauth2/token', c.token.bind(c)), 'POST', {
        grant_type: 'client_credentials', ...CLIENT,
      })

      expect(a.status).to.equal(200)
      expect(failed(a).map(level)).to.deep.equal(['error'])
      expect(loggedCause(a)).to.equal('late failure')
    })
    // The client's mistake, not ours: refused before any read, instead of a TypeError from hashing nothing.
    it('answers client_credentials without client_secret 401 invalid_client, with no ERROR', async () => {
      const real = new OAuthAppService(Logger.getInstance({ service: 'OAuthApp' }), {} as never, {} as never, {} as never)
      app.verifyClientCredentials.callsFake((id: string, secret: string) => real.verifyClientCredentials(id, secret))
      const a = await call(await tokenEndpoint(), 'POST', { grant_type: 'client_credentials', client_id: 'pha_client' })

      expect(a.status).to.equal(401)
      expect(a.body.error).to.equal('invalid_client')
      expect(failed(a)).to.deep.equal([])
    })
  })

  describe('POST /token, Basic client authentication', () => {
    // The header is not schema-checked: a NUL there is refused as the client's, before PostgreSQL could fail on it.
    it('refuses a control character in the Basic client_id 401 invalid_client, before any store read', async () => {
      const basic = Buffer.from('pha_\u0000client:pha_secret', 'utf-8').toString('base64')
      const a = await call(await tokenEndpoint(), 'POST', { grant_type: 'client_credentials' }, {
        authorization: `Basic ${basic}`,
      })

      expect(a.status).to.equal(401)
      expect(a.body.error).to.equal('invalid_client')
      expect(app.verifyClientCredentials.called).to.equal(false)
      expect(above(a, ['info', 'warn', 'error'])).to.deep.equal([])
      expect(a.lines.find((r) => r.message === 'oauth.request.refused')!.code).to.equal('invalid_client')
    })
  })

  describe('POST /device_authorization', () => {
    it('answers a missing frontend URL (our setting) 500, without naming it', async () => {
      const c = controller()
      const a = await call(await serve('post', '/oauth2/device_authorization', c.deviceAuthorization.bind(c)), 'POST', {
        client_id: 'pha_device',
      })

      expect(a.status).to.equal(500)
      expect(a.text).to.not.contain('frontendUrl')
      expect(failed(a).map(level)).to.deep.equal(['error'])
      expect(loggedCause(a)).to.equal('frontendUrl is not configured')
    })

    it('answers a refusal with its device code and logs it at DEBUG only', async () => {
      device.createAuthorization.rejects(new DeviceGrantError('invalid_client', 'unknown client'))
      const c = controller()
      const withFrontend: RequestHandler = (req, _res, next) => {
        ;(req as any).oauthFrontendUrl = 'https://app.acme.test'
        next()
      }
      const a = await call(
        await serve('post', '/oauth2/device_authorization', withFrontend, c.deviceAuthorization.bind(c)),
        'POST',
        { client_id: 'pha_device' },
      )

      expect(a.status).to.equal(400)
      expect(a.body.error).to.equal('invalid_client')
      expect(above(a, ['info', 'warn', 'error'])).to.deep.equal([])
    })
  })

  describe('POST /revoke', () => {
    it('answers a failed revocation 503 temporarily_unavailable (the token stays live), logged at ERROR', async () => {
      app.getAppByClientId.resolves({ isConfidential: false })
      token.revokeToken.rejects(new Error(STORE_DOWN))
      const a = await call(await revokeEndpoint(), 'POST', { token: 'pha_at', client_id: 'pha_device' })

      expect(a.status).to.equal(503)
      expect(a.body).to.deep.equal({ error: 'temporarily_unavailable', error_description: OAUTH_SERVER_FAILURE })
      expect(a.text).to.not.contain('10.20.30.40')
      expect(failed(a).map(level)).to.deep.equal(['error'])
      expect(loggedCause(a)).to.equal(STORE_DOWN)
    })

    for (const [name, error] of [
      ['DecryptionError during client authentication', new DecryptionError('Decryption failed')],
      ['ConflictError (a lock timeout)', new ConflictError('Another change holds this record; try again')],
    ] as const) {
      it(`answers our ${name} 503 and keeps it at ERROR`, async () => {
        app.getAppByClientId.resolves({ isConfidential: true })
        app.verifyClientCredentials.rejects(error)
        const a = await call(await revokeEndpoint(), 'POST', { token: 'pha_at', ...CLIENT })

        expect(a.status).to.equal(503)
        expect(failed(a).map(level)).to.deep.equal(['error'])
        expect(loggedCause(a)).to.equal(error.message)
      })
    }

    it('answers an unknown token 200 with no line (RFC 7009 2.2)', async () => {
      app.getAppByClientId.resolves({ isConfidential: false })
      token.revokeToken.resolves(false)
      const a = await call(await revokeEndpoint(), 'POST', { token: 'pha_unknown', client_id: 'pha_device' })

      expect(a.status).to.equal(200)
      expect(above(a, ['debug', 'info', 'warn', 'error'])).to.deep.equal([])
    })

    it('answers a refusal 200 and a wrong client credential 401', async () => {
      app.getAppByClientId.resolves({ isConfidential: false })
      token.revokeToken.rejects(new NotFoundError('not this client'))
      expect((await call(await revokeEndpoint(), 'POST', { token: 't', client_id: 'pha_device' })).status).to.equal(200)

      app.getAppByClientId.rejects(new InvalidClientError('Invalid client_id'))
      const a = await call(await revokeEndpoint(), 'POST', { token: 't', client_id: 'pha_nope' })
      expect(a.status).to.equal(401)
      expect(a.body.error).to.equal('invalid_client')
    })
  })

  describe('POST /introspect', () => {
    it('still answers active:false on our failure, and logs it once', async () => {
      app.verifyClientCredentials.resolves({})
      token.introspectToken.rejects(new Error(STORE_DOWN))
      const c = controller()
      const a = await call(await serve('post', '/oauth2/introspect', c.introspect.bind(c)), 'POST', {
        token: 'pha_at', ...CLIENT,
      })

      expect(a.status).to.equal(200)
      expect(a.body).to.deep.equal({ active: false })
      expect(failed(a).map(level)).to.deep.equal(['error'])
      expect(loggedCause(a)).to.equal(STORE_DOWN)
    })
  })

  describe('POST /introspect, refusal', () => {
    // A refusal from the store read is not our failure: active:false as before, and no line above DEBUG.
    it('answers an invalid token active:false and logs nothing above DEBUG', async () => {
      app.verifyClientCredentials.resolves({})
      token.introspectToken.rejects(new InvalidTokenError('Invalid access token'))
      const c = controller()
      const a = await call(await serve('post', '/oauth2/introspect', c.introspect.bind(c)), 'POST', {
        token: 'pha_at', ...CLIENT,
      })

      expect(a.status).to.equal(200)
      expect(a.body).to.deep.equal({ active: false })
      expect(above(a, ['info', 'warn', 'error'])).to.deep.equal([])
    })
  })

  describe('GET /authorize', () => {
    const signedIn: RequestHandler = (req, _res, next) => {
      ;(req as any).user = { userId: '64b0000000000000000000a1', orgId: ORG, email: 'ada@acme.test' }
      next()
    }
    const authorize = () => {
      const c = controller()
      return serve('get', '/oauth2/authorize', signedIn, c.authorize.bind(c))
    }
    const QUERY = '?client_id=pha_client&redirect_uri=https%3A%2F%2Fclient.example%2Fcb&response_type=code&state=st1'

    it('redirects our failure as server_error with a fixed description, never our text', async () => {
      dcr.resolveAuthorizeClient.resolves({ app: { orgId: ORG, allowedScopes: [], isConfidential: true } })
      const getNames = sinon.stub().rejects(new Error(STORE_DOWN))
      setOrgRepository({ getNames } as never)
      const a = await call(`${await authorize()}${QUERY}`, 'GET')

      const redirect = new URL(a.body.redirectUrl)
      expect(redirect.searchParams.get('error')).to.equal('server_error')
      expect(redirect.searchParams.get('error_description')).to.equal(OAUTH_SERVER_FAILURE)
      expect(redirect.searchParams.get('state')).to.equal('st1')
      expect(a.text).to.not.contain('10.20.30.40')
      expect(failed(a).map(level)).to.deep.equal(['error'])
      expect(getNames.calledOnceWith(ORG)).to.equal(true)
      expect(loggedCause(a)).to.equal(STORE_DOWN)
    })

    it('redirects a refusal with its code', async () => {
      dcr.resolveAuthorizeClient.resolves({ app: { orgId: ORG, allowedScopes: [], isConfidential: true } })
      token.audienceFor.throws(new InvalidScopeError('unknown resource'))
      const a = await call(`${await authorize()}${QUERY}`, 'GET')

      const redirect = new URL(a.body.redirectUrl)
      expect(redirect.searchParams.get('error')).to.equal('invalid_scope')
      expect(redirect.searchParams.get('error_description')).to.equal('unknown resource')
      expect(failed(a)).to.deep.equal([])
    })
  })
})
