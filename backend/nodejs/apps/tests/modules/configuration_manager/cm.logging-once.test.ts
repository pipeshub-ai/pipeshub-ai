import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import http from 'http'
import type { AddressInfo } from 'net'
import type { NextFunction, Request, RequestHandler, Response } from 'express'
import { Container } from 'inversify'
import { createConfigurationManagerRouter } from '../../../src/modules/configuration_manager/routes/cm_routes'
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context'
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'
import { EncryptionService } from '../../../src/libs/encryptor/encryptor'
import { loadConfigurationManagerConfig } from '../../../src/modules/configuration_manager/config/config'
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths'
import { registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'
import * as userAdminService from '../../../src/modules/user_management/services/user-admin.service'
import * as orgUtils from '../../../src/modules/user_management/utils/org.utils'
import { createGoogleWorkspaceCredentials } from '../../../src/modules/configuration_manager/controller/cm_controller'
import { captureLogRecords, inRequestContext, newRequestId, recordsOf, type LogRecord } from '../../helpers/log-capture'
import { FakeBackend, buildConfig } from '../tokens_manager/routes/connectors-http-harness'

// The real configuration manager router, request context, access line and error middleware over HTTP; the
// key-value store is in memory (or always failing), and the AI service and the embedding server are one FakeBackend.
// Each failure is the error middleware's one line; the controller adds none, and no line carries a provider's text,
// a stored setting or a model configuration.

const ORG = '64c000000000000000000b01'
const ADMIN = '64c0000000000000000000b1'
const CONTROLLER = 'ConfigurationManagerController'
const PROVIDER_TEXT = 'Incorrect API key provided: sk-proj-n5a1****marker'
const API_KEY = 'sk-proj-n5a1-secret-key'
const ENDPOINT = 'https://n5a1-endpoint-marker.openai.azure.test'
const STORED_TEXT = 'xYz-unique-42'

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
}

/** A key-value store whose every call fails, as when etcd or Redis is down. */
const downStore = (): unknown =>
  new Proxy(
    {},
    { get: (_target, key) => (key === 'then' ? undefined : () => Promise.reject(new Error('etcd down'))) },
  )

interface Served {
  origin: string
  router: express.Router
  close: () => Promise<void>
}

interface Events {
  start: sinon.SinonStub
  publishEvent: sinon.SinonStub
  stop: sinon.SinonStub
}

const events = (): Events => ({
  start: sinon.stub().resolves(),
  publishEvent: sinon.stub().resolves(),
  stop: sinon.stub().resolves(),
})

const signedIn: RequestHandler = (req: Request, _res: Response, next: NextFunction) => {
  ;(req as any).user = { userId: ADMIN, orgId: ORG, email: 'ada.admin@acme.test', fullName: 'Ada Admin' }
  next()
}

async function serve(kv: unknown, backendUrl: string, aiEvents: Events = events()): Promise<Served> {
  const container = new Container()
  container.bind('KeyValueStoreService').toConstantValue(kv)
  container.bind('AppConfig').toConstantValue(buildConfig(backendUrl))
  container.bind('EntitiesEventProducer').toConstantValue(events())
  container.bind('AiConfigEventProducer').toConstantValue(aiEvents)
  container.bind('SyncEventProducer').toConstantValue(events())
  container.bind('ConfigService').toConstantValue({})
  container.bind('AuthMiddleware').toConstantValue({ authenticate: signedIn, scopedTokenValidator: () => signedIn })
  const router = createConfigurationManagerRouter(container)
  const app = express()
  app.use(requestContextMiddleware)
  app.use(accessLog())
  app.use(express.json())
  app.use('/api/v1/configurationManager', router)
  app.use(ErrorMiddleware.handleError())
  const server = await new Promise<http.Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening))
  })
  return {
    origin: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    router,
    close: async () => {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    },
  }
}

const level = (r: LogRecord): string => (String(r.level).toLowerCase() === 'warning' ? 'warn' : String(r.level).toLowerCase())
const atLeastInfo = (r: LogRecord): boolean => ['error', 'warn', 'info'].includes(level(r))
const fromController = (lines: LogRecord[]): LogRecord[] => lines.filter((r) => r.service === CONTROLLER)
const failureLines = (lines: LogRecord[]): LogRecord[] => lines.filter((r) => r.message === 'http.request.error')
const written = (lines: LogRecord[]): string => JSON.stringify(lines, (_k, v) => (v instanceof Error ? { message: v.message, stack: v.stack, cause: (v as any).cause } : v))

