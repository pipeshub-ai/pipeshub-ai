import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import nock from 'nock'
import winston from 'winston'
import {
  ADMIN,
  Harness,
  call,
  errorMessage,
  fetchConfigToken,
  sessionToken,
  startHarness,
} from './connectors-http-harness'
import * as tokensConfig from '../../../../src/modules/tokens_manager/config/config'
import { createHealthRouter } from '../../../../src/modules/tokens_manager/routes/health.routes'
import { ConfigService } from '../../../../src/modules/tokens_manager/services/cm.service'
import { EntitiesEventProducer, EventType } from '../../../../src/modules/tokens_manager/services/entity_event.service'
import { Logger } from '../../../../src/libs/services/logger.service'
import * as buildInfoUtils from '../../../../src/libs/utils/build-info.utils'

/**
 * The legacy Google routes, the health probe, the stored-config fallbacks and the entity-event producer:
 * one line per failure, at its class's level, with no stored body or decrypted value in any line
 * (docs/logging.md, "Connector proxy (Node)").
 */

type Row = Record<string, any>

const GOOGLE = 'https://oauth2.googleapis.com'
const CM_OAUTH_CONFIG = '/api/v1/configurationManager/internal/connectors/googleWorkspaceOauthConfig'
const CM_INDIVIDUAL_CREDENTIALS = '/api/v1/configurationManager/internal/connectors/individual/googleWorkspaceCredentials'
const CM_SECRET_BODY = 'cm-body-marker-4471'
const oauthConfig = { clientId: 'google-client', clientSecret: 'google-secret-do-not-log', enableRealTimeUpdates: true }
const JITTER = 0.123456

const level = (r: Row): string => {
  const l = String(r.level).toLowerCase()
  return l === 'warning' ? 'warn' : l
}

