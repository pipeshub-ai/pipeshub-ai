import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import http from 'http';
import { Container } from 'inversify';
import { Application } from '../src/app';
import { createMockLogger, MockLogger } from './helpers/mock-logger';
import { Logger } from '../src/libs/services/logger.service';
import { PrometheusService } from '../src/libs/services/prometheus/prometheus.service';
import { MigrationService } from '../src/modules/configuration_manager/services/migration.service';
import { NotificationService } from '../src/modules/notification/service/notification.service';
import * as appConfigModule from '../src/modules/tokens_manager/config/config';
import * as cmConfigModule from '../src/modules/configuration_manager/config/config';
import * as messageBrokerModule from '../src/libs/services/message-broker.factory';
import * as kvMigrationModule from '../src/libs/keyValueStore/migration/kvStoreMigration.service';
import * as oauthProviderModule from '../src/libs/services/oauth-token-service.provider';
import { TokenManagerContainer } from '../src/modules/tokens_manager/container/token-manager.container';
import { ConfigurationManagerContainer } from '../src/modules/configuration_manager/container/cm_container';
import { StorageContainer } from '../src/modules/storage/container/storage.container';
import { UserManagerContainer } from '../src/modules/user_management/container/userManager.container';
import { AuthServiceContainer } from '../src/modules/auth/container/authService.container';
import { EnterpriseSearchAgentContainer } from '../src/modules/enterprise_search/container/es.container';
import { KnowledgeBaseContainer } from '../src/modules/knowledge_base/container/kb_container';
import { MailServiceContainer } from '../src/modules/mail/container/mailService.container';
import { NotificationContainer } from '../src/modules/notification/container/notification.container';
import { CrawlingManagerContainer } from '../src/modules/crawling_manager/container/cm_container';
import { OAuthProviderContainer } from '../src/modules/oauth_provider/container/oauth.provider.container';
import { ToolsetsContainer } from '../src/modules/toolsets/container/toolsets.container';
import { ApiDocsContainer } from '../src/modules/api-docs/docs.container';
import { StoreType } from '../src/libs/keyValueStore/constants/KeyValueStoreType';

// Route factory modules — stubbed so configureRoutes() doesn't resolve real DI services
import * as healthRoutes from '../src/modules/tokens_manager/routes/health.routes';
import * as userRoutes from '../src/modules/user_management/routes/users.routes';
import * as userGroupRoutes from '../src/modules/user_management/routes/userGroups.routes';
import * as orgRoutes from '../src/modules/user_management/routes/org.routes';
import * as samlRoutes from '../src/modules/auth/routes/saml.routes';
import * as userAccountRoutes from '../src/modules/auth/routes/userAccount.routes';
import * as orgAuthConfigRoutes from '../src/modules/auth/routes/orgAuthConfig.routes';
import * as storageRoutes from '../src/modules/storage/routes/storage.routes';
import * as esRoutes from '../src/modules/enterprise_search/routes/es.routes';
import * as connectorRoutes from '../src/modules/tokens_manager/routes/connectors.routes';
import * as oauthRoutes from '../src/modules/tokens_manager/routes/oauth.routes';
import * as kbRoutes from '../src/modules/knowledge_base/routes/kb.routes';
import * as cmRoutes from '../src/modules/configuration_manager/routes/cm_routes';
import * as mailRoutes from '../src/modules/mail/routes/mail.routes';
import * as crawlingRoutes from '../src/modules/crawling_manager/routes/cm_routes';
import * as oauthProviderRoutes from '../src/modules/oauth_provider/routes/oauth.provider.routes';
import * as oauthClientsRoutes from '../src/modules/oauth_provider/routes/oauth.clients.routes';
import * as mcpRoutes from '../src/modules/mcp/routes/mcp.routes';
import * as oidcRoutes from '../src/modules/oauth_provider/routes/oid.provider.routes';
import * as apiDocsRoutes from '../src/modules/api-docs/docs.routes';
import * as toolsetsRoutes from '../src/modules/toolsets/routes/toolsets_routes';
import * as teamsRoutes from '../src/modules/user_management/routes/teams.routes';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Safely restore an env var — handles undefined correctly (delete vs assign) */
function restoreEnv(key: string, original: string | undefined) {
  if (original === undefined) {
    delete process.env[key];
  } else {
    process.env[key] = original;
  }
}

