import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import nock from 'nock'
import jwt from 'jsonwebtoken'
import {
  MEMBER,
  ADMIN,
  ADMIN_ID,
  ORG_A,
  Harness,
  call,
  errorMessage,
  fetchConfigToken,
  sessionToken,
  single,
  startHarness,
} from './connectors-http-harness'
import * as tokensConfig from '../../../../src/modules/tokens_manager/config/config'
import { ConnectorsConfig } from '../../../../src/modules/configuration_manager/schema/connectors.schema'
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum'

const admin = ADMIN
const member = MEMBER

const GOOGLE = 'https://oauth2.googleapis.com'
const CM_OAUTH_CONFIG = '/api/v1/configurationManager/internal/connectors/googleWorkspaceOauthConfig'
const CM_CREDENTIALS = '/api/v1/configurationManager/internal/connectors/googleWorkspaceCredentials'
const CM_INDIVIDUAL_CREDENTIALS = '/api/v1/configurationManager/internal/connectors/individual/googleWorkspaceCredentials'
const DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
const GMAIL_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'

const oauthConfig = { clientId: 'google-client', clientSecret: 'google-secret', enableRealTimeUpdates: true, topicName: 'projects/p/topics/t' }

const idTokenFor = (email: string): string => jwt.sign({ email, sub: 'google-sub' }, 'google-signs-this')

