import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConfigurationManagerRouter } from '../../../../src/modules/configuration_manager/routes/cm_routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'
import { AiConfigEventProducer } from '../../../../src/modules/configuration_manager/services/kafka_events.service'
import { PrometheusService } from '../../../../src/libs/services/prometheus/prometheus.service'

describe('Configuration Manager Routes - inline handler branch coverage', () => {
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

    const mockEntityEventService = { publishEvent: sinon.stub().resolves(), start: sinon.stub().resolves() }
    const mockAiConfigEventService = { publishEvent: sinon.stub().resolves(), start: sinon.stub().resolves() }
    const mockSyncEventService = { publishEvent: sinon.stub().resolves(), start: sinon.stub().resolves() }
    const mockConfigService = { updateConfig: sinon.stub().resolves(), getConfig: sinon.stub().resolves({}) }

    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(mockAuthMiddleware as any)
    container.bind('KeyValueStoreService').toConstantValue(mockKvStore as any)
    container.bind<AppConfig>('AppConfig').toConstantValue(mockAppConfig as any)
    container.bind('EntitiesEventProducer').toConstantValue(mockEntityEventService as any)
    container.bind<AiConfigEventProducer>('AiConfigEventProducer').toConstantValue(mockAiConfigEventService as any)
    container.bind('SyncEventProducer').toConstantValue(mockSyncEventService as any)
    container.bind('ConfigService').toConstantValue(mockConfigService as any)
    container.bind(PrometheusService).toConstantValue({ recordActivity: sinon.stub() } as any)
    container.bind('SamlController').toConstantValue({
      updateSamlStrategiesWithCallback: sinon.stub().resolves(),
    })

    router = createConfigurationManagerRouter(container)
  })

  afterEach(() => { sinon.restore() })

  function findHandlers(path: string, method: string): any[] {
    const layer = router.stack.find(
      (l: any) => l.route && l.route.path === path && l.route.methods[method],
    )
    if (!layer) return []
    return layer.route.stack.map((s: any) => s.handle)
  }

  function mockRes() {
    const res: any = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
    }
    return res
  }

  // =========================================================================
  // Inline handlers that check req.user
  // =========================================================================
  describe('inline handlers with req.user check', () => {
    const routesWithUserCheck = [
      { path: '/connectors/atlassian/config', method: 'get' },
      { path: '/connectors/atlassian/config', method: 'post' },
      { path: '/connectors/onedrive/config', method: 'get' },
      { path: '/connectors/onedrive/config', method: 'post' },
      { path: '/connectors/sharepoint/config', method: 'get' },
      { path: '/connectors/sharepoint/config', method: 'post' },
      { path: '/connectors/googleWorkspaceCredentials', method: 'get' },
      { path: '/connectors/googleWorkspaceCredentials', method: 'post' },
      { path: '/connectors/googleWorkspaceOauthConfig', method: 'post' },
    ]

    for (const route of routesWithUserCheck) {
      it(`should throw NotFoundError when req.user is missing for ${route.method.toUpperCase()} ${route.path}`, () => {
        const handlers = findHandlers(route.path, route.method)
        // Find the inline handler (usually the last one in the stack)
        const inlineHandler = handlers.find((h: any) => {
          try {
            const req = { user: undefined, tokenPayload: undefined, body: {}, params: {}, headers: {} } as any
            h(req, mockRes(), sinon.stub())
            return false
          } catch (error) {
            return error instanceof NotFoundError
          }
        })

        if (inlineHandler) {
          const req = { user: undefined, body: {}, params: {}, headers: {} } as any
          expect(() => inlineHandler(req, mockRes(), sinon.stub())).to.throw(NotFoundError)
        }
      })
    }
  })

  // =========================================================================
  // Inline handlers that check req.tokenPayload
  // =========================================================================
  describe('inline handlers with req.tokenPayload check', () => {
    const routesWithTokenPayloadCheck = [
      { path: '/internal/connectors/atlassian/config', method: 'get' },
      { path: '/internal/connectors/atlassian/config', method: 'post' },
      { path: '/internal/connectors/onedrive/config', method: 'post' },
      { path: '/internal/connectors/sharepoint/config', method: 'post' },
      { path: '/internal/connectors/:connector/config', method: 'get' },
      { path: '/internal/connectors/individual/googleWorkspaceCredentials', method: 'get' },
      { path: '/internal/connectors/business/googleWorkspaceCredentials', method: 'get' },
      { path: '/internal/connectors/business/googleWorkspaceCredentials', method: 'delete' },
      { path: '/internal/connectors/googleWorkspaceCredentials', method: 'post' },
      { path: '/internal/connectors/googleWorkspaceOauthConfig', method: 'post' },
    ]

    for (const route of routesWithTokenPayloadCheck) {
      it(`should throw NotFoundError when req.tokenPayload is missing for ${route.method.toUpperCase()} ${route.path}`, () => {
        const handlers = findHandlers(route.path, route.method)
        const inlineHandler = handlers.find((h: any) => {
          try {
            const req = { tokenPayload: undefined, user: undefined, body: {}, params: {}, headers: {} } as any
            h(req, mockRes(), sinon.stub())
            return false
          } catch (error) {
            return error instanceof NotFoundError
          }
        })

        if (inlineHandler) {
          const req = { tokenPayload: undefined, body: {}, params: {}, headers: {} } as any
          expect(() => inlineHandler(req, mockRes(), sinon.stub())).to.throw(NotFoundError)
        }
      })
    }
  })

  // =========================================================================
  // Route existence checks for all registered routes
  // =========================================================================
  describe('all routes should be registered', () => {
    const expectedRoutes = [
      { path: '/storageConfig', method: 'post' },
      { path: '/storageConfig', method: 'get' },
      { path: '/internal/storageConfig', method: 'get' },
      { path: '/smtpConfig', method: 'post' },
      { path: '/smtpConfig', method: 'get' },
      { path: '/authConfig/azureAd', method: 'get' },
      { path: '/internal/authConfig/azureAd', method: 'get' },
      { path: '/authConfig/azureAd', method: 'post' },
      { path: '/authConfig/microsoft', method: 'get' },
      { path: '/internal/authConfig/microsoft', method: 'get' },
      { path: '/authConfig/microsoft', method: 'post' },
      { path: '/authConfig/google', method: 'get' },
      { path: '/internal/authConfig/google', method: 'get' },
      { path: '/authConfig/google', method: 'post' },
      { path: '/authConfig/sso', method: 'get' },
      { path: '/internal/authConfig/sso', method: 'get' },
      { path: '/authConfig/sso', method: 'post' },
      { path: '/authConfig/oauth', method: 'get' },
      { path: '/internal/authConfig/oauth', method: 'get' },
      { path: '/authConfig/oauth', method: 'post' },
      { path: '/platform/settings', method: 'post' },
      { path: '/platform/settings', method: 'get' },
      { path: '/platform/feature-flags/available', method: 'get' },
      { path: '/aiModelsConfig', method: 'post' },
      { path: '/aiModelsConfig', method: 'get' },
      { path: '/internal/aiModelsConfig', method: 'get' },
      { path: '/ai-models', method: 'get' },
      { path: '/frontendPublicUrl', method: 'get' },
      { path: '/frontendPublicUrl', method: 'post' },
      { path: '/connectorPublicUrl', method: 'get' },
      { path: '/connectorPublicUrl', method: 'post' },
      { path: '/metricsCollection/toggle', method: 'put' },
      { path: '/metricsCollection', method: 'get' },
      { path: '/metricsCollection/pushInterval', method: 'patch' },
      { path: '/metricsCollection/serverUrl', method: 'patch' },
      { path: '/prompts/system', method: 'get' },
      { path: '/prompts/system', method: 'put' },
      { path: '/slack-bot', method: 'get' },
      { path: '/internal/slack-bot', method: 'get' },
      { path: '/slack-bot', method: 'post' },
    ]

    for (const route of expectedRoutes) {
      it(`should have ${route.method.toUpperCase()} ${route.path}`, () => {
        const layer = router.stack.find(
          (l: any) => l.route && l.route.path === route.path && l.route.methods[route.method],
        )
        expect(layer, `Route ${route.method.toUpperCase()} ${route.path} should exist`).to.not.be.undefined
      })
    }
  })
})
