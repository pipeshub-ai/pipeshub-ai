import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { ADMIN, Harness, MEMBER, sessionToken, startHarness } from '../../tokens_manager/routes/connectors-http-harness'
import { createOAuthClientsRouter } from '../../../../src/modules/oauth_provider/routes/oauth.clients.routes'
import { OAuthAppController } from '../../../../src/modules/oauth_provider/controller/oauth.app.controller'
import { Logger } from '../../../../src/libs/services/logger.service'
import { ForbiddenError, NotFoundError } from '../../../../src/libs/errors/http.errors'
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service'
import { captureLogRecords, newRequestId, recordsOf, type LogRecord } from '../../../helpers/log-capture'

// OAuth clients (Developer Settings): Node saves them itself (OAuthAppService). A change the service returned from is one
// `config.oauth_client.*` audit line naming who made it; a change the service refused or failed writes none.

const K = '/api/v1/oauth-clients'
const APP_ID = '64b0000000000000000000f1'
const CREATED = '64b0000000000000000000f2'
const SECRET = 'n5p-secret-marker-41d9'

type ServiceMethod =
  | 'createApp' | 'updateApp' | 'deleteApp' | 'regenerateSecret' | 'suspendApp' | 'activateApp' | 'setTokenIdentity'

interface Change {
  name: string
  method: string
  path: string
  body?: unknown
  /** The service call that saves the change; it rejecting is a refused or failed change. */
  saves: ServiceMethod | 'revokeAllTokensForApp'
  event: string
  reason: string | null
  target: string
}

const CHANGES: Change[] = [
  { name: 'K1 create', method: 'POST', path: `${K}/`, body: { name: 'CLI', allowedGrantTypes: ['authorization_code'], allowedScopes: ['kb:read'], redirectUris: ['https://cli.acme.test/cb'] }, saves: 'createApp', event: 'config.oauth_client.create', reason: null, target: CREATED },
  { name: 'K2 update', method: 'PUT', path: `${K}/${APP_ID}`, body: { name: 'CLI 2' }, saves: 'updateApp', event: 'config.oauth_client.update', reason: 'settings', target: APP_ID },
  { name: 'K3 delete', method: 'DELETE', path: `${K}/${APP_ID}`, saves: 'deleteApp', event: 'config.oauth_client.delete', reason: null, target: APP_ID },
  { name: 'K4 secret rotate', method: 'POST', path: `${K}/${APP_ID}/regenerate-secret`, saves: 'regenerateSecret', event: 'config.oauth_client.update', reason: 'secret_rotate', target: APP_ID },
  { name: 'K5 suspend', method: 'POST', path: `${K}/${APP_ID}/suspend`, saves: 'suspendApp', event: 'config.oauth_client.update', reason: 'suspend', target: APP_ID },
  { name: 'K6 activate', method: 'POST', path: `${K}/${APP_ID}/activate`, saves: 'activateApp', event: 'config.oauth_client.update', reason: 'activate', target: APP_ID },
  { name: 'K7 token identity', method: 'PUT', path: `${K}/${APP_ID}/token-identity`, body: { serviceAccountId: null }, saves: 'setTokenIdentity', event: 'config.oauth_client.update', reason: 'token_identity', target: APP_ID },
  { name: 'K8 revoke all tokens', method: 'POST', path: `${K}/${APP_ID}/revoke-all-tokens`, saves: 'revokeAllTokensForApp', event: 'config.oauth_client.update', reason: 'tokens_revoked', target: APP_ID },
]

const app = { id: APP_ID, clientId: 'client-1', name: 'CLI', status: 'active' }
const pause = (ms: number) => new Promise((r) => setTimeout(r, ms))
/** Polls until `done()` or the deadline; the caller's assertion then says what is missing. */
const until = async (done: () => boolean, deadlineMs = 5000): Promise<void> => {
  const end = Date.now() + deadlineMs
  while (!done() && Date.now() < end) await pause(10)
}

