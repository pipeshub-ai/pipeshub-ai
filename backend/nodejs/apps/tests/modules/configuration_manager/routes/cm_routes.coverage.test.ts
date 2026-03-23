import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConfigurationManagerRouter } from '../../../../src/modules/configuration_manager/routes/cm_routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Configuration Manager Routes - handler coverage', () => {
  let container: Container
  let router: any

  beforeEach(() => {
    container = new Container()

    const mockAuthMiddleware = {
      authenticate: sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      scopedTokenValidator: sinon.stub().returns(
        sinon.stub().callsFake((_req: any, _res: any, next: any) => next()),
      ),
    }

    const mockKvStore = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
      delete: sinon.stub().resolves(),
      connect: sinon.stub().resolves(),
      watchKey: sinon.stub(),
    }

    const mockAppConfig = {
      storage: { type: 'local', config: {} },
      communicationBackend: 'http://localhost:3002',
      scopedJwtSecret: 'test-scoped-secret',
    }

    const mockEntityEventService = {
      publishEvent: sinon.stub().resolves(),
      start: sinon.stub().resolves(),
    }

    const mockSyncEventService = {
      publishEvent: sinon.stub().resolves(),
      start: sinon.stub().resolves(),
    }

    const mockConfigService = {
      updateConfig: sinon.stub().resolves(),
      getConfig: sinon.stub().resolves({}),
    }

    const mockPrometheusService = {
      recordActivity: sinon.stub(),
      getMetrics: sinon.stub().resolves(''),
    }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind('KeyValueStoreService').toConstantValue(mockKvStore as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind('EntitiesEventProducer').toConstantValue(mockEntityEventService as any)
    container.bind('SyncEventProducer').toConstantValue(mockSyncEventService as any)
    container.bind('ConfigService').toConstantValue(mockConfigService as any)
    container.bind(PrometheusService).toConstantValue(mockPrometheusService as any)

    router = createConfigurationManagerRouter(container)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('route registrations', () => {
    it('should register POST /storageConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/storageConfig' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /storageConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/storageConfig' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /internal/storageConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/internal/storageConfig' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register POST /smtpConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/smtpConfig' && l.route.methods.post,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register GET /smtpConfig route', () => {
      const layer = router.stack.find(
        (l: any) => l.route && l.route.path === '/smtpConfig' && l.route.methods.get,
      )
      expect(layer).to.not.be.undefined
    })

    it('should register auth config routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/authConfig/azureAd')
      expect(paths).to.include('/internal/authConfig/azureAd')
      expect(paths).to.include('/authConfig/microsoft')
      expect(paths).to.include('/internal/authConfig/microsoft')
      expect(paths).to.include('/authConfig/google')
      expect(paths).to.include('/internal/authConfig/google')
      expect(paths).to.include('/authConfig/sso')
      expect(paths).to.include('/internal/authConfig/sso')
      expect(paths).to.include('/authConfig/oauth')
      expect(paths).to.include('/internal/authConfig/oauth')
    })

    it('should register AI models config routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/aiModelsConfig')
      expect(paths).to.include('/internal/aiModelsConfig')
      expect(paths).to.include('/ai-models')
      expect(paths).to.include('/ai-models/:modelType')
      expect(paths).to.include('/ai-models/available/:modelType')
      expect(paths).to.include('/ai-models/providers')
      expect(paths).to.include('/ai-models/providers/:modelType/:modelKey')
      expect(paths).to.include('/ai-models/default/:modelType/:modelKey')
    })

    it('should register frontend URL routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/frontendPublicUrl')
    })

    it('should register connector URL routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/connectorPublicUrl')
    })

    it('should register metrics collection routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/metricsCollection/toggle')
      expect(paths).to.include('/internal/metricsCollection/toggle')
      expect(paths).to.include('/metricsCollection')
      expect(paths).to.include('/metricsCollection/pushInterval')
      expect(paths).to.include('/metricsCollection/serverUrl')
    })

    it('should register Google Workspace routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/connectors/googleWorkspaceCredentials')
      expect(paths).to.include('/internal/connectors/googleWorkspaceCredentials')
      expect(paths).to.include('/connectors/googleWorkspaceOauthConfig')
      expect(paths).to.include('/internal/connectors/googleWorkspaceOauthConfig')
    })

    it('should register connector config routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/connectors/atlassian/config')
      expect(paths).to.include('/internal/connectors/atlassian/config')
      expect(paths).to.include('/connectors/onedrive/config')
      expect(paths).to.include('/connectors/sharepoint/config')
      expect(paths).to.include('/internal/connectors/:connector/config')
    })

    it('should register platform settings routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/platform/settings')
      expect(paths).to.include('/platform/feature-flags/available')
    })

    it('should register Slack bot routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/slack-bot')
      expect(paths).to.include('/slack-bot/:configId')
    })

    it('should register custom system prompt routes', () => {
      const paths = router.stack
        .filter((l: any) => l.route)
        .map((l: any) => l.route.path)

      expect(paths).to.include('/prompts/system')
    })
  })
})
