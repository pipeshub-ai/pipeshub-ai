import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import nock from 'nock'
import express from 'express'
import http from 'http'
import fs from 'fs'
import path from 'path'
import yaml from 'js-yaml'
import jwt from 'jsonwebtoken'
import winston from 'winston'
import { Container } from 'inversify'
import type { AddressInfo } from 'net'
import { Logger } from '../../../../src/libs/services/logger.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema'
import { ConnectorsConfig } from '../../../../src/modules/configuration_manager/schema/connectors.schema'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import * as tokensConfig from '../../../../src/modules/tokens_manager/config/config'
import { AppConfig, registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'
import { SYNC_NOT_STARTED_MESSAGE, createConnectorRouter } from '../../../../src/modules/tokens_manager/routes/connectors.routes'
import { EntitiesEventProducer } from '../../../../src/modules/tokens_manager/services/entity_event.service'
import { FakeBackend, RecordingProducer, buildConfig } from './connectors-http-harness'

/**
 * POST /getTokenFromCode publishes the appEnabled event that asks for the sync. When it cannot be published the
 * request answers 503, logged once, and the user connects again; nothing reports a sync that was never asked for.
 */

type Row = Record<string, any>

const ORG = '64b000000000000000000a01'
const ADMIN = { _id: '64b0000000000000000000a1', orgId: ORG, email: 'ada.admin@acme.test', fullName: 'Ada Admin', role: 'admin' }
const GOOGLE = 'https://oauth2.googleapis.com'
const CM_OAUTH_CONFIG = '/api/v1/configurationManager/internal/connectors/googleWorkspaceOauthConfig'
const CM_CREDENTIALS = '/api/v1/configurationManager/internal/connectors/googleWorkspaceCredentials'

const level = (r: Row): string => {
  const l = String(r.level).toLowerCase()
  return l === 'warning' ? 'warn' : l
}
const query = (value: unknown): any => {
  const q: any = { select: () => q, lean: () => q, sort: () => q, exec: async () => value, then: (a: any, b: any) => Promise.resolve(value).then(a, b) }
  return q
}

describe('Connector routes: getTokenFromCode when the sync event cannot be published', function () {
  this.timeout(20000)
  const backend = new FakeBackend()
  const tokens = new AuthTokenService('sync-event-jwt', 'sync-event-scoped')
  let config: AppConfig
  let origin: string
  let server: http.Server
  let records: Row[]
  let published: unknown[]
  let brokerDown: boolean
  let disconnects: number

  before(async () => {
    await backend.start()
    config = buildConfig(backend.url)
  })
  after(async () => {
    await backend.stop()
  })

  beforeEach(async () => {
    records = []
    sinon.stub(winston.Logger.prototype, 'isLevelEnabled').returns(true)
    sinon.stub(winston.Logger.prototype, 'log').callsFake(function (this: unknown, info: Row) {
      records.push(info)
      return this
    } as any)
    registerAppUpstreams(config)
    backend.reset()
    published = []
    brokerDown = true
    disconnects = 0
    sinon.stub(Users, 'findOne').callsFake((() => query(ADMIN)) as any)
    sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as any)
    sinon.stub(tokensConfig, 'loadAppConfig').resolves({ frontendUrl: 'https://app.acme.test/' } as AppConfig)
    nock.disableNetConnect()
    nock.enableNetConnect('127.0.0.1')
    backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: { clientId: 'google-client', clientSecret: 'google-secret' } })
    backend.on('POST', CM_CREDENTIALS, { status: 200, body: { ok: true } })

    // The container's shared producer: publish reconnects by itself, as every broker's does.
    const broker = {
      isConnected: () => true,
      connect: async () => undefined,
      disconnect: async () => {
        disconnects += 1
      },
      publish: async (_topic: string, message: unknown) => {
        if (brokerDown) throw new Error('Connection is closed.')
        published.push(message)
      },
    }
    const container = new Container()
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens, () => ({}) as any))
    container.bind<AppConfig>('AppConfig').toConstantValue(config)
    container.bind('EntitiesEventProducer').toConstantValue(new EntitiesEventProducer(broker as any, Logger.getInstance({ service: 'sync-event-test' })))
    container.bind('SyncEventProducer').toConstantValue(new RecordingProducer())
    const crawling = new Container()
    crawling.bind(CrawlingSchedulerService).toConstantValue({} as CrawlingSchedulerService)
    const app = express()
    app.use(express.json())
    app.use('/api/v1/connectors', createConnectorRouter(container, crawling))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer((req, res) => void app(req, res))
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  afterEach(async () => {
    sinon.restore()
    nock.cleanAll()
    nock.enableNetConnect()
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    registerUpstreams({})
  })

  const googleGrants = (): void => {
    nock(GOOGLE).post('/token').reply(200, {
      access_token: 'ya29.access',
      refresh_token: '1//refresh',
      expires_in: 3600,
      scope: 'https://www.googleapis.com/auth/drive.readonly openid',
      id_token: jwt.sign({ email: ADMIN.email, sub: 'google-sub' }, 'google-signs-this'),
    })
  }
  const exchange = async (): Promise<{ status: number; body: Record<string, any> }> => {
    googleGrants()
    records.length = 0
    const token = tokens.generateToken({ userId: ADMIN._id, orgId: ORG, email: ADMIN.email, fullName: ADMIN.fullName, accountType: 'business', role: 'admin' })
    const res = await fetch(`${origin}/api/v1/connectors/getTokenFromCode`, {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: JSON.stringify({ tempCode: 'consent-code' }),
    })
    return { status: res.status, body: (await res.json()) as Record<string, any> }
  }
  const failureLines = (): Array<[string, string, unknown]> =>
    records.filter((r) => ['warn', 'error'].includes(level(r)) && r.message !== 'upstream.call.end').map((r) => [r.message, level(r), r.error_class])

  /** A ConnectorsConfig the route finds on its first read (null: first connect) and creates on its second. */
  const rows = (existing: Row | null): Array<{ isEnabled: boolean }> => {
    const saved: Array<{ isEnabled: boolean }> = []
    const findOne = sinon.stub(ConnectorsConfig, 'findOne')
    if (existing) {
      findOne.resolves(existing as never)
    } else {
      findOne.onFirstCall().resolves(null)
      findOne.resolves({ _id: 'cfg-1', name: 'Google Workspace', orgId: ORG, isEnabled: true } as never)
    }
    sinon.stub(ConnectorsConfig.prototype, 'save').callsFake(async function (this: { isEnabled: boolean }) {
      saved.push({ isEnabled: this.isEnabled })
      return this
    } as any)
    return saved
  }

  it('first connect: 503 with the reader message, logged once at WARN as unavailable; the created row is left for the retry', async () => {
    const saved = rows(null)

    const r = await exchange()

    expect(r.status).to.equal(503)
    expect(r.body.error.message).to.equal(SYNC_NOT_STARTED_MESSAGE)
    expect(published).to.have.length(0)
    expect(failureLines()).to.deep.equal([['http.request.error', 'warn', 'unavailable']])
    const failure = records.find((x) => x.message === 'http.request.error')
    expect(failure?.metadata.error.cause.message, 'the broker error is the cause').to.equal('Connection is closed.')
    // No rollback: nothing but this route reads the row, and connecting again republishes.
    expect(saved).to.deep.equal([{ isEnabled: true }])
  })

  it('reconnect: 503, and the existing connector is not switched on', async () => {
    const existing = { _id: 'cfg-7', name: 'Google Workspace', orgId: ORG, isEnabled: false, lastUpdatedBy: 'x', save: sinon.stub().resolves() }
    rows(existing)

    const r = await exchange()

    expect(r.status).to.equal(503)
    expect(r.body.error.message).to.equal(SYNC_NOT_STARTED_MESSAGE)
    expect(existing.isEnabled).to.equal(false)
    expect(existing.save.called).to.equal(false)
    expect(failureLines()).to.deep.equal([['http.request.error', 'warn', 'unavailable']])
  })

  it('connecting again once the broker is back takes the reconnect path, publishes once and answers 200', async () => {
    let row: Row | null = null
    const findOne = sinon.stub(ConnectorsConfig, 'findOne').callsFake((async () => row) as any)
    sinon.stub(ConnectorsConfig.prototype, 'save').callsFake(async function (this: Row) {
      row = { _id: 'cfg-1', name: 'Google Workspace', orgId: ORG, isEnabled: this.isEnabled, save: async () => undefined }
      return this
    } as any)

    expect((await exchange()).status).to.equal(503)
    brokerDown = false
    const retry = await exchange()

    expect(retry.status).to.equal(200)
    expect(retry.body.message).to.equal('Connector is now enabled')
    expect(published).to.have.length(1)
    expect(findOne.called).to.equal(true)
  })

  it('with the broker up, first connect answers 201 and reconnect 200, one event each, and the shared producer stays connected', async () => {
    brokerDown = false
    rows(null)
    expect((await exchange()).status).to.equal(201)
    sinon.restore()
    sinon.stub(winston.Logger.prototype, 'log').returns(undefined as any)
    sinon.stub(Users, 'findOne').callsFake((() => query(ADMIN)) as any)
    sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as any)
    sinon.stub(tokensConfig, 'loadAppConfig').resolves({ frontendUrl: 'https://app.acme.test/' } as AppConfig)
    const existing = { _id: 'cfg-7', name: 'Google Workspace', orgId: ORG, isEnabled: false, lastUpdatedBy: 'x', save: sinon.stub().resolves() }
    rows(existing)
    expect((await exchange()).status).to.equal(200)

    expect(published).to.have.length(2)
    expect(disconnects).to.equal(0)
  })

  it('the OpenAPI spec lists the 503', () => {
    const spec = yaml.load(
      fs.readFileSync(path.join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8'),
    ) as { paths: Record<string, any> }
    expect(Object.keys(spec.paths['/connectors/getTokenFromCode'].post.responses)).to.include('503')
  })
})