describe('OAuth clients saved by Node: config audit lines', function () {
  this.timeout(30000)
  let h: Harness
  let records: LogRecord[]
  let service: Record<ServiceMethod | 'getAppById', sinon.SinonStub>
  let tokens: { revokeAllTokensForApp: sinon.SinonStub; listTokensForApp: sinon.SinonStub }

  // The real router and controller; the app and token services are faked at their boundary.
  const oauthClients = (container: Container) => {
    service = {
      createApp: sinon.stub().resolves({ ...app, id: CREATED, clientSecret: SECRET }),
      updateApp: sinon.stub().resolves(app),
      deleteApp: sinon.stub().resolves(),
      regenerateSecret: sinon.stub().resolves({ ...app, clientSecret: SECRET }),
      suspendApp: sinon.stub().resolves(app),
      activateApp: sinon.stub().resolves(app),
      setTokenIdentity: sinon.stub().resolves(app),
      getAppById: sinon.stub().resolves(app),
    }
    tokens = { revokeAllTokensForApp: sinon.stub().resolves(), listTokensForApp: sinon.stub().resolves([]) }
    container.bind('OAuthAppController').toConstantValue(new OAuthAppController(service as any, tokens as any, {} as any))
    container.bind('Logger').toConstantValue(Logger.getInstance())
    return createOAuthClientsRouter(container)
  }
  const savesStub = (c: Change): sinon.SinonStub =>
    c.saves === 'revokeAllTokensForApp' ? tokens.revokeAllTokensForApp : service[c.saves]

  // The harness stubs the user lookups on the default sandbox, which each test restores: one harness per test.
  beforeEach(async () => {
    h = await startHarness({ extraRouters: [{ mountPath: K, create: oauthClients }] })
    sinon.stub(userAdminService, 'isUserOrgAdmin').resolves(true)
    records = captureLogRecords()
  })
  afterEach(async () => {
    await h.close()
    sinon.restore()
  })

  const send = async (
    c: Pick<Change, 'method' | 'path' | 'body'>,
    { user = ADMIN, signal }: { user?: typeof ADMIN; signal?: AbortSignal } = {},
  ): Promise<{ id: string; status: number; lines: LogRecord[] }> => {
    const id = newRequestId('n5p3')
    let status = 0
    try {
      const res = await fetch(`${h.origin}${c.path}`, {
        method: c.method,
        headers: {
          authorization: `Bearer ${sessionToken(h, user)}`,
          'x-request-id': id,
          ...(c.body !== undefined && { 'content-type': 'application/json' }),
        },
        body: c.body === undefined ? undefined : JSON.stringify(c.body),
        signal,
      })
      status = res.status
      await res.text()
    } catch (error) {
      // The client gave up (the abort case); status stays 0.
      if ((error as Error).name !== 'TimeoutError') throw error
    }
    // The access line is written on 'close'.
    await until(() => recordsOf(records, id).some((r) => r.message === 'http.request.end'))
    return { id, status, lines: recordsOf(records, id) }
  }
  const auditLines = (lines: LogRecord[]) => lines.filter((r) => r.service === 'audit')

  for (const c of CHANGES) {
    describe(`${c.name}: ${c.method} ${c.path}`, () => {
      it('a change the service saved is one audit line naming the admin, before the access line', async () => {
        const { status, lines } = await send(c)

        expect(status).to.be.within(200, 299)
        expect(savesStub(c).callCount).to.equal(1)
        const [line, ...more] = auditLines(lines)
        expect(more, 'one audit line').to.have.length(0)
        expect(line).to.include({
          message: c.event,
          level: 'info',
          actor: ADMIN._id,
          org_id: ADMIN.orgId,
          target: c.target,
          outcome: 'success',
          reason: c.reason,
        })
        const access = lines.findIndex((r) => r.message === 'http.request.end')
        expect(access, 'access line').to.be.greaterThan(lines.indexOf(line!))
        expect(JSON.stringify(lines)).to.not.contain(SECRET)
        for (const v of ['cli.acme.test', 'client-1']) expect(JSON.stringify(auditLines(lines))).to.not.contain(v)
      })

      for (const [label, error, status] of [
        ['refuses it (not the caller\'s app)', new ForbiddenError('Not allowed'), 403],
        ['cannot find the app', new NotFoundError('OAuth app not found'), 404],
        ['fails', new Error('mongo down'), 500],
      ] as const) {
        it(`no audit line when the service ${label}`, async () => {
          savesStub(c).rejects(error)
          const res = await send(c)

          expect(res.status).to.equal(status)
          expect(savesStub(c).callCount).to.equal(1)
          expect(auditLines(res.lines)).to.deep.equal([])
        })
      }
    })
  }

  it('K8: no audit line when the app it names cannot be read (nothing revoked)', async () => {
    service.getAppById.rejects(new NotFoundError('OAuth app not found'))
    const { status, lines } = await send(CHANGES[7]!)

    expect(status).to.equal(404)
    expect(tokens.revokeAllTokensForApp.called).to.equal(false)
    expect(auditLines(lines)).to.deep.equal([])
  })

  it('K1: a created id that is not a PipesHub id is never printed (target null, reason invalid_id)', async () => {
    service.createApp.resolves({ ...app, id: 'admin@acme.test', clientSecret: SECRET })
    const { lines } = await send(CHANGES[0]!)

    expect(auditLines(lines).map((r) => [r.message, r.target, r.reason])).to.deep.equal([
      ['config.oauth_client.create', null, 'invalid_id'],
    ])
    expect(JSON.stringify(lines)).to.not.contain('admin@acme.test')
  })

  it('a member\'s change to their own app names the member', async () => {
    const { status, lines } = await send(CHANGES[4]!, { user: MEMBER })

    expect(status).to.equal(200)
    expect(auditLines(lines).map((r) => [r.message, r.actor, r.reason, r.target])).to.deep.equal([
      ['config.oauth_client.update', MEMBER._id, 'suspend', APP_ID],
    ])
  })

  it('a change whose client left before the service returned is still one audit line', async () => {
    // The client leaves once the service has the change (not on a clock); the service returns after the connection closed.
    const client = new AbortController()
    service.suspendApp.callsFake(async () => {
      client.abort(new DOMException('client left', 'TimeoutError'))
      await until(() => records.some((r) => r.message === 'http.request.end'))
      return app
    })
    const { id, status } = await send(CHANGES[4]!, { signal: client.signal })
    await until(() => auditLines(recordsOf(records, id)).length > 0)

    expect(status, 'the client left').to.equal(0)
    expect(auditLines(recordsOf(records, id)).map((r) => [r.message, r.reason, r.target])).to.deep.equal([
      ['config.oauth_client.update', 'suspend', APP_ID],
    ])
  })

  for (const [name, path] of [
    ['the app list', `${K}/`],
    ['one app', `${K}/${APP_ID}`],
    ['an app\'s tokens', `${K}/${APP_ID}/tokens`],
  ] as const) {
    it(`out of scope: a read of ${name} writes no audit line`, async () => {
      ;(service as any).listApps = sinon.stub().resolves({ data: [], total: 0 })
      const { status, lines } = await send({ method: 'GET', path })

      expect(status).to.equal(200)
      expect(auditLines(lines)).to.deep.equal([])
    })
  }
})