describe('Connector routes: legacy Google Workspace endpoints', () => {
  let h: Harness

  beforeEach(async () => {
    h = await startHarness()
    nock.disableNetConnect()
    nock.enableNetConnect('127.0.0.1')
  })

  afterEach(async () => {
    nock.cleanAll()
    nock.enableNetConnect()
    sinon.restore()
    await h.close()
  })

  describe('POST /getTokenFromCode', () => {
    beforeEach(() => {
      sinon.stub(tokensConfig, 'loadAppConfig').resolves({ frontendUrl: 'https://app.acme.test/' } as tokensConfig.AppConfig)
    })

    const googleGrants = (scope: string, email = admin.email) =>
      nock(GOOGLE)
        .post('/token', {
          code: 'consent-code',
          client_id: 'google-client',
          client_secret: 'google-secret',
          redirect_uri: 'https://app.acme.test/account/individual/settings/connector/googleWorkspace',
          grant_type: 'authorization_code',
        })
        .reply(200, {
          access_token: 'google-access',
          refresh_token: 'google-refresh',
          expires_in: 3600,
          refresh_token_expires_in: 7200,
          scope,
          id_token: idTokenFor(email),
        })

    it('refuses a member without calling Google or the configuration manager', async () => {
      const google = googleGrants(DRIVE_SCOPE)

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, member), { tempCode: 'consent-code' })

      expect(r.status).to.be.within(400, 499)
      expect(errorMessage(r)).to.equal('Admin access required')
      expect(google.isDone()).to.equal(false)
      expect(h.backend.calls).to.have.length(0)
    })

    it("creates the org's connector, stores the tokens under the caller and starts a sync of all three apps", async () => {
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      h.backend.on('POST', CM_CREDENTIALS, { status: 200, body: { ok: true } })
      const google = googleGrants(`${DRIVE_SCOPE} openid`)
      const created = { _id: 'cfg-1', name: 'Google Workspace', orgId: ORG_A }
      const findOne = sinon.stub(ConnectorsConfig, 'findOne')
      findOne.onFirstCall().resolves(null)
      findOne.onSecondCall().resolves(created as never)
      const save = sinon.stub(ConnectorsConfig.prototype, 'save').resolves()
      const before = Date.now()

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'consent-code' })

      expect(r.status).to.equal(201)
      expect(google.isDone()).to.equal(true)

      const configRead = single(h.backend.callsTo('GET', CM_OAUTH_CONFIG))
      const configToken = String(configRead.headers.authorization).replace('Bearer ', '')
      const claims = await h.tokens.verifyScopedToken(configToken, TokenScopes.FETCH_CONFIG)
      expect(claims).to.include({ userId: ADMIN_ID, orgId: ORG_A })

      const stored = single(h.backend.callsTo('POST', CM_CREDENTIALS))
      const body = stored.body as Record<string, unknown>
      expect(body).to.include({
        access_token: 'google-access',
        refresh_token: 'google-refresh',
        enableRealTimeUpdates: true,
        topicName: 'projects/p/topics/t',
      })
      expect(body.access_token_expiry_time).to.be.within(before + 3600_000, Date.now() + 3600_000)
      expect(body.refresh_token_expiry_time).to.be.within(before + 7200_000, Date.now() + 7200_000)

      expect(findOne.firstCall.args[0]).to.deep.equal({ name: 'Google Workspace', orgId: ORG_A })
      expect(save.calledOnce).to.equal(true)
      const saved = save.firstCall.thisValue as { orgId: unknown; lastUpdatedBy: unknown; isEnabled: boolean }
      expect(String(saved.orgId)).to.equal(ORG_A)
      expect(String(saved.lastUpdatedBy)).to.equal(ADMIN_ID)
      expect(saved.isEnabled).to.equal(true)

      expect(h.entityEvents.published).to.have.length(1)
      const event = single(h.entityEvents.published)
      expect(event.eventType).to.equal('appEnabled')
      expect(event.payload).to.include({ orgId: ORG_A, appGroup: 'Google Workspace', appGroupId: 'cfg-1', syncAction: 'immediate' })
      expect(event.payload.apps).to.deep.equal(['DRIVE', 'GMAIL', 'CALENDAR'])
      expect(h.entityEvents.stopped).to.equal(h.entityEvents.started)
    })

    it('re-enables an existing connector for only the apps the user granted', async () => {
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      h.backend.on('POST', CM_CREDENTIALS, { status: 200, body: { ok: true } })
      googleGrants(`${GMAIL_SCOPE} ${DRIVE_SCOPE}`)
      const existing = {
        _id: 'cfg-7',
        name: 'Google Workspace',
        orgId: ORG_A,
        isEnabled: false,
        lastUpdatedBy: 'someone',
        save: sinon.stub().resolves(),
      }
      sinon.stub(ConnectorsConfig, 'findOne').resolves(existing as never)

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'consent-code' })

      expect(r.status).to.equal(200)
      expect(r.body.message).to.equal('Connector is now enabled')
      expect(existing.isEnabled).to.equal(true)
      expect(existing.lastUpdatedBy).to.equal(ADMIN_ID)
      expect(existing.save.calledOnce).to.equal(true)
      const event = single(h.entityEvents.published)
      expect(event.payload.apps).to.have.members(['GMAIL', 'DRIVE'])
      expect(event.payload).to.include({ orgId: ORG_A, appGroupId: 'cfg-7' })
    })

    it('refuses consent given from a different Google account and stores nothing', async () => {
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      googleGrants(DRIVE_SCOPE, 'someone.else@gmail.test')
      const findOne = sinon.stub(ConnectorsConfig, 'findOne').resolves(null)

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'consent-code' })

      expect(r.status).to.equal(400)
      expect(errorMessage(r)).to.equal('Account email is different from the consent-giving mail.')
      expect(h.backend.callsTo('POST', CM_CREDENTIALS)).to.have.length(0)
      expect(findOne.called).to.equal(false)
      expect(h.entityEvents.published).to.have.length(0)
    })

    for (const [missing, message] of [
      ['clientId', 'Client ID is missing'],
      ['clientSecret', 'Client secret is missing'],
    ] as const) {
      it(`stops before Google when the stored OAuth config has no ${missing}`, async () => {
        h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: { ...oauthConfig, [missing]: '' } })
        const google = googleGrants(DRIVE_SCOPE)

        const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'consent-code' })

        expect(r.status).to.equal(404)
        expect(errorMessage(r)).to.equal(message)
        expect(google.isDone()).to.equal(false)
      })
    }

    it('stops when the configuration manager cannot return the OAuth config', async () => {
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 500, body: { detail: 'etcd down' } })

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'consent-code' })

      expect(r.status).to.equal(500)
      expect(h.backend.callsTo('POST', CM_CREDENTIALS)).to.have.length(0)
    })

    it('stores nothing when Google rejects the code', async () => {
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      nock(GOOGLE).post('/token').reply(400, { error: 'invalid_grant' })

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'consent-code' })

      expect(r.status).to.be.at.least(400)
      expect(h.backend.callsTo('POST', CM_CREDENTIALS)).to.have.length(0)
      expect(h.entityEvents.published).to.have.length(0)
    })

    it('does not enable the connector when the tokens could not be stored', async () => {
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      h.backend.on('POST', CM_CREDENTIALS, { status: 500, body: { detail: 'write failed' } })
      googleGrants(DRIVE_SCOPE)
      const findOne = sinon.stub(ConnectorsConfig, 'findOne').resolves(null)

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'consent-code' })

      expect(r.status).to.equal(500)
      expect(findOne.called).to.equal(false)
      expect(h.entityEvents.published).to.have.length(0)
    })
  })

  describe('POST /internal/refreshIndividualConnectorToken', () => {
    const SENTINEL_JITTER = 0.123456
    let backoffDelays: number[]

    beforeEach(() => {
      backoffDelays = []
      // The handler waits 2^n s plus random jitter between Google attempts.
      // Pinning the jitter makes those waits recognisable, so only they are
      // skipped and every other timer keeps its real duration.
      sinon.stub(Math, 'random').returns(SENTINEL_JITTER)
      const realSetTimeout = globalThis.setTimeout
      sinon.stub(globalThis, 'setTimeout').callsFake(((fn: () => void, ms?: number) => {
        if (typeof ms === 'number' && Math.abs((ms % 1000) - SENTINEL_JITTER * 1000) < 1e-6) {
          backoffDelays.push(ms)
          return realSetTimeout(fn, 0)
        }
        return realSetTimeout(fn, ms)
      }) as typeof setTimeout)
    })

    const storedRefreshToken = () =>
      h.backend.on('GET', CM_INDIVIDUAL_CREDENTIALS, {
        status: 200,
        body: { refresh_token: 'stored-refresh', refresh_token_expiry_time: 1_900_000_000_000 },
      })

    const googleRefresh = () =>
      nock(GOOGLE).post('/token', {
        refresh_token: 'stored-refresh',
        client_id: 'google-client',
        client_secret: 'google-secret',
        grant_type: 'refresh_token',
      })

    it('only accepts an internal fetch-config token', async () => {
      const r = await call(h, 'POST', '/internal/refreshIndividualConnectorToken', sessionToken(h, admin))

      expect(r.status).to.equal(401)
      expect(h.backend.calls).to.have.length(0)
    })

    it('exchanges the stored refresh token and saves the new access token next to it', async () => {
      storedRefreshToken()
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      h.backend.on('POST', CM_CREDENTIALS, { status: 200, body: { ok: true } })
      googleRefresh().reply(200, { access_token: 'fresh-access', expires_in: 3600 })
      const token = fetchConfigToken(h, admin)
      const before = Date.now()

      const r = await call(h, 'POST', '/internal/refreshIndividualConnectorToken', token)

      expect(r.status).to.equal(200)
      expect(r.body.message).to.equal('Access token updated successfully')
      const stored = single(h.backend.callsTo('POST', CM_CREDENTIALS))
      expect(stored.headers.authorization).to.equal(`Bearer ${token}`)
      const body = stored.body as Record<string, unknown>
      expect(body).to.include({
        access_token: 'fresh-access',
        refresh_token: 'stored-refresh',
        refresh_token_expiry_time: 1_900_000_000_000,
        enableRealTimeUpdates: true,
      })
      expect(body.access_token_expiry_time).to.be.within(before + 3600_000, Date.now() + 3600_000)
      expect(backoffDelays).to.deep.equal([])
    })

    it('does not call Google when no refresh token is stored', async () => {
      h.backend.on('GET', CM_INDIVIDUAL_CREDENTIALS, { status: 200, body: {} })
      const google = googleRefresh().reply(200, { access_token: 'x', expires_in: 1 })

      const r = await call(h, 'POST', '/internal/refreshIndividualConnectorToken', fetchConfigToken(h, admin))

      expect(r.status).to.equal(500)
      expect(google.isDone()).to.equal(false)
    })

    it('retries Google after a backoff and succeeds on the second attempt', async () => {
      storedRefreshToken()
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      h.backend.on('POST', CM_CREDENTIALS, { status: 200, body: { ok: true } })
      googleRefresh().reply(400, { error: 'temporarily_unavailable' })
      googleRefresh().reply(200, { access_token: 'second-try', expires_in: 60 })

      const r = await call(h, 'POST', '/internal/refreshIndividualConnectorToken', fetchConfigToken(h, admin))

      expect(r.status).to.equal(200)
      expect(backoffDelays).to.deep.equal([2000 + SENTINEL_JITTER * 1000])
      const stored = single(h.backend.callsTo('POST', CM_CREDENTIALS))
      expect((stored.body as Record<string, unknown>).access_token).to.equal('second-try')
    })

    it('gives up after three failed attempts with growing waits and stores nothing', async () => {
      storedRefreshToken()
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      const google = googleRefresh().times(3).reply(400, { error: 'invalid_grant' })

      const r = await call(h, 'POST', '/internal/refreshIndividualConnectorToken', fetchConfigToken(h, admin))

      expect(r.status).to.be.at.least(400)
      expect(google.isDone()).to.equal(true)
      expect(backoffDelays).to.deep.equal([2000 + SENTINEL_JITTER * 1000, 4000 + SENTINEL_JITTER * 1000])
      expect(h.backend.callsTo('POST', CM_CREDENTIALS)).to.have.length(0)
    })

    it('reports a failure to save the new token', async () => {
      storedRefreshToken()
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
      h.backend.on('POST', CM_CREDENTIALS, { status: 500, body: { detail: 'write failed' } })
      googleRefresh().reply(200, { access_token: 'fresh-access', expires_in: 3600 })

      const r = await call(h, 'POST', '/internal/refreshIndividualConnectorToken', fetchConfigToken(h, admin))

      expect(r.status).to.equal(500)
    })

    it('stops when the OAuth config cannot be read', async () => {
      storedRefreshToken()
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 500, body: {} })
      const google = googleRefresh().reply(200, { access_token: 'x', expires_in: 1 })

      const r = await call(h, 'POST', '/internal/refreshIndividualConnectorToken', fetchConfigToken(h, admin))

      expect(r.status).to.equal(500)
      expect(google.isDone()).to.equal(false)
    })
  })

  describe('POST /updateAppConfig', () => {
    it('only accepts an internal fetch-config token', async () => {
      const load = sinon.stub(tokensConfig, 'loadAppConfig')

      const r = await call(h, 'POST', '/updateAppConfig', sessionToken(h, admin))

      expect(r.status).to.equal(401)
      expect(load.called).to.equal(false)
    })

    it('reloads the config so the legacy routes use the new configuration manager address', async () => {
      const reloaded = { ...h.config, cmBackend: `${h.backend.url}/moved`, frontendUrl: 'https://app.acme.test' }
      sinon.stub(tokensConfig, 'loadAppConfig').resolves(reloaded)
      h.backend.on('GET', `/moved${CM_OAUTH_CONFIG}`, { status: 200, body: { clientSecret: 'only-secret' } })

      const reload = await call(h, 'POST', '/updateAppConfig', fetchConfigToken(h, admin))
      const exchange = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'consent-code' })

      expect(reload.status).to.equal(200)
      expect(h.backend.callsTo('GET', `/moved${CM_OAUTH_CONFIG}`)).to.have.length(1)
      expect(errorMessage(exchange)).to.equal('Client ID is missing')
    })
  })
})
