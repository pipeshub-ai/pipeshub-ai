import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import http from 'http'
import { Container } from 'inversify'
import type { AddressInfo } from 'net'
import { Logger } from '../../../src/libs/services/logger.service'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../src/libs/services/authtoken.service'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'
import { LOG_CONTEXT_KEYS } from '../../../src/libs/context/request-context'
import { captureLogRecords, newRequestId, recordsOf } from '../../helpers/log-capture'
import { Users } from '../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema'
import { AppConfig, registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config'
import { createToolsetsRouter } from '../../../src/modules/toolsets/routes/toolsets_routes'
import { createMcpServersRouter } from '../../../src/modules/mcp_servers/routes/mcp_servers.routes'
import { createApiToolsetsRouter } from '../../../src/modules/api_toolsets/routes/api_toolsets.routes'
import { FakeBackend, buildConfig } from '../tokens_manager/routes/connectors-http-harness'

/**
 * The toolset, MCP server and API toolset proxies write no line of their own: a request is its call lines, the
 * error middleware's one failure line and the access line, each naming the instance and agent from the path
 * (docs/logging.md, "Toolset and MCP server proxies (Node)").
 */

type Row = Record<string, any>

const ORG = '64b000000000000000000a01'
const ADMIN = { _id: '64b0000000000000000000a1', orgId: ORG, email: 'ada.admin@acme.test', fullName: 'Ada Admin', role: 'admin' }
const TOOLSET = '7f3c2a10-5b6e-4c1d-8e9f-0a1b2c3d4e5f'
const AGENT = 'a9e1b2c3-0d4e-4f5a-8b6c-7d8e9f0a1b2c'
const MCP_SERVER = 'm1c2p3a4-5b6c-4d7e-8f90-a1b2c3d4e5f6'
const API_TOKEN = 'api-token-do-not-log-4410'
const INSTANCE_NAME = 'Ada ops Jira 7731'
// The only lines a proxied request writes at INFO or above.
const REQUEST_LINES = new Set(['upstream.call.end', 'http.request.error', 'http.request.end'])

const level = (r: Row): string => {
  const l = String(r.level).toLowerCase()
  return l === 'warning' ? 'warn' : l
}
const atLeastInfo = (r: Row): boolean => ['error', 'warn', 'info'].includes(level(r))
// The path ids a line carries; org_id and user_id come from the session, not the path.
const pathIds = (r: Row): Row =>
  Object.fromEntries(LOG_CONTEXT_KEYS.filter((key) => r[key] !== undefined).map((key) => [key, r[key]]))

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

describe('Toolset, MCP server and API toolset proxies: a request is logged once, with its instance', function () {
  this.timeout(20000)
  const backend = new FakeBackend()
  const tokens = new AuthTokenService('n5e-jwt', 'n5e-scoped')
  let config: AppConfig
  let origin: string
  let server: http.Server
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

    const app = express()
    app.use(requestContextMiddleware)
    app.use(accessLog())
    app.use(express.json())
    app.use('/api/v1/toolsets', createToolsetsRouter(container))
    app.use('/api/v1/mcp-servers', createMcpServersRouter(container))
    app.use('/api/v1/api-toolsets', createApiToolsetsRouter(container))
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
    requestId = newRequestId('toolsets-logging-once')
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
  // The proxy's DEBUG line names the path; the user is the line's own `user_id`, not a second copy in the metadata.
  const proxyDebugUserIds = (prefix: string): unknown[] =>
    mine()
      .filter((r) => String(r.message).startsWith(prefix))
      .map((r) => r.metadata?.userId)

  // Every toolset route: each handler used to log its failure at ERROR before passing it on. The connector
  // service's path is the route's own path under /api/v1/toolsets.
  for (const [method, path, body] of [
    ['GET', '/registry', undefined],
    ['GET', '/registry/jira/schema', undefined],
    ['POST', '/', { name: 'jira', auth: { type: 'API_TOKEN', apiToken: API_TOKEN } }],
    ['GET', '/configured', undefined],
    ['GET', '/jira/status', undefined],
    ['GET', '/jira/config', undefined],
    ['POST', '/jira/config', { auth: { type: 'API_TOKEN', apiToken: API_TOKEN } }],
    ['PUT', '/jira/config', { auth: { type: 'API_TOKEN', apiToken: API_TOKEN } }],
    ['DELETE', '/jira/config', undefined],
    ['POST', '/jira/reauthenticate', undefined],
    ['GET', '/jira/oauth/authorize', undefined],
    ['GET', '/oauth/callback?code=oauth-code-x&state=st', undefined],
    ['GET', '/my-toolsets', undefined],
    ['GET', '/instances', undefined],
    ['POST', '/instances', { instanceName: INSTANCE_NAME, toolsetType: 'jira', authType: 'API_TOKEN' }],
    ['GET', `/instances/${TOOLSET}`, undefined],
    ['PUT', `/instances/${TOOLSET}`, { instanceName: INSTANCE_NAME }],
    ['DELETE', `/instances/${TOOLSET}`, undefined],
    ['POST', `/instances/${TOOLSET}/authenticate`, { auth: { apiToken: API_TOKEN } }],
    ['PUT', `/instances/${TOOLSET}/credentials`, { auth: { apiToken: API_TOKEN } }],
    ['DELETE', `/instances/${TOOLSET}/credentials`, undefined],
    ['POST', `/instances/${TOOLSET}/reauthenticate`, undefined],
    ['GET', `/instances/${TOOLSET}/oauth/authorize`, undefined],
    ['GET', `/instances/${TOOLSET}/status`, undefined],
    ['GET', `/instances/${TOOLSET}/tool-rules`, undefined],
    ['PUT', `/instances/${TOOLSET}/tool-rules`, { tools: { search: 'allow' } }],
    ['GET', '/oauth-configs/jira', undefined],
    ['PUT', '/oauth-configs/jira/cfg-1', { config: { clientId: 'c' } }],
    ['DELETE', '/oauth-configs/jira/cfg-1', undefined],
    ['GET', `/agents/${AGENT}`, undefined],
    ['POST', `/agents/${AGENT}/instances/${TOOLSET}/authenticate`, { auth: { apiToken: API_TOKEN } }],
    ['PUT', `/agents/${AGENT}/instances/${TOOLSET}/credentials`, { auth: { apiToken: API_TOKEN } }],
    ['DELETE', `/agents/${AGENT}/instances/${TOOLSET}/credentials`, undefined],
    ['POST', `/agents/${AGENT}/instances/${TOOLSET}/reauthenticate`, undefined],
    ['GET', `/agents/${AGENT}/instances/${TOOLSET}/oauth/authorize`, undefined],
  ] as const) {
    it(`${method} ${path}: a connector-service 500 is one failure line, and the controller writes nothing`, async () => {
      const backendPath = `/api/v1/toolsets${path}`.split('?')[0]
      backend.on(method, backendPath, { status: 500, body: { detail: 'Internal Server Error' } })

      const status = await send(method, `/api/v1/toolsets${path}`, body)

      expect(backend.callsTo(method, backendPath).length, 'the request must reach the connector service').to.be.at.least(1)
      expect(status).to.equal(502)
      expect(visible().filter((r) => !REQUEST_LINES.has(r.message))).to.deep.equal([])
      expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
      expect(byMessage('http.request.error').map((r) => [level(r), r.error_class])).to.deep.equal([['warn', 'upstream_5xx']])
      const text = JSON.stringify(records)
      expect(text).to.not.include(API_TOKEN)
      expect(text).to.not.include(INSTANCE_NAME)
      // Only the instance and agent are bound: the legacy routes' `toolsetId` and the `toolsetType` are not instance ids.
      const ids = { ...(path.includes(TOOLSET) ? { toolset_id: TOOLSET } : {}), ...(path.includes(AGENT) ? { agent_id: AGENT } : {}) }
      expect(visible().map(pathIds)).to.deep.equal(visible().map(() => ids))
    })
  }

  it('a 404 for a toolset instance is the call, failure and access lines, each with toolset_id, and no ERROR', async () => {
    backend.on('GET', `/api/v1/toolsets/instances/${TOOLSET}`, { status: 404, body: { detail: 'Toolset instance not found' } })

    expect(await send('GET', `/api/v1/toolsets/instances/${TOOLSET}`)).to.equal(404)

    expect(visible().map((r) => [r.message, level(r), r.toolset_id])).to.deep.equal([
      ['upstream.call.end', 'warn', TOOLSET],
      ['http.request.error', 'warn', TOOLSET],
      ['http.request.end', 'info', TOOLSET],
    ])
    expect(byMessage('http.request.error')[0].error_class).to.equal('not_found')
  })

  it('the connector service being down is no ERROR, and every line names the toolset', async () => {
    backend.on('GET', `/api/v1/toolsets/instances/${TOOLSET}/status`, 'drop')

    expect(await send('GET', `/api/v1/toolsets/instances/${TOOLSET}/status`)).to.equal(503)

    expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
    expect(byMessage('upstream.call.end').length).to.be.at.least(1)
    expect(visible().every((r) => r.toolset_id === TOOLSET)).to.equal(true)
    expect(byMessage('http.request.error').map((r) => r.error_class)).to.deep.equal(['unavailable'])
  })

  for (const [name, method, path, body, fields] of [
    ['a registry read', 'GET', '/registry', undefined, {}],
    ['an instance read', 'GET', `/instances/${TOOLSET}`, undefined, { toolset_id: TOOLSET }],
    ['an agent read', 'GET', `/agents/${AGENT}`, undefined, { agent_id: AGENT }],
    [
      "an agent's credential update",
      'PUT',
      `/agents/${AGENT}/instances/${TOOLSET}/credentials`,
      { auth: { apiToken: API_TOKEN } },
      { toolset_id: TOOLSET, agent_id: AGENT },
    ],
  ] as const) {
    it(`${name} that succeeds writes one line at INFO or above, the access line, with the path's ids`, async () => {
      backend.on(method, `/api/v1/toolsets${path}`, { status: 200, body: { success: true } })

      expect(await send(method, `/api/v1/toolsets${path}`, body)).to.equal(200)

      const lines = visible()
      expect(lines.map((r) => r.message)).to.deep.equal(['http.request.end'])
      expect({ toolset_id: lines[0].toolset_id, agent_id: lines[0].agent_id }).to.deep.equal({
        toolset_id: undefined,
        agent_id: undefined,
        ...fields,
      })
    })
  }

  for (const mount of ['toolsets', 'mcp-servers', 'api-toolsets']) {
    it(`${mount}: a refused path segment is never bound: 400 before the connector service, no instance id on any line`, async () => {
      expect(await send('GET', `/api/v1/${mount}/instances/..%2Fadmin`)).to.equal(400)

      expect(backend.calls).to.deep.equal([])
      expect(mine().map(pathIds).filter((ids) => Object.keys(ids).length > 0)).to.deep.equal([])
      expect(byMessage('http.request.error').map((r) => r.error_class)).to.deep.equal(['bad_request'])
    })
  }

  for (const [method, path, fields] of [
    ['GET', `/instances/${MCP_SERVER}`, { mcp_server_id: MCP_SERVER }],
    ['DELETE', `/agents/${AGENT}/instances/${MCP_SERVER}/credentials`, { mcp_server_id: MCP_SERVER, agent_id: AGENT }],
  ] as const) {
    it(`MCP servers ${method} ${path}: a connector-service 500 is one WARN failure line, each line with the server`, async () => {
      backend.on(method, `/api/v1/mcp-servers${path}`, { status: 500, body: { detail: 'Internal Server Error' } })

      expect(await send(method, `/api/v1/mcp-servers${path}`)).to.equal(502)

      expect(visible().filter((r) => !REQUEST_LINES.has(r.message))).to.deep.equal([])
      expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
      expect(byMessage('http.request.error').map((r) => [level(r), r.error_class])).to.deep.equal([['warn', 'upstream_5xx']])
      expect(proxyDebugUserIds('MCP servers proxy:')).to.deep.equal([undefined])
      for (const line of visible()) {
        expect({ mcp_server_id: line.mcp_server_id, agent_id: line.agent_id, toolset_id: line.toolset_id }).to.deep.equal({
          mcp_server_id: undefined,
          agent_id: undefined,
          toolset_id: undefined,
          ...fields,
        })
      }
    })
  }

  it('API toolsets: a connector-service 500 is one WARN failure line, each line with toolset_id', async () => {
    backend.on('GET', `/api/v1/api-toolsets/instances/${TOOLSET}`, { status: 500, body: { detail: 'Internal Server Error' } })

    expect(await send('GET', `/api/v1/api-toolsets/instances/${TOOLSET}`)).to.equal(502)

    expect(visible().filter((r) => !REQUEST_LINES.has(r.message))).to.deep.equal([])
    expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
    expect(byMessage('http.request.error').map((r) => [level(r), r.error_class])).to.deep.equal([['warn', 'upstream_5xx']])
    expect(visible().map((r) => r.toolset_id)).to.deep.equal([TOOLSET, TOOLSET, TOOLSET])
    expect(proxyDebugUserIds('API toolsets proxy:')).to.deep.equal([undefined])
  })
})