describe('Configuration manager routes log each failure once', function () {
  this.timeout(60000)
  const backend = new FakeBackend()
  let records: LogRecord[]
  let savedSecretKey: string | undefined
  let savedEmbeddingUrl: string | undefined

  const encryptor = () => {
    const cfg = loadConfigurationManagerConfig()
    return EncryptionService.getInstance(cfg.algorithm, cfg.secretKey)
  }

  before(async () => {
    savedSecretKey = process.env.SECRET_KEY
    process.env.SECRET_KEY = process.env.SECRET_KEY || 'cm-logging-once-secret'
    await backend.start()
    savedEmbeddingUrl = process.env.EMBEDDING_SERVER_URL
    process.env.EMBEDDING_SERVER_URL = backend.url
  })
  after(async () => {
    await backend.stop()
    if (savedSecretKey === undefined) delete process.env.SECRET_KEY
    else process.env.SECRET_KEY = savedSecretKey
    if (savedEmbeddingUrl === undefined) delete process.env.EMBEDDING_SERVER_URL
    else process.env.EMBEDDING_SERVER_URL = savedEmbeddingUrl
  })
  beforeEach(() => {
    backend.reset()
    registerAppUpstreams(buildConfig(backend.url))
    sinon.stub(userAdminService, 'isUserOrgAdmin').resolves(true)
    records = captureLogRecords()
  })
  afterEach(() => {
    sinon.restore()
    registerUpstreams({})
  })

  async function send(
    s: Served,
    method: string,
    path: string,
    body?: unknown,
  ): Promise<{ status: number; text: string; lines: LogRecord[] }> {
    const id = newRequestId('n5a1')
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

  describe('every route, with the key-value store down', () => {
    let s: Served
    beforeEach(async () => {
      // Each handler gets the empty body, so every catch block runs; the schemas are tested on their own.
      sinon.stub(ValidationMiddleware, 'validate').returns(((_req: Request, _res: Response, next: NextFunction) => next()) as never)
      sinon.stub(orgUtils, 'findActiveOrgById').rejects(new Error('document store down'))
      s = await serve(downStore(), backend.url)
    })
    afterEach(() => s.close())

    it('writes no controller line at INFO or above and at most one ERROR, the error middleware\'s', async () => {
      const routes = (s.router.stack as any[]).flatMap((layer) =>
        layer.route ? Object.keys(layer.route.methods).map((m) => ({ method: m.toUpperCase(), path: String(layer.route.path) })) : [],
      )
      expect(routes.length).to.be.greaterThan(80)
      const internal: string[] = []
      const refused: string[] = []
      for (const route of routes) {
        // Dials the mail server in the body (localhost here); its one WARN with the reply code is kept on purpose.
        if (`${route.method} ${route.path}` === 'POST /smtpConfig') continue
        const path = route.path.replace(/:modelType/g, 'llm').replace(/:[A-Za-z]+/g, 'n5a1-id')
        const r = await send(s, route.method, path, route.method === 'GET' || route.method === 'DELETE' ? undefined : {})
        const id = `${route.method} ${route.path} -> ${r.status}`
        expect(fromController(r.lines).filter(atLeastInfo), id).to.deep.equal([])
        const errors = r.lines.filter((x) => level(x) === 'error')
        expect(errors.map((x) => x.message), id).to.satisfy((m: string[]) => m.every((x) => x === 'http.request.error'))
        expect(errors, id).to.have.length.at.most(1)
        expect(failureLines(r.lines), id).to.have.length.at.most(1)
        // A 5xx with no line would be a swallowed failure.
        if (r.status >= 500) expect(failureLines(r.lines), id).to.have.length(1)
        if (errors.length === 1 && errors[0]!.error_class === 'internal' && r.status === 500) internal.push(id)
        if (r.status === 400 && failureLines(r.lines).length === 1) {
          expect(errors, id).to.deep.equal([])
          refused.push(id)
        }
      }
      // Most routes read the store first: one ERROR (internal), a 500. A handler's own refusal: one WARN, no ERROR.
      expect(internal.length).to.be.greaterThan(55)
      expect(refused.length).to.be.at.least(3)
    })

    it('answers a store outage 500 with one ERROR carrying one stack', async () => {
      const r = await send(s, 'GET', '/smtpConfig')
      expect(r.status).to.equal(500)
      const errors = r.lines.filter((x) => level(x) === 'error')
      expect(errors).to.have.length(1)
      expect(errors[0]).to.include({ message: 'http.request.error', error_class: 'internal' })
      expect(written(r.lines).match(/etcd down\\n\s+at /g) ?? []).to.have.length(1)
    })
  })

  it('a refused setting (400) is one WARN from the error middleware, with no ERROR', async () => {
    const s = await serve(new MemoryKvStore(), backend.url)
    try {
      const r = await send(s, 'PUT', '/prompts/system', {})
      expect(r.status).to.equal(400)
      expect(r.lines.filter((x) => level(x) === 'error')).to.deep.equal([])
      expect(fromController(r.lines)).to.deep.equal([])
      expect(failureLines(r.lines).map((x) => [level(x), x.error_class])).to.deep.equal([['warn', 'bad_request']])
    } finally {
      await s.close()
    }
  })

  describe('AI model health checks', () => {
    let kv: MemoryKvStore
    let s: Served
    beforeEach(async () => {
      kv = new MemoryKvStore()
      kv.values.set(
        configPaths.aiModels,
        encryptor().encrypt(
          JSON.stringify({
            llm: [
              { modelKey: 'mk-1', provider: 'azureOpenAI', isDefault: true, configuration: { model: 'gpt-4o', apiKey: 'old-key' } },
              { modelKey: 'mk-2', provider: 'openAI', isDefault: false, configuration: { model: 'gpt-4o-mini', apiKey: 'old-key' } },
            ],
            embedding: [],
          }),
        ),
      )
      kv.values.set(
        configPaths.webSearch,
        encryptor().encrypt(
          JSON.stringify({
            providers: [{ provider: 'serper', providerKey: 'key-serper', configuration: { apiKey: 'old-key' }, isDefault: false }],
          }),
        ),
      )
      s = await serve(kv, backend.url)
    })
    afterEach(() => s.close())

    const refused = { status: 400, body: { detail: PROVIDER_TEXT } }
    const checks: Array<[string, string, string, string, unknown]> = [
      ['bulk save, embedding', 'POST', '/aiModelsConfig', '/api/v1/embedding-health-check', { embedding: [{ provider: 'openAI', configuration: { model: 'text-embedding-3-small', apiKey: API_KEY } }] }],
      ['new default model', 'PUT', '/ai-models/default/llm/mk-2', '/api/v1/health-check/llm', undefined],
      ['add web search provider', 'POST', '/web-search/providers', '/api/v1/web-search-health-check', { provider: 'serper', configuration: { apiKey: API_KEY } }],
      ['update web search provider', 'PUT', '/web-search/providers/key-serper', '/api/v1/web-search-health-check', { provider: 'serper', configuration: { apiKey: API_KEY } }],
      ['default web search provider', 'PUT', '/web-search/default/key-serper', '/api/v1/web-search-health-check', undefined],
    ]
    for (const [name, method, path, checkPath, body] of checks) {
      it(`${name}: a refused check writes no controller line at any level`, async () => {
        backend.on('POST', checkPath, refused)
        const r = await send(s, method, path, body)
        expect(backend.callsTo('POST', checkPath), 'the check ran').to.have.length(1)
        expect(r.status).to.equal(400)
        expect(fromController(r.lines)).to.deep.equal([])
        expect(written(r.lines)).to.not.contain(API_KEY)
      })
    }

    it('adding a model the check refuses: one WARN ai_model.health_check.failed with the status, no provider text', async () => {
      backend.on('POST', '/api/v1/health-check/llm', refused)
      const r = await send(s, 'POST', '/ai-models/providers', {
        modelType: 'llm',
        provider: 'openAI',
        configuration: { model: 'gpt-4o', apiKey: API_KEY },
      })
      expect(r.status).to.equal(400)
      expect(r.text).to.contain('Incorrect API key provided')
      const mine = fromController(r.lines)
      expect(mine.map((x) => [level(x), x.message, x.status])).to.deep.equal([['warn', 'ai_model.health_check.failed', 400]])
      expect(mine[0]!.metadata).to.deep.equal({ model_type: 'llm', provider: 'openAI' })
      expect(r.lines.filter((x) => level(x) === 'error')).to.deep.equal([])
      expect(written(r.lines)).to.not.contain('n5a1****marker')
      expect(written(r.lines)).to.not.contain(API_KEY)
    })

    it('updating a model the check refuses: the same one WARN; no line has the endpoint or the configuration', async () => {
      backend.on('POST', '/api/v1/health-check/llm', refused)
      const r = await send(s, 'PUT', '/ai-models/providers/llm/mk-1', {
        provider: 'azureOpenAI',
        configuration: { model: 'gpt-4o', apiKey: API_KEY, endpoint: ENDPOINT, deploymentName: 'n5a1-deployment' },
      })
      expect(r.status).to.equal(400)
      const mine = fromController(r.lines)
      expect(mine.map((x) => [level(x), x.message, x.status])).to.deep.equal([['warn', 'ai_model.health_check.failed', 400]])
      expect(mine[0]!.metadata).to.deep.equal({ model_type: 'llm', provider: 'azureOpenAI' })
      for (const marker of ['n5a1****marker', API_KEY, ENDPOINT, 'n5a1-deployment']) {
        expect(written(r.lines), marker).to.not.contain(marker)
      }
    })

    it('a bulk save whose check fails with a 5xx is a 500, one WARN upstream_5xx whose cause is the status, never the check\'s body', async () => {
      backend.on('POST', '/api/v1/llm-health-check', { status: 500, body: { detail: PROVIDER_TEXT } })
      const r = await send(s, 'POST', '/aiModelsConfig', {
        llm: [{ provider: 'openAI', configuration: { model: 'gpt-4o', apiKey: API_KEY } }],
      })
      expect(r.status).to.equal(500)
      expect(fromController(r.lines)).to.deep.equal([])
      const failure = failureLines(r.lines)
      // The AI service failed and logs it itself, so Node's line is WARN, not ERROR.
      expect(failure.map((x) => [level(x), x.error_class])).to.deep.equal([['warn', 'upstream_5xx']])
      expect((failure[0]!.metadata.error as any).cause).to.deep.equal({ status: 500 })
      expect(written(r.lines)).to.not.contain('n5a1****marker')
    })

    it('a config event that cannot be sent is one ERROR broker.event.publish_failed with the event type; the save answers 200', async () => {
      const aiEvents = events()
      aiEvents.start.rejects(new Error('broker down'))
      const own = await serve(kv, backend.url, aiEvents)
      try {
        backend.on('POST', '/api/v1/llm-health-check', { status: 200, body: { status: 'healthy' } })
        const r = await send(own, 'POST', '/aiModelsConfig', {
          llm: [{ provider: 'openAI', configuration: { model: 'gpt-4o', apiKey: API_KEY } }],
        })
        expect(r.status).to.equal(200)
        const mine = fromController(r.lines)
        expect(mine.map((x) => [level(x), x.message])).to.deep.equal([['error', 'broker.event.publish_failed']])
        const { error, ...fields } = mine[0]!.metadata
        expect(fields).to.deep.equal({ event_type: 'llmConfigured' })
        expect(error).to.include({ message: 'broker down' })
      } finally {
        await own.close()
      }
    })
  })

  describe('stored settings that do not parse', () => {
    let kv: MemoryKvStore
    let s: Served
    beforeEach(async () => {
      kv = new MemoryKvStore()
      s = await serve(kv, backend.url)
    })
    afterEach(() => s.close())
    const unparsable = () => encryptor().encrypt(`{"botToken":${STORED_TEXT}}`)

    it('Slack bot settings: one ERROR (the middleware\'s), the stored text in no line', async () => {
      kv.values.set(configPaths.slackBot, unparsable())
      const r = await send(s, 'GET', '/slack-bot')
      expect(r.status).to.equal(500)
      expect(fromController(r.lines)).to.deep.equal([])
      const errors = r.lines.filter((x) => level(x) === 'error')
      expect(errors.map((x) => x.message)).to.deep.equal(['http.request.error'])
      expect((errors[0]!.metadata.error as any).cause).to.include({ name: 'SyntaxError', message: 'response was not valid JSON' })
      expect(written(r.lines)).to.not.contain(STORED_TEXT)
    })

    it('metrics collection settings: one WARN, then the empty config; the stored text in no line', async () => {
      kv.values.set(configPaths.metricsCollection, unparsable())
      const r = await send(s, 'GET', '/metricsCollection')
      expect(r.status).to.equal(200)
      const mine = fromController(r.lines)
      expect(mine.map((x) => [level(x), x.message])).to.deep.equal([
        ['warn', 'Failed to parse metrics collection config, using empty config'],
      ])
      expect(mine[0]!.metadata.error).to.include({ message: 'response was not valid JSON' })
      expect(written(r.lines)).to.not.contain(STORED_TEXT)
    })

    it('platform settings: one WARN, then the defaults; the stored text in no line', async () => {
      kv.values.set(configPaths.platform.settings, unparsable())
      const r = await send(s, 'GET', '/platform/settings')
      expect(r.status).to.equal(200)
      const warns = r.lines.filter((x) => level(x) === 'warn')
      expect(warns.map((x) => x.message)).to.deep.equal(['Failed to decrypt/parse platform settings; using defaults'])
      expect(warns[0]!.metadata.error).to.include({ message: 'response was not valid JSON' })
      expect(written(r.lines)).to.not.contain(STORED_TEXT)
    })
  })

  describe('AI service and embedding server proxies', () => {
    let s: Served
    beforeEach(async () => {
      s = await serve(new MemoryKvStore(), backend.url)
    })
    afterEach(() => s.close())

    it('the model registry not answering is a 503 whose cause is the call\'s error; no controller line', async () => {
      backend.on('GET', '/api/v1/ai-models/registry', 'drop')
      const r = await send(s, 'GET', '/ai-models/registry')
      expect(r.status).to.equal(503)
      expect(fromController(r.lines)).to.deep.equal([])
      const failure = failureLines(r.lines)
      expect(failure.map((x) => [level(x), x.error_class])).to.deep.equal([['warn', 'unavailable']])
      expect((failure[0]!.metadata.error as any).cause).to.be.an('object').that.has.property('code')
    })

    it('a registry answer that is an error is relayed with no controller line', async () => {
      backend.on('GET', '/api/v1/ai-models/registry/capabilities', { status: 404, body: { detail: 'no such capability' } })
      const r = await send(s, 'GET', '/ai-models/registry/capabilities')
      expect(r.status).to.equal(404)
      expect(fromController(r.lines)).to.deep.equal([])
      expect(failureLines(r.lines)).to.deep.equal([])
    })

    it('an embedding server that refuses to prepare a model is relayed with no controller line', async () => {
      backend.on('POST', '/prepare-model', { status: 409, body: { detail: 'already downloading' } })
      const r = await send(s, 'POST', '/ai-models/prepare-model', { model: 'BAAI/bge-m3' })
      expect(r.status).to.equal(409)
      expect(fromController(r.lines)).to.deep.equal([])
    })

    it('an embedding server that does not answer a prepare-model call is one WARN unavailable from the error middleware', async () => {
      backend.on('POST', '/prepare-model', 'drop')
      const r = await send(s, 'POST', '/ai-models/prepare-model', { model: 'BAAI/bge-m3' })
      expect(r.status).to.equal(503)
      expect(fromController(r.lines)).to.deep.equal([])
      expect(failureLines(r.lines).map((x) => [level(x), x.error_class])).to.deep.equal([['warn', 'unavailable']])
    })

    it('a download-progress stream that loses the embedding server is one WARN', async () => {
      backend.on('GET', '/download-progress/BAAI%2Fbge-m3', { status: 500, body: { detail: 'n5a1-embedding-body' } })
      const r = await send(s, 'GET', '/ai-models/download-progress?model=BAAI%2Fbge-m3')
      expect(r.status).to.equal(200)
      expect(r.text).to.contain('Lost connection to embedding server')
      expect(fromController(r.lines).map((x) => [level(x), x.message])).to.deep.equal([
        ['warn', 'Lost connection to embedding server'],
      ])
      expect(written(r.lines)).to.not.contain('n5a1-embedding-body')
    })
  })

  it('a Google Workspace business save with real-time updates names no Gmail topic on any line', async () => {
    const TOPIC = 'projects/n5a1-project/topics/n5a1-topic-marker'
    sinon.stub(orgUtils, 'findActiveOrgById').resolves({ accountType: 'business' } as never)
    const kv = new MemoryKvStore()
    kv.values.set(
      `${configPaths.connectors.googleWorkspace.credentials.business}/${ORG}`,
      encryptor().encrypt(JSON.stringify({ type: 'service_account', client_id: 'n5a1-client', adminEmail: 'it@acme.test' })),
    )
    const res: any = { status: sinon.stub(), json: sinon.stub(), end: sinon.stub() }
    res.status.returns(res)
    res.json.returns(res)
    const next = sinon.stub()
    const id = newRequestId('n5a1')
    await inRequestContext(id, () =>
      createGoogleWorkspaceCredentials(kv as never, ADMIN, ORG, events() as never)(
        { body: { adminEmail: 'it@acme.test', enableRealTimeUpdates: 'true', topicName: TOPIC }, headers: {} } as never,
        res,
        next,
      ),
    )
    expect(next.called, String(next.firstCall?.args[0])).to.equal(false)
    expect(res.status.firstCall.args[0]).to.equal(200)
    expect(written(recordsOf(records, id))).to.not.contain('n5a1-topic-marker')
  })
})
