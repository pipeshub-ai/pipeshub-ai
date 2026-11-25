import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import http from 'http'
import type { AddressInfo } from 'net'
import type { NextFunction, Request, RequestHandler, Response } from 'express'
import { Container } from 'inversify'
import { createConfigurationManagerRouter } from '../../../src/modules/configuration_manager/routes/cm_routes'
import { auditedConfigStore, configSection } from '../../../src/modules/configuration_manager/utils/config-audit'
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths'
import { loadConfigurationManagerConfig } from '../../../src/modules/configuration_manager/config/config'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { setContextIdentity } from '../../../src/libs/context/request-context'
import { makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'
import { EncryptionService } from '../../../src/libs/encryptor/encryptor'
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service'
import { KeyValueStoreFactory } from '../../../src/libs/keyValueStore/keyValueStoreFactory'
import { McpAccessPolicyStore } from '../../../src/modules/mcp/access-policy'
import * as userAdminService from '../../../src/modules/user_management/services/user-admin.service'
import * as orgUtils from '../../../src/modules/user_management/utils/org.utils'
import * as smtpTransport from '../../../src/modules/mail/utils/smtp-transport'
import * as s3HealthCheck from '../../../src/modules/storage/utils/s3-health-check.util'
import { registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'
import { captureLogRecords, inRequestContext, newRequestId, recordsOf, type LogRecord } from '../../helpers/log-capture'
import { FakeBackend, buildConfig } from '../tokens_manager/routes/connectors-http-harness'
import { CONTROL_PLANE_DEPLOYMENT_REVISION, CONTROL_PLANE_TRANSACTION } from '../../../src/modules/agent-harness/composition'
import { testControlPlane } from '../control-plane/support/in-memory'
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract'

// Every admin config write is one INFO `config.<section>.update|delete` line on the `audit` logger, with the admin as
// `actor`, written after the store accepted it. Service-token (`/internal/`) writes, refused writes and failed writes
// have none, and no line names a stored value.

const ORG = '64c000000000000000000c01'
const ADMIN = '64c0000000000000000000c1'
const SECRET = 'n5a2-secret-marker-7f3c'
const CONNECTION = '3f2b8c1e-9a4d-4c7b-8e2f-1a2b3c4d5e6f'

class MemoryKvStore {
  values = new Map<string, string>()
  async get<T>(key: string): Promise<T | null> {
    return (this.values.get(key) ?? null) as T | null
  }
  async set(key: string, value: string): Promise<void> {
    this.values.set(key, value)
  }
  async compareAndSet<T>(key: string, expected: T | null, next: T): Promise<boolean> {
    if ((this.values.get(key) ?? null) !== expected) return false
    this.values.set(key, next as unknown as string)
    return true
  }
  async delete(key: string): Promise<void> {
    this.values.delete(key)
  }
  isConnected(): boolean {
    return true
  }
}

const events = () => ({ start: sinon.stub().resolves(), publishEvent: sinon.stub().resolves(), stop: sinon.stub().resolves() })

// What the real auth middleware does after verifying the token: the user, the log identity and the org context.
const signedIn: RequestHandler = (req: Request, _res: Response, next: NextFunction) => {
  ;(req as any).user = { userId: ADMIN, orgId: ORG, email: 'ada.admin@acme.test', fullName: 'Ada Admin' }
  setContextIdentity({ orgId: ORG, userId: ADMIN })
  runWithOrgContext(makeOrgContext({ orgId: ORG, userId: ADMIN, kind: 'user', source: 'jwt' }), next)
}
const serviceToken: RequestHandler = (req: Request, _res: Response, next: NextFunction) => {
  ;(req as any).tokenPayload = { userId: ADMIN, orgId: ORG }
  runWithOrgContext(makeOrgContext({ orgId: ORG, userId: ADMIN, kind: 'service', source: 'scoped_jwt' }), next)
}

interface Served {
  origin: string
  close: () => Promise<void>
}

async function serve(kv: unknown, backendUrl: string): Promise<Served> {
  const container = new Container()
  container.bind('KeyValueStoreService').toConstantValue(kv)
  container.bind('AppConfig').toConstantValue({ ...buildConfig(backendUrl), communicationBackend: backendUrl })
  container.bind('AiConfigEventProducer').toConstantValue(events())
  container.bind('SyncEventProducer').toConstantValue(events())
  container.bind('ConfigService').toConstantValue({ updateConfig: sinon.stub().resolves({ statusCode: 200 }) })
  container.bind('AuthMiddleware').toConstantValue({ authenticate: signedIn, scopedTokenValidator: () => serviceToken })
  // An AI-model or platform-settings save is a control-plane write (an AI-model save also moves the deployment
  // revision); the app uses the real stores, here in-memory ones.
  container.bind(CONTROL_PLANE_TRANSACTION).toConstantValue(testControlPlane())
  container.bind(CONTROL_PLANE_DEPLOYMENT_REVISION).toConstantValue(new InMemoryHeadRevisions())
  const app = express()
  app.use(requestContextMiddleware)
  app.use(accessLog())
  app.use(express.json())
  app.use('/api/v1/configurationManager', createConfigurationManagerRouter(container))
  app.use(ErrorMiddleware.handleError())
  const server = await new Promise<http.Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening))
  })
  return {
    origin: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    close: async () => {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    },
  }
}