describe('Legacy routes, health probe and stored config: one line per failure', function () {
  this.timeout(30000)
  let records: Row[]

  beforeEach(() => {
    records = []
    // Every winston logger, whichever root a module got at load, at every level.
    sinon.stub(winston.Logger.prototype, 'isLevelEnabled').returns(true)
    sinon.stub(winston.Logger.prototype, 'log').callsFake(function (this: unknown, info: Row) {
      records.push(info)
      return this
    } as any)
  })
  afterEach(() => sinon.restore())

  const byMessage = (message: string): Row[] => records.filter((r) => r.message === message)
  const errors = (): Row[] => records.filter((r) => level(r) === 'error')

  describe('legacy Google Workspace routes', () => {
    let h: Harness

    beforeEach(async () => {
      h = await startHarness()
      nock.disableNetConnect()
      nock.enableNetConnect('127.0.0.1')
      // The refresh waits 2^n s plus jitter between Google attempts; only those waits are skipped.
      sinon.stub(Math, 'random').returns(JITTER)
      const realSetTimeout = globalThis.setTimeout
      sinon.stub(globalThis, 'setTimeout').callsFake(((fn: () => void, ms?: number, ...args: unknown[]) =>
        realSetTimeout(fn, typeof ms === 'number' && ms >= 1000 && Math.abs((ms % 1000) - JITTER * 1000) < 1e-6 ? 0 : ms, ...args)) as typeof setTimeout)
    })
    afterEach(async () => {
      nock.cleanAll()
      nock.enableNetConnect()
      await h.close()
    })

    const storedRefreshToken = (): void => {
      h.backend.on('GET', CM_INDIVIDUAL_CREDENTIALS, { status: 200, body: { refresh_token: 'stored-refresh-do-not-log' } })
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: oauthConfig })
    }
    const refresh = async (): Promise<number> => {
      records.length = 0
      return (await call(h, 'POST', '/internal/refreshIndividualConnectorToken', fetchConfigToken(h, ADMIN))).status
    }

    for (const [name, status, body] of [
      ['a dead grant', 400, { error: 'invalid_grant' }],
      ['Google unavailable', 503, { error: 'backendError' }],
    ] as const) {
      it(`${name}: one ERROR line (the middleware's), no line per attempt, 500 as before`, async () => {
        storedRefreshToken()
        const google = nock(GOOGLE).post('/token').reply(status, body).persist()

        expect(await refresh()).to.equal(500)

        // How many POSTs is a behaviour of the route and of the shared axios-retry, not of this logging.
        expect(google.isDone()).to.equal(true)
        expect(errors().map((r) => r.message)).to.deep.equal(['http.request.error'])
        expect(records.filter((r) => String(r.message).startsWith('Error refreshing'))).to.deep.equal([])
        expect(JSON.stringify(records)).to.not.include('stored-refresh-do-not-log')
        expect(JSON.stringify(records)).to.not.include('google-secret-do-not-log')
      })
    }

    it('a refresh that succeeds writes one INFO line of its own', async () => {
      storedRefreshToken()
      h.backend.on('POST', '/api/v1/configurationManager/internal/connectors/googleWorkspaceCredentials', { status: 200, body: { ok: true } })
      nock(GOOGLE).post('/token').reply(200, { access_token: 'fresh-access-do-not-log', expires_in: 3600 })

      expect(await refresh()).to.equal(200)

      expect(records.filter((r) => ['info', 'warn', 'error'].includes(level(r))).map((r) => r.message)).to.deep.equal([
        'Access token refreshed successfully',
      ])
      expect(JSON.stringify(records)).to.not.include('fresh-access-do-not-log')
    })

    it('the configuration manager failing (5xx) is upstream_5xx at WARN, without its body', async () => {
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 500, body: { detail: CM_SECRET_BODY, owner: 'ada@acme.test' } })

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, ADMIN), { tempCode: 'consent-code' })

      expect(r.status).to.equal(500)
      expect(byMessage('http.request.error').map((x) => [level(x), x.error_class])).to.deep.equal([['warn', 'upstream_5xx']])
      expect(JSON.stringify(records)).to.not.include(CM_SECRET_BODY)
      expect(JSON.stringify(records)).to.not.include('ada@acme.test')
    })

    // 408 too: a 4xx status as the cause would read as `timeout`, an upstream class, and hide our failure at WARN.
    for (const status of [404, 408] as const) {
      it(`the configuration manager refusing (${status}) stays our 500, internal, without its body`, async () => {
        h.backend.on('GET', CM_OAUTH_CONFIG, { status, body: { detail: CM_SECRET_BODY } })

        const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, ADMIN), { tempCode: 'consent-code' })

        expect(r.status).to.equal(500)
        expect(byMessage('http.request.error').map((x) => [level(x), x.error_class])).to.deep.equal([['error', 'internal']])
        expect(JSON.stringify(records)).to.not.include(CM_SECRET_BODY)
      })
    }

    it('the configuration manager answering 502 is upstream_5xx at WARN', async () => {
      h.backend.on('GET', CM_OAUTH_CONFIG, { status: 502, body: { detail: CM_SECRET_BODY } })

      const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, ADMIN), { tempCode: 'consent-code' })

      expect(r.status).to.equal(500)
      expect(byMessage('http.request.error').map((x) => [level(x), x.error_class])).to.deep.equal([['warn', 'upstream_5xx']])
    })

    it('a refresh token missing from storage (a 200 without it) is our 500 and logs nothing of the stored answer', async () => {
      h.backend.on('GET', CM_INDIVIDUAL_CREDENTIALS, { status: 200, body: { topicName: CM_SECRET_BODY } })

      expect(await refresh()).to.equal(500)

      expect(errors().map((r) => [r.message, r.error_class])).to.deep.equal([['http.request.error', 'internal']])
      expect(JSON.stringify(records)).to.not.include(CM_SECRET_BODY)
    })

    for (const route of ['/getTokenFromCode', '/internal/refreshIndividualConnectorToken'] as const) {
      it(`${route}: no client secret configured is our failure, 500 internal at ERROR, and Google is not called`, async () => {
        sinon.stub(tokensConfig, 'loadAppConfig').resolves({ frontendUrl: 'https://app.acme.test/' } as tokensConfig.AppConfig)
        h.backend.on('GET', CM_INDIVIDUAL_CREDENTIALS, { status: 200, body: { refresh_token: 'stored-refresh-do-not-log' } })
        h.backend.on('GET', CM_OAUTH_CONFIG, { status: 200, body: { ...oauthConfig, clientSecret: '' } })
        const google = nock(GOOGLE).post('/token').reply(200, {})

        const token = route === '/getTokenFromCode' ? sessionToken(h, ADMIN) : fetchConfigToken(h, ADMIN)
        const r = await call(h, 'POST', route, token, route === '/getTokenFromCode' ? { tempCode: 'consent-code' } : undefined)

        expect(r.status).to.equal(500)
        expect(errorMessage(r)).to.equal('Google Workspace client id or secret is not configured')
        expect(errors().map((x) => [x.message, x.error_class])).to.deep.equal([['http.request.error', 'internal']])
        expect(google.isDone()).to.equal(false)
      })
    }
  })

  describe('entity-event producer', () => {
    const producerOver = (publish: () => Promise<void>): EntitiesEventProducer =>
      new EntitiesEventProducer(
        { isConnected: () => true, connect: async () => undefined, disconnect: async () => undefined, publish } as any,
        Logger.getInstance({ service: 'tokens_manager' }),
      )
    const event = { eventType: EventType.AppEnabledEvent, timestamp: 1, payload: { orgId: 'o', appGroup: 'g', appGroupId: 'id', apps: [] } } as any

    it('a published event is one DEBUG line with event_type and topic', async () => {
      await producerOver(async () => undefined).publishEvent(event)

      expect(records.map((r) => [level(r), r.message, r.metadata?.event_type, r.metadata?.topic])).to.deep.equal([
        ['debug', 'broker.event.published', 'appEnabled', 'entity-events'],
      ])
    })

    it('a failed publish is one ERROR line with the error, and is still swallowed', async () => {
      await producerOver(async () => {
        throw new Error('Connection is closed.')
      }).publishEvent(event)

      expect(records.map((r) => [level(r), r.message, r.metadata?.event_type, r.metadata?.topic])).to.deep.equal([
        ['error', 'broker.event.publish_failed', 'appEnabled', 'entity-events'],
      ])
      expect(records[0].metadata.error.message).to.equal('Connection is closed.')
    })
  })

  describe('health probe', () => {
    let deployment: Record<string, string | undefined>
    let readDeploymentConfig: sinon.SinonStub
    let axiosGet: sinon.SinonStub
    let handler: (req: unknown, res: unknown, next: unknown) => Promise<void>

    beforeEach(() => {
      deployment = { dataStoreType: 'arangodb', messageBrokerType: 'redis', kvStoreType: 'redis', vectorDbType: 'qdrant' }
      readDeploymentConfig = sinon.stub().callsFake(async () => ({ ...deployment }))
      sinon.stub(ConfigService, 'getInstance').returns({ readDeploymentConfig } as any)
      sinon.stub(buildInfoUtils, 'getBuildInfo').returns({ version: '0.0.0', commitId: 'a'.repeat(40) } as any)
      axiosGet = sinon.stub(require('axios'), 'get')
      const probe = { get: async () => null, healthCheck: async () => true }
      const appConfig = { connectorBackend: 'http://connector', deployment }
      const container = {
        isBound: () => true,
        get: (key: string) => (key === 'AppConfig' ? appConfig : key === 'AuthMiddleware' ? { authenticate: () => undefined } : probe),
      } as any
      const router = createHealthRouter(container, { get: () => probe } as any) as any
      const layer = router.stack.find((l: any) => l.route?.path === '/' && l.route.methods.get)
      handler = layer.route.stack[layer.route.stack.length - 1].handle
    })
    afterEach(() => {
      ;(ConfigService as any).instance = undefined
    })

    const answer = (graph: unknown, vector: unknown = { status: 200 }): void => {
      axiosGet.callsFake(async (url: string) => {
        const reply = url.includes('graph-db') ? graph : vector
        if (reply instanceof Error) throw reply
        return reply
      })
    }
    const probe = async (): Promise<sinon.SinonStub> => {
      const next = sinon.stub()
      await handler({}, { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }, next)
      return next
    }
    const changes = (): Array<[string, string, string, string]> =>
      byMessage('health.check.changed').map((r) => [level(r), r.metadata.check, r.metadata.state, r.metadata.previous])

    it('a check that turns unhealthy is one WARN, then DEBUG while it stays so, then one INFO when it recovers', async () => {
      answer({ status: 500 })
      await probe()
      await probe()
      await probe()
      answer({ status: 200 })
      await probe()

      expect(changes().filter(([, check]) => check === 'graph_db')).to.deep.equal([
        ['warn', 'graph_db', 'unhealthy', 'none'],
        ['debug', 'graph_db', 'unhealthy', 'unhealthy'],
        ['debug', 'graph_db', 'unhealthy', 'unhealthy'],
        ['info', 'graph_db', 'healthy', 'unhealthy'],
      ])
      const first = byMessage('health.check.changed')[0]
      expect([first.status, first.error_class]).to.deep.equal([500, 'upstream_5xx'])
      expect(errors()).to.deep.equal([])
    })

    it('a first probe that finds every check healthy writes nothing above DEBUG', async () => {
      answer({ status: 200 })
      await probe()

      expect(records.filter((r) => level(r) !== 'debug')).to.deep.equal([])
    })

    it('a timed-out graph check is WARN reason=timeout; a refused connection is WARN state=unknown', async () => {
      answer(Object.assign(new Error('timeout of 5000ms exceeded'), { code: 'ECONNABORTED', request: {} }))
      await probe()
      answer(Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' }))
      await probe()

      const graph = byMessage('health.check.changed').filter((r) => r.metadata.check === 'graph_db')
      expect(graph.map((r) => [level(r), r.metadata.state, r.reason, r.error_class])).to.deep.equal([
        ['warn', 'unhealthy', 'timeout', 'timeout'],
        ['warn', 'unknown', 'connector_refused', undefined],
      ])
    })

    it('the vector check is logged the same way', async () => {
      answer({ status: 200 }, { status: 503 })
      await probe()

      expect(changes().filter(([, check]) => check === 'vector_db')).to.deep.equal([['warn', 'vector_db', 'unhealthy', 'none']])
    })

    it('a store type not written yet is DEBUG only', async () => {
      deployment.dataStoreType = undefined
      deployment.vectorDbType = undefined
      answer({ status: 200 })
      await probe()

      expect(records.filter((r) => level(r) !== 'debug')).to.deep.equal([])
    })

    it('a deployment record that cannot be read answers from the boot config, with one WARN that does not quote it', async () => {
      // Today's reader swallows its own parse errors; this pins what the route logs if one ever reaches it.
      readDeploymentConfig.callsFake(async () => JSON.parse('{"dataStoreType":xYz-deploy-quoted}'))
      answer({ status: 200 })
      await probe()

      const lines = byMessage('Failed to refresh deployment config')
      expect(lines.map(level)).to.deep.equal(['warn'])
      expect(lines[0].metadata.error.name).to.equal('SyntaxError')
      expect(JSON.stringify(records)).to.not.include('xYz-deploy')
    })

    it('an unexpected failure goes to the error middleware, with no line of its own', async () => {
      answer({ status: 200 })
      const failure = new Error('unexpected serialization error')
      const next = sinon.stub()
      await handler({}, { status: sinon.stub().returnsThis(), json: sinon.stub().throws(failure) }, next)

      expect(next.calledOnceWithExactly(failure)).to.equal(true)
      expect(records.filter((r) => level(r) !== 'debug')).to.deep.equal([])
    })
  })

  describe('stored configuration', () => {
    const DECRYPTED_MARKER = 'xYz-unique'
    let kv: { get: sinon.SinonStub; set: sinon.SinonStub }
    let service: any

    beforeEach(() => {
      kv = { get: sinon.stub(), set: sinon.stub().resolves() }
      service = Object.create(ConfigService.prototype)
      service.keyValueStoreService = kv
      service.encryptionService = { encrypt: (t: string) => t, decrypt: sinon.stub() }
    })

    it('a value that cannot be decrypted falls back to the environment with one WARN naming the path', async () => {
      kv.get.resolves('stored')
      service.encryptionService.decrypt.throws(new Error('Unsupported state or unable to authenticate data'))

      expect(await service.getEncryptedConfig('/services/kafka', { brokers: ['env'] })).to.deep.equal({ brokers: ['env'] })

      expect(records.map((r) => [level(r), r.message, r.metadata?.path])).to.deep.equal([
        ['warn', 'Stored config unreadable; using the environment defaults', '/services/kafka'],
      ])
    })

    it('a decrypted value that is not JSON is never quoted (JSON.parse quotes it in its message)', async () => {
      kv.get.resolves('stored')
      service.encryptionService.decrypt.returns(`{"uri":${DECRYPTED_MARKER}-42}`)

      await service.getEncryptedConfig('/services/kafka', {})

      expect(byMessage('Stored config unreadable; using the environment defaults')).to.have.length(1)
      expect(JSON.stringify(records)).to.not.include(DECRYPTED_MARKER)
    })

    it('an unreadable deployment record is rewritten with one WARN, without quoting it', async () => {
      kv.get.resolves(`{"dataStoreType":${DECRYPTED_MARKER}}`)

      const config = await service.getDeploymentConfig()

      expect(Object.keys(config).sort()).to.deep.equal(['kvStoreType', 'messageBrokerType'])
      expect(records.map((r) => [level(r), r.message])).to.deep.equal([
        ['warn', 'Deployment config unreadable; rewriting it with the Node-owned keys only'],
      ])
      expect(JSON.stringify(records)).to.not.include(DECRYPTED_MARKER)
    })
  })
})