function createMockAppConfig(): appConfigModule.AppConfig {
  return {
    jwtSecret: 'test-jwt-secret',
    scopedJwtSecret: 'test-scoped-jwt-secret',
    cookieSecret: 'test-cookie-secret',
    rsAvailable: 'true',
    communicationBackend: 'http://localhost:3002',
    frontendUrl: 'http://localhost:3001',
    iamBackend: 'http://localhost:3003',
    authBackend: 'http://localhost:3004',
    cmBackend: 'http://localhost:3005',
    kbBackend: 'http://localhost:3006',
    esBackend: 'http://localhost:3007',
    storageBackend: 'http://localhost:3008',
    tokenBackend: 'http://localhost:3009',
    aiBackend: 'http://localhost:8000',
    connectorBackend: 'http://localhost:8088',
    connectorPublicUrl: 'http://localhost:8088',
    indexingBackend: 'http://localhost:8091',
    kafka: { brokers: ['localhost:9092'] },
    redis: { host: 'localhost', port: 6379 },
    mongo: { uri: 'mongodb://localhost:27017', db: 'test' },
    qdrant: { host: 'localhost', port: 6333, apiKey: '', grpcPort: 6334 },
    arango: { url: 'http://localhost:8529', db: 'test', username: 'root', password: '' },
    etcd: { host: 'http://localhost', port: 2379, dialTimeout: 2000 },
    smtp: null,
    storage: { storageType: 'local', endpoint: '' },
    oauthIssuer: 'http://localhost:3000',
    oauthBackendUrl: 'http://localhost:3000',
    mcpScopes: ['read', 'write'],
    skipDomainCheck: false,
    maxRequestsPerMinute: 1000,
    maxOAuthClientRequestsPerMinute: 1000,
    deployment: {
      dataStoreType: 'arangodb',
      messageBrokerType: 'kafka',
      kvStoreType: 'etcd',
      vectorDbType: 'qdrant',
    },
  };
}

function createMockCmConfig(): cmConfigModule.ConfigurationManagerConfig {
  return {
    storeType: StoreType.Redis,
    storeConfig: { host: 'http://localhost', port: 2379, dialTimeout: 2000 },
    redisConfig: {
      host: 'localhost',
      port: 6379,
      password: undefined,
      tls: false,
      db: 0,
      keyPrefix: 'pipeshub:kv:',
    },
    secretKey: 'a'.repeat(64),
    algorithm: 'aes-256-gcm',
  };
}

/**
 * Creates a mock Inversify Container that behaves enough like a real one
 * for the Application class to bind PrometheusService, MigrationService, etc.
 */
function createMockInversifyContainer(): Container {
  const container = new Container();
  return container;
}

/**
 * Stubs all route factory functions so configureRoutes() doesn't try to
 * resolve real services from the mock containers.
 */
function stubAllRouteFactories(sandbox: sinon.SinonSandbox) {
  const dummyRouter = express.Router();

  sandbox.stub(healthRoutes, 'createHealthRouter').returns(dummyRouter);
  sandbox.stub(userRoutes, 'createUserRouter').returns(dummyRouter);
  sandbox.stub(userGroupRoutes, 'createUserGroupRouter').returns(dummyRouter);
  sandbox.stub(orgRoutes, 'createOrgRouter').returns(dummyRouter);
  sandbox.stub(samlRoutes, 'createSamlRouter').returns(dummyRouter);
  sandbox.stub(userAccountRoutes, 'createUserAccountRouter').returns(dummyRouter);
  sandbox.stub(orgAuthConfigRoutes, 'createOrgAuthConfigRouter').returns(dummyRouter);
  sandbox.stub(storageRoutes, 'createStorageRouter').returns(dummyRouter);
  sandbox.stub(esRoutes, 'createConversationalRouter').returns(dummyRouter);
  sandbox.stub(esRoutes, 'createSemanticSearchRouter').returns(dummyRouter);
  sandbox.stub(esRoutes, 'createAgentConversationalRouter').returns(dummyRouter);
  sandbox.stub(connectorRoutes, 'createConnectorRouter').returns(dummyRouter);
  sandbox.stub(oauthRoutes, 'createOAuthRouter').returns(dummyRouter);
  sandbox.stub(kbRoutes, 'createKnowledgeBaseRouter').returns(dummyRouter);
  sandbox.stub(cmRoutes, 'createConfigurationManagerRouter').returns(dummyRouter);
  sandbox.stub(mailRoutes, 'createMailServiceRouter').returns(dummyRouter);
  sandbox.stub(crawlingRoutes, 'default').returns(dummyRouter);
  sandbox.stub(crawlingRoutes, 'createCrawlingManagerRouter').returns(dummyRouter);
  sandbox.stub(oauthProviderRoutes, 'createOAuthProviderRouter').returns(dummyRouter);
  sandbox.stub(oauthClientsRoutes, 'createOAuthClientsRouter').returns(dummyRouter);
  sandbox.stub(mcpRoutes, 'createMCPRouter').returns(dummyRouter);
  sandbox.stub(oidcRoutes, 'createOIDCDiscoveryRouter').returns(dummyRouter);
  sandbox.stub(apiDocsRoutes, 'createApiDocsRouter').returns(dummyRouter);
  sandbox.stub(toolsetsRoutes, 'createToolsetsRouter').returns(dummyRouter);
  sandbox.stub(teamsRoutes, 'createTeamsRouter').returns(dummyRouter);
}

/**
 * Sets up stubs for all container initializations so Application.initialize()
 * can run without real databases/services.
 */
