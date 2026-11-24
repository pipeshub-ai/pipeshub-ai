import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import http from 'http'
import { Container } from 'inversify'
import type { AddressInfo } from 'net'
import { Logger } from '../../../../src/libs/services/logger.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../../src/libs/middlewares/access-log.middleware'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { runWithRequestContext } from '../../../../src/libs/context/request-context'
import { captureLogRecords, newRequestId, recordsOf } from '../../../helpers/log-capture'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { AppConfig, registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'
import { createConnectorRouter } from '../../../../src/modules/tokens_manager/routes/connectors.routes'
import { createOAuthRouter } from '../../../../src/modules/tokens_manager/routes/oauth.routes'
import { FakeBackend, RecordingProducer, buildConfig } from './connectors-http-harness'

/**
 * The connector and OAuth-config routes write no line of their own for a failure they pass on, and no
 * per-request INFO: the error middleware's line, the call line and the access line tell it, each with
 * the connector bound from the path (docs/logging.md, "Connector proxy (Node)").
 */

type Row = Record<string, any>

const ORG = '64b000000000000000000a01'
const ADMIN = { _id: '64b0000000000000000000a1', orgId: ORG, email: 'ada.admin@acme.test', fullName: 'Ada Admin', role: 'admin' }
const CONNECTOR = 'c0ffee00-1b4d-4c8e-9a6f-2d5e8b7c1a90'
const CLIENT_SECRET = 'client-secret-do-not-log-7731'
const INSTANCE_NAME = 'Ada private drive 5521'
const CONTROLLER_LOGGERS = new Set(['Connector Controller', 'OAuth Controller'])

const level = (r: Row): string => {
  const l = String(r.level).toLowerCase()
  return l === 'warning' ? 'warn' : l
}
const atLeastInfo = (r: Row): boolean => ['error', 'warn', 'info'].includes(level(r))

const query = (value: unknown): any => {
  const q: any = {
    select: () => q,
    lean: () => q,
    sort: () => q,
    exec: async () => value,
    then: (a: any, b: any) => Promise.resolve(value).then(a, b),
  }
  return q
}

describe('Connector routes: a request is logged once, with its connector', function () {
  this.timeout(20000)
  const backend = new FakeBackend()
  const tokens = new AuthTokenService('n5f-jwt', 'n5f-scoped')
  let config: AppConfig
  let origin: string
  let server: http.Server
  // Every record the process writes while a test runs; `mine()` is the current request's.
  let records: Row[]
  let requestId = ''

  before(async () => {
    await backend.start()
    config = buildConfig(backend.url)
  })
  after(async () => {
    await backend.stop()
  })

  beforeEach(async () => {
    records = captureLogRecords()
    registerAppUpstreams(config)
    backend.reset()
    sinon.stub(Users, 'findOne').callsFake((() => query(ADMIN)) as any)
    sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as any)

    const container = new Container()
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens, () => ({}) as any))
    container.bind<AppConfig>('AppConfig').toConstantValue(config)
    container.bind('EntitiesEventProducer').toConstantValue(new RecordingProducer())
    container.bind('SyncEventProducer').toConstantValue(new RecordingProducer())
    const crawling = new Container()
    crawling.bind(CrawlingSchedulerService).toConstantValue({
      removeJobsForConnector: async () => undefined,
      markReconcilePending: async () => undefined,
      clearReconcilePending: async () => undefined,
    } as unknown as CrawlingSchedulerService)

    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    app.use(express.json())
    app.use('/api/v1/connectors', createConnectorRouter(container, crawling))
    app.use('/api/v1/oauth', createOAuthRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer((req, res) => void app(req, res))
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  afterEach(async () => {
    sinon.restore()
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    registerUpstreams({})
  })

  const session = (): string =>
    tokens.generateToken({ userId: ADMIN._id, orgId: ORG, email: ADMIN.email, fullName: ADMIN.fullName, accountType: 'business', role: 'admin' })

  /** Sends one request and returns its status once the access line is written. */
  const send = async (method: string, path: string, body?: unknown): Promise<number> => {
    records.length = 0
    requestId = newRequestId('connectors-logging-once')
    const res = await fetch(`${origin}${path}`, {
      method,
      headers: {
        authorization: `Bearer ${session()}`,
        'x-request-id': requestId,
        ...(body === undefined ? {} : { 'content-type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    await res.text()
    const deadline = Date.now() + 5000
    while (Date.now() < deadline && !mine().some((r) => r.message === 'http.request.end')) {
      await new Promise((resolve) => setImmediate(resolve))
    }
    return res.status
  }
  const mine = (): Row[] => recordsOf(records, requestId)
  const visible = (): Row[] => mine().filter(atLeastInfo)
  const byMessage = (message: string): Row[] => mine().filter((r) => r.message === message)

  it('a connector-service 400 on a config save is the middleware line, the call line and the access line, each with connector_id', async () => {
    backend.on('PUT', `/api/v1/connectors/${CONNECTOR}/config/auth`, { status: 400, body: { detail: 'Base URL must start with https://' } })

    const status = await send('PUT', `/api/v1/connectors/${CONNECTOR}/config/auth`, { auth: { clientSecret: CLIENT_SECRET } })

    expect(status).to.equal(400)
    expect(visible().map((r) => [r.message, level(r)])).to.deep.equal([
      ['upstream.call.end', 'warn'],
      ['http.request.error', 'warn'],
      ['http.request.end', 'info'],
    ])
    expect(visible().map((r) => r.connector_id)).to.deep.equal([CONNECTOR, CONNECTOR, CONNECTOR])
    expect(byMessage('http.request.error')[0].error_class).to.equal('bad_request')
  })

  it('the connector service being down is no ERROR: its call lines, the middleware line and the access line, all WARN', async () => {
    backend.on('GET', '/api/v1/connectors/', 'drop')

    const status = await send('GET', '/api/v1/connectors/?scope=team')

    expect(status).to.equal(503)
    expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
    expect(byMessage('upstream.call.end').length).to.be.at.least(1)
    expect(visible().every((r) => level(r) === 'warn')).to.equal(true)
    expect(byMessage('http.request.error')[0].error_class).to.equal('unavailable')
  })

  it('a 404 for a connector is one WARN middleware line with connector_id, and nothing from the controller', async () => {
    backend.on('GET', '/api/v1/stats', { status: 404, body: { detail: 'Connector not found' } })

    const status = await send('GET', `/api/v1/connectors/${CONNECTOR}/stats`)

    expect(status).to.equal(404)
    const failure = byMessage('http.request.error')
    expect(failure.map((r) => [level(r), r.error_class, r.connector_id])).to.deep.equal([['warn', 'not_found', CONNECTOR]])
    expect(mine().filter((r) => CONTROLLER_LOGGERS.has(r.service))).to.deep.equal([])
  })

  // A save also writes its settings audit line, on the `audit` logger.
  for (const [name, method, path, backendMethod, backendPath, body, auditEvent] of [
    ['a read', 'GET', '/active', 'GET', '/api/v1/connectors/active', undefined, null],
    ['an instance read', 'GET', `/${CONNECTOR}`, 'GET', `/api/v1/connectors/${CONNECTOR}`, undefined, null],
    ['a delete', 'DELETE', `/${CONNECTOR}`, 'DELETE', `/api/v1/connectors/${CONNECTOR}`, undefined, 'config.connector.delete'],
    ['an agent toggle', 'POST', `/${CONNECTOR}/toggle`, 'POST', `/api/v1/connectors/${CONNECTOR}/toggle`, { type: 'agent' }, 'config.connector.update'],
    ['a create', 'POST', '/', 'POST', '/api/v1/connectors/', { connectorType: 'Jira', instanceName: INSTANCE_NAME, scope: 'team' }, 'config.connector.create'],
  ] as const) {
    it(`${name} that succeeds writes one line at INFO or above besides any audit line: the access line`, async () => {
      backend.on(backendMethod, backendPath, { status: 200, body: { success: true, connector: { _key: CONNECTOR }, connectors: [] } })

      const status = await send(method, `/api/v1/connectors${path}`, body)
      // The schedule removal after a delete runs in the background.
      await new Promise((resolve) => setTimeout(resolve, 20))

      expect(status).to.equal(200)
      expect(visible().map((r) => [r.service, r.message])).to.deep.equal([
        ...(auditEvent === null ? [] : [['audit', auditEvent]]),
        ['HttpAccess', 'http.request.end'],
      ])
      if (path.includes(CONNECTOR)) expect(visible().map((r) => r.connector_id)).to.deep.equal(visible().map(() => CONNECTOR))
    })
  }

  it('a background timer logging during the request (another run, or no context) is not counted as its line', async () => {
    backend.on('GET', '/api/v1/connectors/active', { status: 200, body: { connectors: [] } })
    const background = Logger.getInstance({ service: 'n5f-background' })
    // Like a chat run's heartbeat that outlived its test: fires on every turn of the event loop.
    const timers = [
      setInterval(() => background.warn('Failed to record run activity'), 0),
      runWithRequestContext({ rootId: 'some-other-run' }, () =>
        setInterval(() => background.warn('Failed to record run activity'), 0),
      ),
    ]
    try {
      expect(await send('GET', '/api/v1/connectors/active')).to.equal(200)
    } finally {
      timers.forEach(clearInterval)
    }

    expect(records.some((r) => r.message === 'Failed to record run activity')).to.equal(true)
    expect(visible().map((r) => r.message)).to.deep.equal(['http.request.end'])
  })

  it('a registry read names the connector type on its lines', async () => {
    backend.on('GET', '/api/v1/connectors/registry/Jira/schema', { status: 200, body: { schema: {} } })

    expect(await send('GET', '/api/v1/connectors/registry/Jira/schema')).to.equal(200)

    expect(visible().map((r) => [r.message, r.connector_type])).to.deep.equal([['http.request.end', 'Jira']])
  })

  it('a record read names the record on its lines', async () => {
    backend.on('GET', '/api/v1/records/rec-1/content', { status: 404, body: { detail: 'Record not found' } })

    expect(await send('GET', '/api/v1/connectors/record/rec-1/content')).to.equal(404)

    expect(visible().map((r) => [r.message, r.record_id])).to.deep.equal([
      ['upstream.call.end', 'rec-1'],
      ['http.request.error', 'rec-1'],
      ['http.request.end', 'rec-1'],
    ])
  })

  // Every route whose controller used to log a failure before passing it on. The fake service answers
  // 404 to anything not stubbed, so each one reaches its controller and fails there.
  for (const [method, path, body] of [
    ['GET', '/api/v1/connectors/registry', undefined],
    ['GET', '/api/v1/connectors/registry/Jira/schema', undefined],
    ['POST', '/api/v1/connectors/registry/Jira/test-connection', { auth: { token: 'x' } }],
    ['GET', '/api/v1/connectors/network/egress-ips', undefined],
    ['GET', '/api/v1/connectors/?scope=team', undefined],
    ['GET', '/api/v1/connectors/active', undefined],
    ['GET', '/api/v1/connectors/inactive', undefined],
    ['GET', '/api/v1/connectors/configured?scope=team', undefined],
    ['GET', '/api/v1/connectors/agents/active', undefined],
    ['POST', '/api/v1/connectors/', { connectorType: 'Jira', instanceName: INSTANCE_NAME, scope: 'team' }],
    ['GET', `/api/v1/connectors/${CONNECTOR}`, undefined],
    ['GET', `/api/v1/connectors/${CONNECTOR}/config`, undefined],
    ['PUT', `/api/v1/connectors/${CONNECTOR}/config`, { auth: {}, sync: {}, filters: {} }],
    ['PUT', `/api/v1/connectors/${CONNECTOR}/config/auth`, { auth: { clientSecret: CLIENT_SECRET } }],
    ['PUT', `/api/v1/connectors/${CONNECTOR}/config/filters-sync`, { filters: {} }],
    ['DELETE', `/api/v1/connectors/${CONNECTOR}`, undefined],
    ['GET', `/api/v1/connectors/${CONNECTOR}/oauth/authorize`, undefined],
    ['GET', '/api/v1/connectors/oauth/callback?code=oauth-code-x&state=st', undefined],
    ['GET', `/api/v1/connectors/${CONNECTOR}/filters`, undefined],
    ['POST', `/api/v1/connectors/${CONNECTOR}/filters`, { filters: { a: 1 } }],
    ['GET', `/api/v1/connectors/${CONNECTOR}/filters/labels/options`, undefined],
    ['POST', `/api/v1/connectors/${CONNECTOR}/toggle`, { type: 'agent' }],
    ['GET', `/api/v1/connectors/${CONNECTOR}/stats`, undefined],
    ['GET', '/api/v1/connectors/record/rec-1/content', undefined],
    ['GET', '/api/v1/connectors/navigate?nodeId=n-1&nodeType=record', undefined],
    ['GET', '/api/v1/connectors/record/lookup?identifiers=abc', undefined],
    ['POST', `/api/v1/connectors/${CONNECTOR}/reindex`, {}],
    ['POST', `/api/v1/connectors/${CONNECTOR}/sync/stop`, undefined],
    ['POST', `/api/v1/connectors/${CONNECTOR}/resync`, { connectorName: 'Jira' }],
    ['POST', '/api/v1/connectors/vector-store/cleanup', {}],
    ['POST', '/api/v1/connectors/vector-store/reindex', {}],
    ['GET', '/api/v1/oauth/registry', undefined],
    ['GET', '/api/v1/oauth/registry/Jira', undefined],
    ['GET', '/api/v1/oauth/', undefined],
    ['GET', '/api/v1/oauth/Jira', undefined],
    ['POST', '/api/v1/oauth/Jira', { oauthInstanceName: 'app', config: { clientId: 'c', clientSecret: CLIENT_SECRET } }],
    ['GET', '/api/v1/oauth/Jira/cfg-1', undefined],
    ['PUT', '/api/v1/oauth/Jira/cfg-1', { config: { clientSecret: CLIENT_SECRET } }],
    ['DELETE', '/api/v1/oauth/Jira/cfg-1', undefined],
  ] as const) {
    it(`${method} ${path}: a refused call reaches the service and the controller writes nothing`, async () => {
      const status = await send(method, path, body)

      expect(backend.calls.length, 'the request must reach the connector service').to.be.at.least(1)
      expect(status).to.be.at.least(400)
      // The deprecated POST /filters still warns that it was called; that is not a failure line.
      const own = mine().filter(
        (r) => CONTROLLER_LOGGERS.has(r.service) && atLeastInfo(r) && !String(r.message).startsWith('Deprecated POST'),
      )
      expect(own).to.deep.equal([])
      expect(byMessage('http.request.error')).to.have.length(1)
      const text = JSON.stringify(records)
      expect(text).to.not.include(CLIENT_SECRET)
      expect(text).to.not.include(INSTANCE_NAME)
      expect(text).to.not.include(ADMIN.email)
    })
  }
})
