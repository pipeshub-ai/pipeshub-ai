import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createKnowledgeBaseRouter } from '../../../../src/modules/knowledge_base/routes/kb.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { RecordsEventProducer } from '../../../../src/modules/knowledge_base/services/records_events.service'
import { SyncEventProducer } from '../../../../src/modules/knowledge_base/services/sync_events.service'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Knowledge Base Routes', () => {
  let container: Container
  let mockAuthMiddleware: any
  let mockAppConfig: any
  let mockRecordsEventProducer: any
  let mockSyncEventProducer: any
  let mockKeyValueStore: any

  beforeEach(() => {
    container = new Container()

    mockAuthMiddleware = {
      authenticate: (_req: any, _res: any, next: any) => next(),
      scopedTokenValidator: sinon.stub().returns((_req: any, _res: any, next: any) => next()),
    }

    mockAppConfig = {
      connectorBackend: 'http://localhost:8088',
      aiBackend: 'http://localhost:8000',
      storage: {
        endpoint: 'http://localhost:3003',
      },
      jwtSecret: 'test-jwt-secret',
      scopedJwtSecret: 'test-scoped-secret',
      cmBackend: 'http://localhost:3001',
    }

    mockRecordsEventProducer = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }

    mockSyncEventProducer = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    }

    mockKeyValueStore = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
      delete: sinon.stub().resolves(),
    }

    const mockPrometheusService = {
      recordActivity: sinon.stub(),
      getMetrics: sinon.stub().resolves(''),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind<RecordsEventProducer>('RecordsEventProducer').toConstantValue(mockRecordsEventProducer)
    container.bind<SyncEventProducer>('SyncEventProducer').toConstantValue(mockSyncEventProducer)
    container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(mockKeyValueStore)
    container.bind(PrometheusService).toConstantValue(mockPrometheusService as any)
  })

  afterEach(() => {
    sinon.restore()
  })

  it('should return a valid Express router', () => {
    const router = createKnowledgeBaseRouter(container)
    expect(router).to.exist
    expect(router).to.have.property('stack')
  })

  it('should register knowledge base CRUD routes', () => {
    const router = createKnowledgeBaseRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    // KB CRUD - root path / for POST (create) and GET (list)
    const postRoot = routes.find((r: any) => r.path === '/' && r.methods.post)
    expect(postRoot).to.exist
    const getRoot = routes.find((r: any) => r.path === '/' && r.methods.get)
    expect(getRoot).to.exist
  })

  it('should register record routes', () => {
    const router = createKnowledgeBaseRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/record/:recordId')
  })

  it('should register all records route', () => {
    const router = createKnowledgeBaseRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/records')
  })

  it('should register knowledge hub nodes route', () => {
    const router = createKnowledgeBaseRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/knowledge-hub/nodes')
  })

  it('should register stream record route', () => {
    const router = createKnowledgeBaseRouter(container)
    const routes = router.stack
      .filter((layer: any) => layer.route)
      .map((layer: any) => ({
        path: layer.route.path,
        methods: layer.route.methods,
      }))
    const paths = routes.map((r: any) => r.path)

    expect(paths).to.include('/stream/record/:recordId')
  })
})