const audits = (lines: LogRecord[]): LogRecord[] => lines.filter((r) => r.service === 'audit')
const written = (lines: LogRecord[]): string => JSON.stringify(lines)
// Lines at INFO or above that are neither the audit line nor the access line.
const otherLines = (lines: LogRecord[]): string[] =>
  lines
    .filter((x) => x.service !== 'audit' && x.message !== 'http.request.end' && ['info', 'warn', 'warning', 'error'].includes(String(x.level)))
    .map((x) => `${x.service}: ${x.message}`)

describe('Configuration audit events', function () {
  this.timeout(60000)
  let records: LogRecord[]

  let savedSecretKey: string | undefined
  before(() => {
    savedSecretKey = process.env.SECRET_KEY
    process.env.SECRET_KEY = process.env.SECRET_KEY || '5cb4705285c05446326184a38387e7091ebf91ba776c58724a2fa4543d34193b'
  })
  after(() => {
    if (savedSecretKey === undefined) delete process.env.SECRET_KEY
    else process.env.SECRET_KEY = savedSecretKey
  })
  beforeEach(() => {
    records = captureLogRecords()
  })
  afterEach(() => sinon.restore())

  describe('configSection', () => {
    const cases: Array<[string, string]> = [
      [configPaths.smtp, 'smtp'],
      [configPaths.storageService, 'storage'],
      [configPaths.auth.microsoft, 'auth_microsoft'],
      [configPaths.auth.google, 'auth_google'],
      [configPaths.auth.azureAD, 'auth_azure_ad'],
      [configPaths.auth.sso, 'auth_sso'],
      [configPaths.auth.oauth, 'auth_oauth'],
      [configPaths.auth.github, 'auth_github'],
      [configPaths.inheritance, 'inheritance'],
      [configPaths.platform.settings, 'platform'],
      [configPaths.slackBot, 'slack_bot'],
      [configPaths.systemPrompts, 'system_prompt'],
      [configPaths.aiModels, 'ai_models'],
      [configPaths.webSearch, 'web_search'],
      [configPaths.endpoint, 'public_urls'],
      [configPaths.metricsCollection, 'metrics'],
      [configPaths.aiGatewayIdentityPolicy, 'ai_identity_policy'],
      [`${configPaths.mcpAccessPolicy}/${ORG}`, 'mcp_access'],
      [`${configPaths.connectors.googleWorkspace.credentials.business}/${ORG}`, 'connector_google_workspace'],
      [configPaths.connectors.googleWorkspace.config, 'connector_google_workspace'],
      [configPaths.connectors.atlassian.config, 'connector_atlassian'],
      [configPaths.connectors.onedrive.config, 'connector_onedrive'],
      [configPaths.connectors.sharepoint.config, 'connector_sharepoint'],
      // A key below a section is that section.
      [configPaths.aiModelsEmbedding, 'ai_models'],
      [`${configPaths.slackBot}/bot-1`, 'slack_bot'],
      // A near miss is not the section; an unlisted path is `other`.
      ['/services/smtpX', 'other'],
      ['/services/storage-v2', 'other'],
      [configPaths.auth.base, 'other'],
      [configPaths.connectors.googleWorkspace.base.slice(0, -1), 'other'],
      [configPaths.secretKeys, 'other'],
      ['', 'other'],
    ]
    for (const [path, section] of cases) {
      it(`${path || '(empty)'} -> ${section}`, () => {
        expect(configSection(path)).to.equal(section)
      })
    }
  })

  describe('auditedConfigStore', () => {
    const run = <T>(fn: () => Promise<T>): Promise<T> => inRequestContext(id, fn)
    let id: string
    let kv: MemoryKvStore
    let store: KeyValueStoreService
    beforeEach(() => {
      id = newRequestId('n5a2')
      kv = new MemoryKvStore()
      store = auditedConfigStore(kv as unknown as KeyValueStoreService)
    })
    const lines = () => audits(recordsOf(records, id)).map((r) => [r.level, r.message, r.outcome])

    it('a set the store accepted is one INFO update line, after the write', async () => {
      const spy = sinon.spy(kv, 'set')
      await run(() => store.set(configPaths.smtp, SECRET))
      expect(spy.calledOnceWithExactly(configPaths.smtp, SECRET)).to.equal(true)
      expect(lines()).to.deep.equal([['info', 'config.smtp.update', 'success']])
      expect(written(recordsOf(records, id))).to.not.contain(SECRET)
      expect(written(recordsOf(records, id))).to.not.contain(configPaths.smtp)
    })

    it('a delete the store accepted is one delete line', async () => {
      kv.values.set(configPaths.smtp, 'x')
      await run(() => store.delete(configPaths.smtp))
      expect(kv.values.has(configPaths.smtp)).to.equal(false)
      expect(lines()).to.deep.equal([['info', 'config.smtp.delete', 'success']])
    })

    it('a rejected set, delete or compare-and-set writes no line and the rejection reaches the caller', async () => {
      const down = new Error('etcd down')
      sinon.stub(kv, 'set').rejects(down)
      sinon.stub(kv, 'delete').rejects(down)
      sinon.stub(kv, 'compareAndSet').rejects(down)
      const thrown: unknown[] = []
      await run(() => store.set(configPaths.smtp, SECRET)).catch((e) => thrown.push(e))
      await run(() => store.delete(configPaths.smtp)).catch((e) => thrown.push(e))
      await run(() => store.compareAndSet(configPaths.slackBot, null, 'next')).catch((e) => thrown.push(e))
      expect(thrown).to.deep.equal([down, down, down])
      expect(lines()).to.deep.equal([])
    })

    it('a compare-and-set writes a line only when it wrote, and answers what the store answered', async () => {
      kv.values.set(configPaths.slackBot, 'current')
      expect(await run(() => store.compareAndSet(configPaths.slackBot, 'stale', 'next'))).to.equal(false)
      expect(lines()).to.deep.equal([])
      expect(await run(() => store.compareAndSet(configPaths.slackBot, 'current', 'next'))).to.equal(true)
      expect(kv.values.get(configPaths.slackBot)).to.equal('next')
      expect(lines()).to.deep.equal([['info', 'config.slack_bot.update', 'success']])
    })

    it('a passthrough runs on the real store, so a store with #private fields works through the wrapper', async () => {
      class PrivateFieldStore {
        #values = new Map<string, string>()
        async get(key: string): Promise<string | null> {
          return this.#values.get(key) ?? null
        }
        async set(key: string, value: string): Promise<void> {
          this.#values.set(key, value)
        }
        async delete(key: string): Promise<void> {
          this.#values.delete(key)
        }
        async compareAndSet(): Promise<boolean> {
          return false
        }
      }
      const wrapped = auditedConfigStore(new PrivateFieldStore() as unknown as KeyValueStoreService)
      await run(() => wrapped.set(configPaths.smtp, 'stored'))
      expect(await run(() => wrapped.get(configPaths.smtp))).to.equal('stored')
      expect(lines()).to.deep.equal([['info', 'config.smtp.update', 'success']])
    })

    it('reads pass through and write nothing', async () => {
      kv.values.set(configPaths.smtp, 'stored')
      expect(await run(() => store.get(configPaths.smtp))).to.equal('stored')
      expect(lines()).to.deep.equal([])
    })

    it('a reconnect through the wrapper reconnects the real store, which stays a KeyValueStoreService', async () => {
      const first = { healthCheck: sinon.stub().resolves(true), getKey: sinon.stub().resolves('from-first') }
      const second = { healthCheck: sinon.stub().resolves(true), getKey: sinon.stub().resolves('from-second') }
      sinon.stub(KeyValueStoreFactory, 'createStore').onFirstCall().returns(first as never).onSecondCall().returns(second as never)
      // Not the process singleton: a store of its own, built the way getInstance builds one.
      const real = new (KeyValueStoreService as any)(loadConfigurationManagerConfig()) as KeyValueStoreService
      const wrapped = auditedConfigStore(real)
      expect(wrapped).to.be.instanceOf(KeyValueStoreService)
      // Own write keys only: an `in` check would answer Object.prototype's `constructor`.
      expect(wrapped.constructor).to.not.equal(Object)
      await wrapped.connect()
      await wrapped.disconnect()
      expect(real.isConnected()).to.equal(false)
      expect(await wrapped.get('k')).to.equal('from-second')
      expect(real.isConnected()).to.equal(true)
      expect((real as any).store).to.equal(second)
      expect(await real.get('k')).to.equal('from-second')
    })
  })

  describe('admin config routes over HTTP', () => {
    const backend = new FakeBackend()
    let kv: MemoryKvStore
    let mcpStore: MemoryKvStore
    let s: Served

    const encrypt = (value: unknown): string => {
      const cfg = loadConfigurationManagerConfig()
      return EncryptionService.getInstance(cfg.algorithm, cfg.secretKey).encrypt(JSON.stringify(value))
    }

    /** The settings the routes below change or need: SMTP, two models (one on a connection), two web search providers. */
    const seededStore = (): MemoryKvStore => {
      const kv = new MemoryKvStore()
      kv.values.set(configPaths.smtp, encrypt({ host: 'smtp.acme.test', port: 587, fromEmail: 'noreply@acme.test', password: SECRET }))
      kv.values.set(
        configPaths.aiModels,
        encrypt({
          llm: [
            { modelKey: 'mk-1', provider: 'openAI', isDefault: true, connectionId: CONNECTION, configuration: { model: 'gpt-4o', apiKey: 'old-key' } },
            { modelKey: 'mk-2', provider: 'openAI', isDefault: false, configuration: { model: 'gpt-4o-mini', apiKey: 'old-key' } },
          ],
          embedding: [],
        }),
      )
      kv.values.set(
        configPaths.webSearch,
        encrypt({
          providers: [
            { provider: 'serper', providerKey: 'key-serper', configuration: { apiKey: 'old-key' }, isDefault: true },
            { provider: 'exa', providerKey: 'key-exa', configuration: { apiKey: 'old-key' }, isDefault: false },
          ],
        }),
      )
      kv.values.set(
        `${configPaths.connectors.googleWorkspace.credentials.business}/${ORG}`,
        encrypt({ type: 'service_account', client_id: 'n5a2-client', adminEmail: 'it@acme.test' }),
      )
      return kv
    }

    before(() => backend.start())
    after(() => backend.stop())
    beforeEach(async () => {
      backend.reset()
      registerAppUpstreams(buildConfig(backend.url))
      for (const path of ['/api/v1/health-check/llm', '/api/v1/llm-health-check', '/api/v1/web-search-health-check', '/api/v1/mail/updateSmtpConfig']) {
        backend.on('POST', path, { status: 200, body: { status: 'healthy' } })
      }
      const noAgents = { status: 200, body: { success: true, agents: [] } }
      backend.on('GET', '/api/v1/agent/model-usage/mk-1', noAgents)
      backend.on('GET', '/api/v1/agent/web-search-usage/exa', noAgents)
      sinon.stub(userAdminService, 'isUserOrgAdmin').resolves(true)
      sinon.stub(orgUtils, 'findActiveOrgById').resolves({ accountType: 'business' } as never)
      sinon.stub(smtpTransport, 'smtpSettingsProblem').resolves(null)
      mcpStore = new MemoryKvStore()
      sinon.stub(McpAccessPolicyStore, 'store').returns(mcpStore as never)
      McpAccessPolicyStore.resetCache()
    })
    afterEach(() => registerUpstreams({}))

    async function send(method: string, path: string, body?: unknown): Promise<{ status: number; text: string; lines: LogRecord[] }> {
      const id = newRequestId('n5a2')
      const res = await fetch(`${s.origin}/api/v1/configurationManager${path}`, {
        method,
        headers: { 'content-type': 'application/json', 'x-request-id': id },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const text = await res.text()
      const deadline = Date.now() + 3000
      while (Date.now() < deadline && !recordsOf(records, id).some((r) => r.message === 'http.request.end')) {
        await new Promise((resolve) => setTimeout(resolve, 5))
      }
      return { status: res.status, text, lines: recordsOf(records, id) }
    }

    const update = (section: string) => `config.${section}.update`
    const slackBot = { name: 'Ops bot', botToken: `xoxb-${SECRET}`, signingSecret: `sign-${SECRET}` }
    // Every admin write route (AUDIT §4: 37), in an order where each finds what it needs. `path` may read the
    // answer of an earlier route (the Slack bot's id). `events` is every audit line the request writes.
    const ADMIN_WRITES: Array<{ method: string; path: string | ((ctx: Record<string, string>) => string); body?: unknown; events: string[]; keep?: (text: string, ctx: Record<string, string>) => void }> = [
      { method: 'POST', path: '/storageConfig', body: { storageType: 'local' }, events: [update('storage')] },
      { method: 'POST', path: '/smtpConfig', body: { host: 'smtp.acme.test', port: 587, fromEmail: 'noreply@acme.test', username: 'n5a2-user', password: SECRET }, events: [update('smtp')] },
      { method: 'PUT', path: '/smtpConfig/enabled', body: { enabled: false }, events: [update('smtp')] },
      { method: 'DELETE', path: '/smtpConfig', events: ['config.smtp.delete'] },
      { method: 'POST', path: '/connectors/atlassian/config', body: { clientId: 'atl-client', clientSecret: SECRET }, events: [update('connector_atlassian')] },
      { method: 'POST', path: '/connectors/sharepoint/config', body: { clientId: 'sp-client', clientSecret: SECRET, tenantId: 'tenant-1', sharepointDomain: 'acme.sharepoint.com', hasAdminConsent: true }, events: [update('connector_sharepoint')] },
      { method: 'POST', path: '/connectors/onedrive/config', body: { clientId: 'od-client', clientSecret: SECRET, tenantId: 'tenant-1', hasAdminConsent: true }, events: [update('connector_onedrive')] },
      { method: 'POST', path: '/authConfig/microsoft', body: { clientId: 'ms-client', tenantId: 'common' }, events: [update('auth_microsoft')] },
      { method: 'POST', path: '/authConfig/google', body: { clientId: 'google-client' }, events: [update('auth_google')] },
      { method: 'POST', path: '/platform/settings', body: { fileUploadMaxSizeBytes: 1048576, featureFlags: {} }, events: [update('platform')] },
      {
        method: 'POST', path: '/slack-bot', body: slackBot, events: [update('slack_bot')],
        keep: (text, ctx) => { ctx.bot = JSON.parse(text).config.id },
      },
      { method: 'PUT', path: (ctx) => `/slack-bot/${ctx.bot}`, body: { ...slackBot, name: 'Ops bot 2' }, events: [update('slack_bot')] },
      { method: 'DELETE', path: (ctx) => `/slack-bot/${ctx.bot}`, events: [update('slack_bot')] },
      { method: 'PUT', path: '/prompts/system', body: { customSystemPrompt: `Be brief ${SECRET}`, customSystemPromptWebSearch: '' }, events: [update('system_prompt')] },
      { method: 'POST', path: '/connectors/googleWorkspaceCredentials', body: { adminEmail: 'it@acme.test', fileChanged: false }, events: [update('connector_google_workspace')] },
      { method: 'POST', path: '/connectors/googleWorkspaceOauthConfig', body: { clientId: 'gw-client', clientSecret: SECRET }, events: [update('connector_google_workspace')] },
      { method: 'PUT', path: '/ai-models/roles', body: { roles: { indexing: { modelType: 'llm', modelKey: 'mk-1' } } }, events: [update('ai_models')] },
      { method: 'PUT', path: `/ai-models/connections/${CONNECTION}/credentials`, body: { configuration: { apiKey: SECRET } }, events: [update('ai_models')] },
      { method: 'PUT', path: `/ai-models/connections/${CONNECTION}/identity-forwarding`, body: { enabled: true }, events: [update('ai_models')] },
      { method: 'POST', path: '/ai-models/providers/batch', body: { modelType: 'llm', provider: 'openAI', configuration: { apiKey: SECRET }, models: [{ model: 'gpt-4.1' }] }, events: [update('ai_models')] },
      { method: 'PUT', path: '/mcp/access', body: { enabled: true, allowWriteTools: false, disabledTools: [] }, events: [update('mcp_access')] },
      { method: 'PUT', path: '/ai-models/identity-policy', body: {}, events: [update('ai_identity_policy')] },
      { method: 'POST', path: '/ai-models/providers', body: { modelType: 'llm', provider: 'openAI', configuration: { model: 'gpt-4o-mini', apiKey: SECRET } }, events: [update('ai_models')] },
      { method: 'PUT', path: '/ai-models/providers/llm/mk-2', body: { provider: 'openAI', configuration: { model: 'gpt-4o-mini', apiKey: SECRET } }, events: [update('ai_models')] },
      { method: 'PUT', path: '/ai-models/default/llm/mk-2', events: [update('ai_models')] },
      { method: 'DELETE', path: '/ai-models/providers/llm/mk-1', events: [update('ai_models')] },
      // Last of the AI model routes: a bulk save replaces the seeded models the others need.
      { method: 'POST', path: '/aiModelsConfig', body: { llm: [{ provider: 'openAI', configuration: { model: 'gpt-4o', apiKey: SECRET } }] }, events: [update('ai_models')] },
      { method: 'PUT', path: '/web-search/settings', body: { includeImages: false }, events: [update('web_search')] },
      { method: 'POST', path: '/web-search/providers', body: { provider: 'tavily', configuration: { apiKey: SECRET } }, events: [update('web_search')] },
      { method: 'PUT', path: '/web-search/providers/key-serper', body: { provider: 'serper', configuration: { apiKey: SECRET }, isDefault: true }, events: [update('web_search')] },
      { method: 'PUT', path: '/web-search/default/key-serper', events: [update('web_search')] },
      { method: 'DELETE', path: '/web-search/providers/key-exa', events: [update('web_search')] },
      { method: 'POST', path: '/frontendPublicUrl', body: { url: 'https://pipeshub.acme.test' }, events: [update('public_urls')] },
      { method: 'POST', path: '/connectorPublicUrl', body: { url: 'https://connectors.acme.test' }, events: [update('public_urls')] },
      { method: 'PUT', path: '/metricsCollection/toggle', body: { enableMetricCollection: true }, events: [update('metrics')] },
      { method: 'PATCH', path: '/metricsCollection/pushInterval', body: { pushIntervalMs: 60000 }, events: [update('metrics')] },
      { method: 'PATCH', path: '/metricsCollection/serverUrl', body: { serverUrl: 'https://metrics.acme.test' }, events: [update('metrics')] },
    ]

    describe('every admin write route (one store, in order) writes its audit lines with the admin as actor', () => {
      const ctx: Record<string, string> = {}
      before(async () => {
        kv = seededStore()
        s = await serve(kv, backend.url)
      })
      after(() => s.close())

      it('covers the 37 admin write routes', () => {
        expect(ADMIN_WRITES).to.have.length(37)
      })
      for (const route of ADMIN_WRITES) {
        it(`${route.method} ${typeof route.path === 'string' ? route.path : route.path({ bot: ':configId' })} -> ${route.events.join(', ')}`, async () => {
          const path = typeof route.path === 'function' ? route.path(ctx) : route.path
          const r = await send(route.method, path, route.body)
          const label = `${route.method} ${path} -> ${r.status} ${r.text.slice(0, 200)}`
          expect(r.status, label).to.be.oneOf([200, 201])
          const lines = audits(r.lines)
          expect(lines.map((x) => x.message), label).to.deep.equal(route.events)
          for (const line of lines) {
            expect(line, label).to.include({ level: 'info', actor: ADMIN, outcome: 'success', reason: null, target: null, org_id: ORG })
          }
          // The audit line is the change's record: no other line at INFO or above but the access line.
          expect(otherLines(r.lines), label).to.deep.equal([])
          expect(written(r.lines), label).to.not.contain(SECRET)
          route.keep?.(r.text, ctx)
        })
      }
    })

    describe('the other storage types (the table saves local storage)', () => {
      beforeEach(async () => {
        kv = seededStore()
        s = await serve(kv, backend.url)
      })
      afterEach(() => s.close())

      const STORAGE_SAVES: Array<[string, Record<string, unknown>]> = [
        ['an S3', { storageType: 's3', s3AccessKeyId: 'AKIAN5A2', s3SecretAccessKey: SECRET, s3Region: 'us-east-1', s3BucketName: 'acme-files' }],
        ['an Azure Blob', { storageType: 'azureBlob', azureBlobConnectionString: `AccountKey=${SECRET}`, containerName: 'acme-files' }],
      ]
      for (const [name, body] of STORAGE_SAVES) {
        it(`${name} save is one storage audit line and no controller INFO line`, async () => {
          sinon.stub(s3HealthCheck, 'validateS3Capabilities').resolves({ success: true, checks: [] })
          const r = await send('POST', '/storageConfig', body)
          expect(r.status, r.text).to.be.oneOf([200, 201])
          expect(audits(r.lines).map((x) => x.message)).to.deep.equal([update('storage')])
          expect(otherLines(r.lines)).to.deep.equal([])
          expect(written(r.lines)).to.not.contain(SECRET)
        })
      }
    })

    describe('what is not audited', () => {
      beforeEach(async () => {
        kv = seededStore()
        s = await serve(kv, backend.url)
      })
      afterEach(() => s.close())

    // The same handlers on service-token routes: the connector service saves these, not an admin.
    const INTERNAL_WRITES: Array<[string, string, unknown]> = [
      ['POST', '/internal/connectors/atlassian/config', { clientId: 'atl-client', clientSecret: SECRET }],
      ['POST', '/internal/connectors/onedrive/config', { clientId: 'od-client', clientSecret: SECRET, tenantId: 'tenant-1', hasAdminConsent: true }],
      ['POST', '/internal/connectors/sharepoint/config', { clientId: 'sp-client', clientSecret: SECRET, tenantId: 'tenant-1', sharepointDomain: 'acme.sharepoint.com', hasAdminConsent: true }],
      ['POST', '/internal/connectors/googleWorkspaceCredentials', { adminEmail: 'it@acme.test', fileChanged: false }],
      ['POST', '/internal/connectors/googleWorkspaceOauthConfig', { clientId: 'gw-client', clientSecret: SECRET }],
      ['POST', '/internal/metricsCollection/toggle', { enableMetricCollection: true }],
      ['DELETE', '/internal/connectors/business/googleWorkspaceCredentials', undefined],
    ]
    for (const [method, path, body] of INTERNAL_WRITES) {
      it(`${method} ${path} saves with no audit line`, async () => {
        const before = new Map(kv.values)
        const r = await send(method, path, body)
        expect(r.status, r.text).to.equal(200)
        expect(kv.values, 'the write happened').to.not.deep.equal(before)
        expect(audits(r.lines)).to.deep.equal([])
      })
    }

    it('a refused write (400) and a failed write (500) write no audit line', async () => {
      const refused = await send('POST', '/authConfig/google', {})
      expect(refused.status).to.equal(400)
      sinon.stub(kv, 'set').rejects(new Error('etcd down'))
      const failed = await send('POST', '/authConfig/google', { clientId: 'google-client' })
      expect(failed.status).to.equal(500)
      expect(audits([...refused.lines, ...failed.lines])).to.deep.equal([])
    })

    it('an MCP access policy the store did not save writes no audit line', async () => {
      sinon.stub(mcpStore, 'set').rejects(new Error('etcd down'))
      const r = await send('PUT', '/mcp/access', { enabled: true, allowWriteTools: false, disabledTools: [] })
      expect(r.status).to.equal(500)
      expect(audits(r.lines)).to.deep.equal([])
    })

    it('a save whose later step fails keeps its audit line (the SMTP reload)', async () => {
      backend.on('POST', '/api/v1/mail/updateSmtpConfig', 'drop')
      const r = await send('POST', '/smtpConfig', { host: 'smtp.acme.test', port: 587, fromEmail: 'noreply@acme.test', password: SECRET })
      expect(r.status).to.be.at.least(500)
      expect(audits(r.lines).map((x) => x.message)).to.deep.equal([update('smtp')])
    })

    it('reads write no audit line', async () => {
      for (const path of ['/smtpConfig', '/authConfig/google', '/aiModelsConfig', '/web-search', '/mcp/access', '/metricsCollection']) {
        const r = await send('GET', path)
        expect(r.status, path).to.equal(200)
        expect(audits(r.lines), path).to.deep.equal([])
      }
    })
    })
  })
})