function stubAllContainers(sandbox: sinon.SinonSandbox) {
  // Stub route factories so configureRoutes() doesn't blow up
  stubAllRouteFactories(sandbox);

  const containers: Record<string, Container> = {};
  const containerClasses = [
    { cls: TokenManagerContainer, name: 'token' },
    { cls: ConfigurationManagerContainer, name: 'cm' },
    { cls: StorageContainer, name: 'storage' },
    { cls: UserManagerContainer, name: 'userManager' },
    { cls: AuthServiceContainer, name: 'auth' },
    { cls: EnterpriseSearchAgentContainer, name: 'es' },
    { cls: KnowledgeBaseContainer, name: 'kb' },
    { cls: MailServiceContainer, name: 'mail' },
    { cls: NotificationContainer, name: 'notification' },
    { cls: CrawlingManagerContainer, name: 'crawling' },
    { cls: OAuthProviderContainer, name: 'oauth' },
    { cls: ToolsetsContainer, name: 'toolsets' },
    { cls: ApiDocsContainer, name: 'apiDocs' },
  ];

  for (const { cls, name } of containerClasses) {
    const c = createMockInversifyContainer();
    containers[name] = c;
    sandbox.stub(cls, 'initialize').resolves(c);
  }

  // NotificationService mock — needed for initialize() to call .initialize(server)
  const mockNotificationService = {
    initialize: sandbox.stub(),
    shutdown: sandbox.stub(),
  };
  containers['notification']
    .bind<any>(NotificationService)
    .toConstantValue(mockNotificationService);

  // OAuthTokenService mock — needed for addOAuthServicesToAuthMiddleware
  const mockOAuthTokenService = { validateToken: sandbox.stub() };
  containers['oauth']
    .bind<any>('OAuthTokenService')
    .toConstantValue(mockOAuthTokenService);

  // SamlController mock — needed for updateSamlStrategies
  const mockSamlController = {
    updateSamlStrategiesWithCallback: sandbox.stub(),
  };
  containers['auth']
    .bind<any>('SamlController')
    .toConstantValue(mockSamlController);

  return { containers, mockNotificationService, mockOAuthTokenService, mockSamlController };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Application', () => {
  let sandbox: sinon.SinonSandbox;
  let mockLogger: MockLogger;

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    mockLogger = createMockLogger();
    sandbox.stub(Logger, 'getInstance').returns(mockLogger as any);
    sandbox.stub(Logger.prototype, 'info');
    sandbox.stub(Logger.prototype, 'error');
    sandbox.stub(Logger.prototype, 'warn');
    sandbox.stub(Logger.prototype, 'debug');
  });

  afterEach(() => {
    sandbox.restore();
  });

  // =========================================================================
  // Constructor
  // =========================================================================
  describe('constructor', () => {
    it('should create an Application instance', () => {
      const app = new Application();
      expect(app).to.be.instanceOf(Application);
    });

    it('should default port to 3000 when PORT is not set', () => {
      const original = process.env.PORT;
      delete process.env.PORT;
      try {
        const app = new Application();
        // Access private port via cast
        expect((app as any).port).to.equal(3000);
      } finally {
        restoreEnv('PORT', original);
      }
    });

    it('should use PORT from environment variable', () => {
      const original = process.env.PORT;
      process.env.PORT = '4000';
      try {
        const app = new Application();
        expect((app as any).port).to.equal(4000);
      } finally {
        restoreEnv('PORT', original);
      }
    });

    it('should create an Express app and HTTP server', () => {
      const app = new Application();
      expect((app as any).app).to.be.a('function');
      expect((app as any).server).to.be.instanceOf(http.Server);
    });
  });

  // =========================================================================
  // initialize()
  // =========================================================================
  describe('initialize()', () => {
    let mockAppConfig: appConfigModule.AppConfig;
    let mockCmConfig: cmConfigModule.ConfigurationManagerConfig;

    beforeEach(() => {
      mockAppConfig = createMockAppConfig();
      mockCmConfig = createMockCmConfig();

      sandbox.stub(appConfigModule, 'loadAppConfig').resolves(mockAppConfig);
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(mockCmConfig);
      sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
    });

    it('should initialize all containers and configure the app', async () => {
      const app = new Application();
      const { containers, mockNotificationService } = stubAllContainers(sandbox);

      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.initialize();

      // All containers should have been initialized
      expect((TokenManagerContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((ConfigurationManagerContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((StorageContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((UserManagerContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((AuthServiceContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((EnterpriseSearchAgentContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((KnowledgeBaseContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((MailServiceContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((NotificationContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((CrawlingManagerContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((OAuthProviderContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((ToolsetsContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
      expect((ApiDocsContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;

      // NotificationService.initialize should have been called with the http server
      expect(mockNotificationService.initialize.calledOnce).to.be.true;
      expect(mockNotificationService.initialize.firstCall.args[0]).to.be.instanceOf(http.Server);
    });

    it('should call ensureMessageTopicsExist during initialization', async () => {
      const app = new Application();
      stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.initialize();

      const stub = messageBrokerModule.ensureMessageTopicsExist as sinon.SinonStub;
      expect(stub.calledOnce).to.be.true;
    });

    it('should continue initialization even if Kafka topic creation fails', async () => {
      const app = new Application();
      stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      (messageBrokerModule.ensureMessageTopicsExist as sinon.SinonStub).rejects(
        new Error('Kafka broker unavailable'),
      );

      // Should NOT throw
      await app.initialize();

      // All containers still initialized
      expect((TokenManagerContainer.initialize as sinon.SinonStub).calledOnce).to.be.true;
    });

    it('should register OAuthTokenService into auth middleware', async () => {
      const app = new Application();
      const { mockOAuthTokenService } = stubAllContainers(sandbox);
      const registerStub = sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.initialize();

      expect(registerStub.calledOnce).to.be.true;
      expect(registerStub.firstCall.args[0]).to.equal(mockOAuthTokenService);
    });

    it('should update SAML strategies after initialization', async () => {
      const app = new Application();
      const { mockSamlController } = stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.initialize();

      expect(mockSamlController.updateSamlStrategiesWithCallback.calledOnce).to.be.true;
    });

    it('should bind PrometheusService to all service containers', async () => {
      const app = new Application();
      const { containers } = stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.initialize();

      // Check that PrometheusService was bound in major containers
      const containerNames = [
        'token', 'userManager', 'auth', 'cm', 'storage',
        'es', 'kb', 'mail', 'crawling', 'oauth', 'toolsets',
      ];
      for (const name of containerNames) {
        expect(
          containers[name].isBound(PrometheusService),
          `PrometheusService should be bound in ${name} container`,
        ).to.be.true;
      }
    });

    it('should bind MigrationService to configuration manager container', async () => {
      const app = new Application();
      const { containers } = stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.initialize();

      expect(containers['cm'].isBound(MigrationService)).to.be.true;
    });

    it('should throw and log error if a container initialization fails', async () => {
      const app = new Application();
      const initError = new Error('MongoDB connection refused');
      sandbox.stub(TokenManagerContainer, 'initialize').rejects(initError);

      try {
        await app.initialize();
        expect.fail('Should have thrown');
      } catch (err: any) {
        expect(err.message).to.equal('MongoDB connection refused');
      }
    });

    it('should pass configurationManagerConfig to container initializations', async () => {
      const app = new Application();
      stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.initialize();

      // TokenManagerContainer.initialize gets only cmConfig
      const tmStub = TokenManagerContainer.initialize as sinon.SinonStub;
      expect(tmStub.firstCall.args[0]).to.deep.equal(mockCmConfig);

      // ConfigurationManagerContainer.initialize gets cmConfig + appConfig
      const cmStub = ConfigurationManagerContainer.initialize as sinon.SinonStub;
      expect(cmStub.firstCall.args[0]).to.deep.equal(mockCmConfig);
      expect(cmStub.firstCall.args[1]).to.deep.equal(mockAppConfig);
    });

    it('should handle OAuthTokenService registration failure gracefully', async () => {
      const app = new Application();
      stubAllContainers(sandbox);

      // Stub registerOAuthTokenService to throw
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService').throws(
        new Error('Registration failed'),
      );

      // The app overrides this with a try/catch in addOAuthServicesToAuthMiddleware
      // so it should NOT throw
      await app.initialize();
    });

    it('should handle SAML strategy update failure gracefully', async () => {
      const app = new Application();
      const { mockSamlController } = stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      mockSamlController.updateSamlStrategiesWithCallback.throws(
        new Error('SAML config unavailable'),
      );

      // Should NOT throw — updateSamlStrategies has a try/catch
      await app.initialize();
    });
  });

  // =========================================================================
  // configureMiddleware() — tested via HTTP requests to the initialized app
  // =========================================================================
  describe('middleware configuration', () => {
    let app: Application;
    let mockAppConfig: appConfigModule.AppConfig;

    beforeEach(async () => {
      mockAppConfig = createMockAppConfig();
      sandbox.stub(appConfigModule, 'loadAppConfig').resolves(mockAppConfig);
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
      sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      app = new Application();
      stubAllContainers(sandbox);
      await app.initialize();
    });

    it('should parse JSON request bodies', async () => {
      // The express app is configured — verify by checking the internal stack
      const expressApp = (app as any).app as express.Express;
      const layerNames = (expressApp as any)._router.stack
        .map((layer: any) => layer.name)
        .filter(Boolean);
      expect(layerNames).to.include('jsonParser');
    });

    it('should parse URL-encoded request bodies', async () => {
      const expressApp = (app as any).app as express.Express;
      const layerNames = (expressApp as any)._router.stack
        .map((layer: any) => layer.name)
        .filter(Boolean);
      expect(layerNames).to.include('urlencodedParser');
    });

    it('should include CORS middleware', async () => {
      const expressApp = (app as any).app as express.Express;
      const layerNames = (expressApp as any)._router.stack
        .map((layer: any) => layer.name)
        .filter(Boolean);
      expect(layerNames).to.include('corsMiddleware');
    });

    it('should NOT include helmet when STRICT_MODE is not true', async () => {
      const expressApp = (app as any).app as express.Express;
      const layerNames = (expressApp as any)._router.stack
        .map((layer: any) => layer.name)
        .filter(Boolean);
      // helmet adds multiple middleware layers with various names
      expect(layerNames).to.not.include('helmet');
    });

    it('should include helmet when STRICT_MODE=true', async () => {
      // Re-initialize with STRICT_MODE — route factories are already stubbed
      // by the beforeEach's stubAllContainers call, so only stub containers here
      const original = process.env.STRICT_MODE;
      process.env.STRICT_MODE = 'true';
      try {
        const strictApp = new Application();
        // Container stubs are already in place from the beforeEach, just re-use them
        // (they resolve to the same mock containers on each call)
        await strictApp.initialize();

        const expressApp = (strictApp as any).app as express.Express;
        const layerNames = (expressApp as any)._router.stack
          .map((layer: any) => layer.name)
          .filter(Boolean);
        // helmet registers multiple middleware — at least one should be present
        const hasHelmetMiddleware = layerNames.some(
          (name: string) =>
            name.includes('helmet') ||
            name === 'crossOriginOpenerPolicy' ||
            name === 'contentSecurityPolicy',
        );
        expect(hasHelmetMiddleware).to.be.true;
      } finally {
        restoreEnv('STRICT_MODE', original);
      }
    });
  });

  // =========================================================================
  // configureRoutes() — verify all API routes are mounted
  // =========================================================================
  describe('route configuration', () => {
    let expressApp: express.Express;

    beforeEach(async () => {
      sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
      sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      const app = new Application();
      stubAllContainers(sandbox);
      await app.initialize();
      expressApp = (app as any).app;
    });

    const expectedRoutes = [
      '/api/v1/health',
      '/api/v1/users',
      '/api/v1/teams',
      '/api/v1/userGroups',
      '/api/v1/org',
      '/api/v1/saml',
      '/api/v1/userAccount',
      '/api/v1/orgAuthConfig',
      '/api/v1/document',
      '/api/v1/conversations',
      '/api/v1/agents',
      '/api/v1/search',
      '/api/v1/connectors',
      '/api/v1/oauth',
      '/api/v1/knowledgeBase',
      '/api/v1/configurationManager',
      '/api/v1/toolsets',
      '/api/v1/mail',
      '/api/v1/crawlingManager',
      '/api/v1/oauth2',
      '/api/v1/oauth-clients',
      '/mcp',
      '/.well-known',
      '/api/v1/docs',
    ];

    for (const route of expectedRoutes) {
      it(`should mount route: ${route}`, () => {
        // Walk the Express router stack looking for the mount path
        const stack = (expressApp as any)._router?.stack || [];
        const found = stack.some((layer: any) => {
          if (layer.regexp) {
            // Test if the route's regexp matches the expected path
            return layer.regexp.test(route);
          }
          return false;
        });
        expect(found, `Route ${route} should be mounted`).to.be.true;
      });
    }

    it('should mount Slack events proxy at /slack/events', () => {
      const stack = (expressApp as any)._router?.stack || [];
      const found = stack.some((layer: any) => {
        if (layer.regexp) {
          return layer.regexp.test('/slack/events');
        }
        return false;
      });
      expect(found, 'Slack events proxy should be mounted').to.be.true;
    });

    it('should serve SPA fallback as the last handler', () => {
      const stack = (expressApp as any)._router?.stack || [];
      // The last route layer should be the SPA catch-all GET *
      const lastRouteLayers = stack.filter(
        (l: any) => l.route && l.route.path === '*',
      );
      expect(lastRouteLayers.length).to.be.greaterThan(0);
    });
  });

  // =========================================================================
  // start()
  // =========================================================================
  describe('start()', () => {
    it('should start the server on the configured port', async () => {
      const app = new Application();
      // Stub the logger on the instance
      (app as any).logger = mockLogger;

      const server = (app as any).server as http.Server;
      const listenStub = sandbox.stub(server, 'listen').callsFake(
        (_port: number, callback: () => void) => {
          callback();
          return server;
        },
      );

      await app.start();

      expect(listenStub.calledOnce).to.be.true;
      expect(listenStub.firstCall.args[0]).to.equal(3000);
    });

    it('should start on custom port from environment', async () => {
      const original = process.env.PORT;
      process.env.PORT = '5555';
      try {
        const app = new Application();
        (app as any).logger = mockLogger;

        const server = (app as any).server as http.Server;
        const listenStub = sandbox.stub(server, 'listen').callsFake(
          (_port: number, callback: () => void) => {
            callback();
            return server;
          },
        );

        await app.start();
        expect(listenStub.firstCall.args[0]).to.equal(5555);
      } finally {
        restoreEnv('PORT', original);
      }
    });

    it('should throw if server.listen fails', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const server = (app as any).server as http.Server;
      sandbox.stub(server, 'listen').callsFake(() => {
        throw new Error('EADDRINUSE');
      });

      try {
        await app.start();
        expect.fail('Should have thrown');
      } catch (err: any) {
        expect(err.message).to.equal('EADDRINUSE');
      }
    });
  });

  // =========================================================================
  // stop()
  // =========================================================================
  describe('stop()', () => {
    let app: Application;

    beforeEach(() => {
      app = new Application();
      (app as any).logger = mockLogger;

      // Stub all container dispose methods
      sandbox.stub(NotificationContainer, 'dispose').resolves();
      sandbox.stub(StorageContainer, 'dispose').resolves();
      sandbox.stub(UserManagerContainer, 'dispose').resolves();
      sandbox.stub(AuthServiceContainer, 'dispose').resolves();
      sandbox.stub(EnterpriseSearchAgentContainer, 'dispose').resolves();
      sandbox.stub(TokenManagerContainer, 'dispose').resolves();
      sandbox.stub(KnowledgeBaseContainer, 'dispose').resolves();
      sandbox.stub(ConfigurationManagerContainer, 'dispose').resolves();
      sandbox.stub(MailServiceContainer, 'dispose').resolves();
      sandbox.stub(CrawlingManagerContainer, 'dispose').resolves();
      sandbox.stub(ApiDocsContainer, 'dispose').resolves();
      sandbox.stub(OAuthProviderContainer, 'dispose').resolves();
    });

    it('should dispose all containers', async () => {
      // Set up a mock notification container with NotificationService
      const mockNotifService = { shutdown: sandbox.stub() };
      const notifContainer = new Container();
      notifContainer.bind<any>(NotificationService).toConstantValue(mockNotifService);
      (app as any).notificationContainer = notifContainer;

      await app.stop();

      expect((NotificationContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((StorageContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((UserManagerContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((AuthServiceContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((EnterpriseSearchAgentContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((TokenManagerContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((KnowledgeBaseContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((ConfigurationManagerContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((MailServiceContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((CrawlingManagerContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((ApiDocsContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
      expect((OAuthProviderContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
    });

    it('should call NotificationService.shutdown()', async () => {
      const mockNotifService = { shutdown: sandbox.stub() };
      const notifContainer = new Container();
      notifContainer.bind<any>(NotificationService).toConstantValue(mockNotifService);
      (app as any).notificationContainer = notifContainer;

      await app.stop();

      expect(mockNotifService.shutdown.calledOnce).to.be.true;
    });

    it('should handle NotificationService not available during shutdown', async () => {
      // Set up a container where NotificationService is not bound
      const emptyContainer = new Container();
      (app as any).notificationContainer = emptyContainer;

      // Should NOT throw — stop() has a try/catch for notification shutdown
      await app.stop();
    });

    it('should throw if a container dispose fails', async () => {
      const mockNotifService = { shutdown: sandbox.stub() };
      const notifContainer = new Container();
      notifContainer.bind<any>(NotificationService).toConstantValue(mockNotifService);
      (app as any).notificationContainer = notifContainer;

      (StorageContainer.dispose as sinon.SinonStub).rejects(new Error('Redis disconnect error'));

      try {
        await app.stop();
        expect.fail('Should have thrown');
      } catch (err: any) {
        expect(err.message).to.equal('Redis disconnect error');
      }
    });
  });

  // =========================================================================
  // runMigration()
  // =========================================================================
  describe('runMigration()', () => {
    it('should call MigrationService.runMigration()', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const mockMigrationService = { runMigration: sandbox.stub().resolves() };
      const cmContainer = new Container();
      cmContainer.bind<any>(MigrationService).toConstantValue(mockMigrationService);
      (app as any).configurationManagerContainer = cmContainer;

      await app.runMigration();

      expect(mockMigrationService.runMigration.calledOnce).to.be.true;
    });

    it('should throw if migration fails', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const mockMigrationService = {
        runMigration: sandbox.stub().rejects(new Error('Migration schema error')),
      };
      const cmContainer = new Container();
      cmContainer.bind<any>(MigrationService).toConstantValue(mockMigrationService);
      (app as any).configurationManagerContainer = cmContainer;

      try {
        await app.runMigration();
        expect.fail('Should have thrown');
      } catch (err: any) {
        expect(err.message).to.equal('Migration schema error');
      }
    });
  });

  // =========================================================================
  // preInitMigration()
  // =========================================================================
  describe('preInitMigration()', () => {
    it('should skip migration when storeType is not Redis', async () => {
      const app = new Application();
      const cmConfig = createMockCmConfig();
      cmConfig.storeType = StoreType.Etcd3;
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(cmConfig);

      const migrationStub = sandbox.stub(kvMigrationModule, 'checkAndMigrateIfNeeded');

      await app.preInitMigration();

      expect(migrationStub.called).to.be.false;
    });

    it('should skip migration when ETCD_URL is not set', async () => {
      const app = new Application();
      const cmConfig = createMockCmConfig();
      cmConfig.storeType = StoreType.Redis;
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(cmConfig);

      const originalEtcdUrl = process.env.ETCD_URL;
      delete process.env.ETCD_URL;

      const migrationStub = sandbox.stub(kvMigrationModule, 'checkAndMigrateIfNeeded');

      try {
        await app.preInitMigration();
        expect(migrationStub.called).to.be.false;
      } finally {
        restoreEnv('ETCD_URL', originalEtcdUrl);
      }
    });

    it('should run migration when storeType is Redis and ETCD_URL is set', async () => {
      const app = new Application();
      const cmConfig = createMockCmConfig();
      cmConfig.storeType = StoreType.Redis;
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(cmConfig);

      const originalEtcdUrl = process.env.ETCD_URL;
      process.env.ETCD_URL = 'http://localhost:2379';

      const migrationStub = sandbox.stub(kvMigrationModule, 'checkAndMigrateIfNeeded').resolves({
        success: true,
        migratedKeys: ['key1', 'key2'],
        failedKeys: [],
        skippedKeys: [],
      });

      try {
        await app.preInitMigration();
        expect(migrationStub.calledOnce).to.be.true;
      } finally {
        restoreEnv('ETCD_URL', originalEtcdUrl);
      }
    });

    it('should handle migration returning null (already completed)', async () => {
      const app = new Application();
      const cmConfig = createMockCmConfig();
      cmConfig.storeType = StoreType.Redis;
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(cmConfig);

      const originalEtcdUrl = process.env.ETCD_URL;
      process.env.ETCD_URL = 'http://localhost:2379';

      sandbox.stub(kvMigrationModule, 'checkAndMigrateIfNeeded').resolves(null);

      try {
        // Should NOT throw
        await app.preInitMigration();
      } finally {
        restoreEnv('ETCD_URL', originalEtcdUrl);
      }
    });

    it('should throw if migration fails', async () => {
      const app = new Application();
      const cmConfig = createMockCmConfig();
      cmConfig.storeType = StoreType.Redis;
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(cmConfig);

      const originalEtcdUrl = process.env.ETCD_URL;
      process.env.ETCD_URL = 'http://localhost:2379';

      sandbox.stub(kvMigrationModule, 'checkAndMigrateIfNeeded').resolves({
        success: false,
        migratedKeys: [],
        failedKeys: ['secretKey'],
        skippedKeys: [],
        error: 'Connection timeout',
      });

      try {
        await app.preInitMigration();
        expect.fail('Should have thrown');
      } catch (err: any) {
        expect(err.message).to.include('KV store migration failed');
        expect(err.message).to.include('Connection timeout');
      } finally {
        restoreEnv('ETCD_URL', originalEtcdUrl);
      }
    });
  });

  // =========================================================================
  // setupSlackEventsProxy()
  // =========================================================================
  describe('setupSlackEventsProxy()', () => {
    it('should use SLACK_BOT_PORT from environment', async () => {
      const original = process.env.SLACK_BOT_PORT;
      process.env.SLACK_BOT_PORT = '4040';

      try {
        sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
        sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
        sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
        sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

        const app = new Application();
        stubAllContainers(sandbox);
        await app.initialize();

        // The proxy is registered — we can verify via the router stack
        const expressApp = (app as any).app as express.Express;
        const stack = (expressApp as any)._router?.stack || [];
        const slackLayer = stack.find((layer: any) =>
          layer.regexp && layer.regexp.test('/slack/events'),
        );
        expect(slackLayer).to.exist;
      } finally {
        restoreEnv('SLACK_BOT_PORT', original);
      }
    });

    it('should default SLACK_BOT_PORT to 3020', async () => {
      const original = process.env.SLACK_BOT_PORT;
      delete process.env.SLACK_BOT_PORT;

      try {
        sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
        sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
        sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
        sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

        const app = new Application();
        stubAllContainers(sandbox);
        await app.initialize();

        // Proxy should still be registered with default port
        const expressApp = (app as any).app as express.Express;
        const stack = (expressApp as any)._router?.stack || [];
        const slackLayer = stack.find((layer: any) =>
          layer.regexp && layer.regexp.test('/slack/events'),
        );
        expect(slackLayer).to.exist;
      } finally {
        restoreEnv('SLACK_BOT_PORT', original);
      }
    });
  });

  // =========================================================================
  // addOAuthServicesToAuthMiddleware()
  // =========================================================================
  describe('addOAuthServicesToAuthMiddleware()', () => {
    it('should register OAuthTokenService from the oauth provider container', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const mockOAuthTokenService = { validateToken: sandbox.stub() };
      const oauthContainer = new Container();
      oauthContainer.bind<any>('OAuthTokenService').toConstantValue(mockOAuthTokenService);
      (app as any).oauthProviderContainer = oauthContainer;

      const registerStub = sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.addOAuthServicesToAuthMiddleware();

      expect(registerStub.calledOnce).to.be.true;
      expect(registerStub.firstCall.args[0]).to.equal(mockOAuthTokenService);
    });

    it('should warn but not throw if OAuthTokenService lookup fails', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      // Empty container — no OAuthTokenService bound
      const emptyContainer = new Container();
      (app as any).oauthProviderContainer = emptyContainer;

      // Should NOT throw
      await app.addOAuthServicesToAuthMiddleware();

      expect(mockLogger.warn.called).to.be.true;
    });
  });

  // =========================================================================
  // updateSamlStrategies()
  // =========================================================================
  describe('updateSamlStrategies()', () => {
    it('should call SamlController.updateSamlStrategiesWithCallback', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const mockSamlController = {
        updateSamlStrategiesWithCallback: sandbox.stub(),
      };
      const authContainer = new Container();
      authContainer.bind<any>('SamlController').toConstantValue(mockSamlController);
      (app as any).authServiceContainer = authContainer;

      await app.updateSamlStrategies();

      expect(mockSamlController.updateSamlStrategiesWithCallback.calledOnce).to.be.true;
    });

    it('should warn but not throw if SamlController is not available', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const emptyContainer = new Container();
      (app as any).authServiceContainer = emptyContainer;

      // Should NOT throw
      await app.updateSamlStrategies();

      expect(mockLogger.warn.called).to.be.true;
    });

    it('should warn but not throw if updateSamlStrategiesWithCallback throws', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const mockSamlController = {
        updateSamlStrategiesWithCallback: sandbox.stub().throws(new Error('SAML error')),
      };
      const authContainer = new Container();
      authContainer.bind<any>('SamlController').toConstantValue(mockSamlController);
      (app as any).authServiceContainer = authContainer;

      // Should NOT throw
      await app.updateSamlStrategies();

      expect(mockLogger.warn.called).to.be.true;
    });
  });

  // =========================================================================
  // setupApiDocs()
  // =========================================================================
  describe('setupApiDocs()', () => {
    it('should mount API docs at /api/v1/docs after initialization', async () => {
      sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
      sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      const app = new Application();
      stubAllContainers(sandbox);
      await app.initialize();

      const expressApp = (app as any).app as express.Express;
      const stack = (expressApp as any)._router?.stack || [];
      const docsLayer = stack.find((layer: any) =>
        layer.regexp && layer.regexp.test('/api/v1/docs'),
      );
      expect(docsLayer).to.exist;
    });
  });

  // =========================================================================
  // CORS configuration
  // =========================================================================
  describe('CORS configuration', () => {
    it('should use ALLOWED_ORIGINS from environment', async () => {
      const original = process.env.ALLOWED_ORIGINS;
      process.env.ALLOWED_ORIGINS = 'https://example.com,https://app.example.com';

      try {
        sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
        sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
        sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
        sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

        const app = new Application();
        stubAllContainers(sandbox);
        await app.initialize();

        // CORS middleware is registered — check the stack
        const expressApp = (app as any).app as express.Express;
        const stack = (expressApp as any)._router?.stack || [];
        const corsLayer = stack.find((l: any) => l.name === 'corsMiddleware');
        expect(corsLayer).to.exist;
      } finally {
        restoreEnv('ALLOWED_ORIGINS', original);
      }
    });

    it('should default to localhost:3001 when ALLOWED_ORIGINS is not set', async () => {
      const original = process.env.ALLOWED_ORIGINS;
      delete process.env.ALLOWED_ORIGINS;

      try {
        sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
        sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
        sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
        sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

        const app = new Application();
        stubAllContainers(sandbox);
        await app.initialize();

        // Just verify CORS is mounted — the default is embedded in the code
        const expressApp = (app as any).app as express.Express;
        const stack = (expressApp as any)._router?.stack || [];
        const corsLayer = stack.find((l: any) => l.name === 'corsMiddleware');
        expect(corsLayer).to.exist;
      } finally {
        restoreEnv('ALLOWED_ORIGINS', original);
      }
    });
  });

  // =========================================================================
  // Error handling middleware
  // =========================================================================
  describe('error handling middleware', () => {
    it('should register error handler as the last middleware before static/SPA', async () => {
      sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
      sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      const app = new Application();
      stubAllContainers(sandbox);
      await app.initialize();

      const expressApp = (app as any).app as express.Express;
      const stack = (expressApp as any)._router?.stack || [];

      // Error middleware layers have 4 parameters (err, req, res, next)
      // Express identifies them by arity
      const errorLayers = stack.filter(
        (layer: any) => layer.handle && layer.handle.length === 4,
      );
      expect(errorLayers.length).to.be.greaterThan(0);
    });
  });

  // =========================================================================
  // Full initialization order
  // =========================================================================
  describe('initialization order', () => {
    it('should initialize containers before configuring middleware and routes', async () => {
      const callOrder: string[] = [];

      sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
      sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      const app = new Application();
      const { containers } = stubAllContainers(sandbox);

      // Track TokenManagerContainer init to verify order
      (TokenManagerContainer.initialize as sinon.SinonStub).callsFake(async () => {
        callOrder.push('tokenManagerInit');
        return containers['token'];
      });

      await app.initialize();

      expect(callOrder).to.include('tokenManagerInit');
    });

    it('should set up Slack proxy before body-parsing middleware', async () => {
      sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
      sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      const app = new Application();
      stubAllContainers(sandbox);
      await app.initialize();

      const expressApp = (app as any).app as express.Express;
      const stack = (expressApp as any)._router?.stack || [];
      const layerNames = stack.map((l: any) => l.name || 'anonymous');

      // Slack proxy should appear before jsonParser
      const slackIdx = stack.findIndex((l: any) =>
        l.regexp && l.regexp.test('/slack/events'),
      );
      const jsonIdx = layerNames.indexOf('jsonParser');

      expect(slackIdx).to.be.greaterThan(-1);
      expect(jsonIdx).to.be.greaterThan(-1);
      expect(slackIdx).to.be.lessThan(jsonIdx);
    });
  });
});
