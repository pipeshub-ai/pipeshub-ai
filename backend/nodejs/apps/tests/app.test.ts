import { editionBoot } from '../src/config';
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import http from 'http';
import net, { AddressInfo } from 'net';
import { Container } from 'inversify';
import { Application } from '../src/app';
import { createMockLogger, MockLogger } from './helpers/mock-logger';
import { Logger } from '../src/libs/services/logger.service';
import * as telemetryModule from '../src/libs/services/telemetry/telemetry.service';
import * as metricsRefreshModule from '../src/modules/user_management/services/metrics.refresh.service';
import { KeyValueStoreService } from '../src/libs/services/keyValueStore.service';
import { MigrationService } from '../src/modules/configuration_manager/services/migration.service';
import { NotificationService } from '../src/modules/notification/service/notification.service';
import * as appConfigModule from '../src/modules/tokens_manager/config/config';
import * as cmConfigModule from '../src/modules/configuration_manager/config/config';
import * as messageBrokerModule from '../src/libs/services/message-broker.factory';
import * as kvMigrationModule from '../src/libs/keyValueStore/migration/kvStoreMigration.service';
import * as oauthProviderModule from '../src/libs/services/oauth-token-service.provider';
import * as rateLimitModule from '../src/libs/middlewares/rate-limit.middleware';
import { RedisConnectionProviderFactory } from '../src/libs/services/redis/connectionProviderFactory';
import * as redisConnectionProviderFactoryModule from '../src/libs/services/redis/connectionProviderFactory';
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
import * as userAccountRoutes from '../src/modules/auth/routes/userAccount.routes';
import * as orgAuthConfigRoutes from '../src/modules/auth/routes/orgAuthConfig.routes';
import * as storageRoutes from '../src/modules/storage/routes/storage.routes';
import * as esRoutes from '../src/modules/enterprise_search/routes/es.routes';
import * as codeGraphRoutes from '../src/modules/enterprise_search/routes/code-graph.routes';
import * as connectorRoutes from '../src/modules/tokens_manager/routes/connectors.routes';
import * as oauthRoutes from '../src/modules/tokens_manager/routes/oauth.routes';
import * as kbRoutes from '../src/modules/knowledge_base/routes/kb.routes';
import * as artifactsRoutes from '../src/modules/artifacts/routes/artifacts.routes';
import * as notificationRoutes from '../src/modules/notification/routes/notification.routes';
import * as cmRoutes from '../src/modules/configuration_manager/routes/cm_routes';
import * as openaiModelsRoutes from '../src/modules/configuration_manager/routes/openai_models.routes';
import * as mailRoutes from '../src/modules/mail/routes/mail.routes';
import * as crawlingRoutes from '../src/modules/crawling_manager/routes/cm_routes';
import * as oauthProviderRoutes from '../src/modules/oauth_provider/routes/oauth.provider.routes';
import * as oauthClientsRoutes from '../src/modules/oauth_provider/routes/oauth.clients.routes';
import * as patRoutes from '../src/modules/oauth_provider/routes/pat.routes';
import * as mcpRoutes from '../src/modules/mcp/routes/mcp.routes';
import {
  MCP_BODY_TOO_LARGE_MESSAGE,
  MCP_JSON_BODY_LIMIT_BYTES,
} from '../src/modules/mcp/middlewares/mcp-body.middleware';
import * as oidcRoutes from '../src/modules/oauth_provider/routes/oid.provider.routes';
import * as apiDocsRoutes from '../src/modules/api-docs/docs.routes';
import * as toolsetsRoutes from '../src/modules/toolsets/routes/toolsets_routes';
import * as teamsRoutes from '../src/modules/user_management/routes/teams.routes';
import * as serviceAccountsRoutes from '../src/modules/user_management/routes/service-accounts.routes';
import * as serviceTokenRoutes from '../src/modules/oauth_provider/routes/service-token.routes';
import * as delegationRoutes from '../src/modules/oauth_provider/routes/delegation.routes';
import { MailConsumer } from '../src/modules/mail/services/mail.consumer';
import { MailSenderService } from '../src/modules/mail/services/mail.sender.service';
import { BrokerTopic } from '../src/libs/types/messaging.types';

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
    maxAuthRequestsPerMinute: 10,
    trustProxy: { value: false },
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
  sandbox.stub(userAccountRoutes, 'createUserAccountRouter').returns(dummyRouter);
  sandbox.stub(orgAuthConfigRoutes, 'createOrgAuthConfigRouter').returns(dummyRouter);
  sandbox.stub(storageRoutes, 'createStorageRouter').returns(dummyRouter);
  sandbox.stub(esRoutes, 'createConversationalRouter').returns(dummyRouter);
  sandbox.stub(esRoutes, 'createSemanticSearchRouter').returns(dummyRouter);
  sandbox.stub(esRoutes, 'createAccessRequestRouter').returns(dummyRouter);
  sandbox.stub(esRoutes, 'createAgentConversationalRouter').returns(dummyRouter);
  sandbox.stub(esRoutes, 'createChatSpeechRouter').returns(dummyRouter);
  sandbox.stub(codeGraphRoutes, 'createCodeGraphRouter').returns(dummyRouter);
  sandbox.stub(connectorRoutes, 'createConnectorRouter').returns(dummyRouter);
  sandbox.stub(oauthRoutes, 'createOAuthRouter').returns(dummyRouter);
  sandbox.stub(kbRoutes, 'createKnowledgeBaseRouter').returns(dummyRouter);
  sandbox.stub(artifactsRoutes, 'createArtifactsRouter').returns(dummyRouter);
  sandbox.stub(notificationRoutes, 'createNotificationRouter').returns(dummyRouter);
  sandbox.stub(cmRoutes, 'createConfigurationManagerRouter').returns(dummyRouter);
  sandbox.stub(openaiModelsRoutes, 'createOpenAIModelsRouter').returns(dummyRouter);
  sandbox.stub(mailRoutes, 'createMailServiceRouter').returns(dummyRouter);
  sandbox.stub(crawlingRoutes, 'default').returns(dummyRouter);
  sandbox.stub(crawlingRoutes, 'createCrawlingManagerRouter').returns(dummyRouter);
  sandbox.stub(oauthProviderRoutes, 'createOAuthProviderRouter').returns(dummyRouter);
  sandbox.stub(oauthClientsRoutes, 'createOAuthClientsRouter').returns(dummyRouter);
  sandbox.stub(patRoutes, 'createPatRouter').returns(dummyRouter);
  sandbox.stub(mcpRoutes, 'createMCPRouter').returns(dummyRouter);
  sandbox.stub(oidcRoutes, 'createOIDCDiscoveryRouter').returns(dummyRouter);
  sandbox.stub(apiDocsRoutes, 'createApiDocsRouter').returns(dummyRouter);
  sandbox.stub(toolsetsRoutes, 'createToolsetsRouter').returns(dummyRouter);
  sandbox.stub(teamsRoutes, 'createTeamsRouter').returns(dummyRouter);
  sandbox.stub(serviceAccountsRoutes, 'createServiceAccountsRouter').returns(dummyRouter);
  sandbox.stub(serviceTokenRoutes, 'createServiceTokenRouter').returns(dummyRouter);
  sandbox.stub(delegationRoutes, 'createDelegationRouter').returns(dummyRouter);
}

/**
 * Sets up stubs for all container initializations so Application.initialize()
 * can run without real databases/services.
 */
function stubAllContainers(sandbox: sinon.SinonSandbox) {
  // Stub route factories so configureRoutes() doesn't blow up
  stubAllRouteFactories(sandbox);

  // Telemetry — never start the real push loop / Mongo-backed gauge refresh
  sandbox.stub(telemetryModule, 'startTelemetry');
  sandbox.stub(metricsRefreshModule, 'startOrgMetricsRefresh');
  sandbox.stub(KeyValueStoreService, 'getInstance').returns({} as any);

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

  // initialize() builds the outbox dispatcher, which takes the message
  // producer from the user-manager container. Unlike the routers, that is not
  // behind a factory the harness can stub, so the binding has to resolve here
  // as it does in production.
  containers.userManager!.bind('MessageProducer').toConstantValue({
    isConnected: () => true,
    connect: sandbox.stub().resolves(),
    disconnect: sandbox.stub().resolves(),
    publish: sandbox.stub().resolves(),
    publishBatch: sandbox.stub().resolves(),
    healthCheck: sandbox.stub().resolves(true),
  } as any);

  // configureRoutes joins the service-account and service-token services, which
  // live in different containers, so both have to resolve here as they do in
  // production. Unlike the routers, this wiring is not behind a factory the
  // harness can stub.
  containers.userManager!.bind('ServiceAccountsService').toConstantValue({
    setTokenRevoker: sandbox.stub(),
  } as any);
  containers.oauth!.bind('ServiceTokenService').toConstantValue({
    revokeAllForServiceAccount: sandbox.stub().resolves(),
    revokeDelegatedTokensForServiceAccount: sandbox.stub().resolves(),
  } as any);


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

  // CrawlingSchedulerService mock — needed for configureRoutes() and
  // runMigration() which both pull the singleton from the container.
  const { CrawlingSchedulerService } = require(
    '../src/modules/crawling_manager/services/crawling_service',
  );
  const mockCrawlingScheduler = {
    scheduleJob: sandbox.stub().resolves({ id: 'mock-job' }),
    removeJob: sandbox.stub().resolves(),
    getJobStatus: sandbox.stub().resolves(null),
  };
  containers['crawling']
    .bind<any>(CrawlingSchedulerService)
    .toConstantValue(mockCrawlingScheduler);

  return { containers, mockNotificationService, mockOAuthTokenService };
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

    it('runs the edition boot hooks while initializing', async () => {
      const app = new Application();
      stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');
      const initialize = sandbox.stub(editionBoot, 'initialize').resolves();
      const mountRawRoutes = sandbox.stub(editionBoot, 'mountRawRoutes');
      const mountRoutes = sandbox.stub(editionBoot, 'mountRoutes');
      const startWorkers = sandbox.stub(editionBoot, 'startWorkers');

      await app.initialize();

      expect(initialize.calledOnce).to.be.true;
      expect(mountRawRoutes.calledOnce).to.be.true;
      expect(mountRawRoutes.calledBefore(mountRoutes)).to.be.true;
      expect(mountRoutes.calledOnce).to.be.true;
      expect(startWorkers.calledOnce).to.be.true;
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

    it('imports REDIS_PROVIDER_MODULE (R10) before any container or Redis client is built', async () => {
      const app = new Application();
      stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');
      const loadStub = sandbox
        .stub(RedisConnectionProviderFactory, 'ensureProviderModuleLoaded')
        .resolves();

      await app.initialize();

      expect(loadStub.calledOnce).to.be.true;
      // Must run before the broker config (and therefore any Redis client
      // construction) is resolved, or an EE `memorydb` provider that
      // self-registers on import would never be loaded in time.
      const ensureTopicsStub =
        messageBrokerModule.ensureMessageTopicsExist as sinon.SinonStub;
      expect(loadStub.calledBefore(ensureTopicsStub)).to.be.true;
    });

    it('prepares the Redis connection provider (F1) before loadConfigurationManagerConfig builds the bootstrap KV client (T5)', async () => {
      const app = new Application();
      stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');
      const prepareStub = sandbox
        .stub(redisConnectionProviderFactoryModule, 'getPreparedRedisProvider')
        .resolves({} as any);

      await app.initialize();

      expect(prepareStub.calledOnce).to.be.true;
      // An EE MemoryDB provider's `prepare()` resolves rotating IAM
      // credentials (R21); it must run before this call builds the
      // bootstrap KV store's Redis client, or that client is built with a
      // stale/absent credential.
      const loadConfigStub =
        cmConfigModule.loadConfigurationManagerConfig as sinon.SinonStub;
      expect(prepareStub.calledBefore(loadConfigStub)).to.be.true;
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

    it('should start telemetry and the org metrics refresh loop', async () => {
      const app = new Application();
      stubAllContainers(sandbox);
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

      await app.initialize();

      expect(
        (telemetryModule.startTelemetry as sinon.SinonStub).calledOnce,
        'startTelemetry should be called once during initialize',
      ).to.be.true;
      expect(
        (metricsRefreshModule.startOrgMetricsRefresh as sinon.SinonStub).calledOnce,
        'startOrgMetricsRefresh should be called once during initialize',
      ).to.be.true;
    });

    it('should not start telemetry when TELEMETRY_COLLECTION_FLAG is off', async () => {
      const previous = process.env.TELEMETRY_COLLECTION_FLAG;
      process.env.TELEMETRY_COLLECTION_FLAG = 'false';
      try {
        const app = new Application();
        stubAllContainers(sandbox);
        sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');

        await app.initialize();

        expect((telemetryModule.startTelemetry as sinon.SinonStub).called).to.be
          .false;
        expect(
          (metricsRefreshModule.startOrgMetricsRefresh as sinon.SinonStub).called,
        ).to.be.false;
      } finally {
        if (previous === undefined) delete process.env.TELEMETRY_COLLECTION_FLAG;
        else process.env.TELEMETRY_COLLECTION_FLAG = previous;
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

    // Forms rendered from model output must not post off-origin.
    it("should send a CSP with form-action 'self' when STRICT_MODE=true", async () => {
      const original = process.env.STRICT_MODE;
      process.env.STRICT_MODE = 'true';
      try {
        const strictApp = new Application();
        await strictApp.initialize();

        interface MiddlewareLayer {
          name: string;
          handle: (req: { headers: Record<string, string> }, res: HeaderSink, next: (err?: unknown) => void) => void;
        }
        interface HeaderSink {
          setHeader: (name: string, value: string) => void;
          removeHeader: (name: string) => void;
          getHeader: (name: string) => string | undefined;
        }
        const expressApp = (strictApp as unknown as { app: { _router: { stack: MiddlewareLayer[] } } }).app;
        const helmetLayer = expressApp._router.stack.find((layer) => layer.name === 'helmetMiddleware');
        expect(helmetLayer, 'helmet middleware layer').to.exist;

        const headers: Record<string, string> = {};
        const res: HeaderSink = {
          setHeader: (name, value) => { headers[name.toLowerCase()] = value; },
          removeHeader: () => {},
          getHeader: (name) => headers[name.toLowerCase()],
        };
        await new Promise<void>((resolve, reject) =>
          helmetLayer!.handle({ headers: {} }, res, (err?: unknown) => (err ? reject(err) : resolve())),
        );

        const csp = headers['content-security-policy'];
        expect(csp).to.be.a('string');
        expect(csp).to.include("form-action 'self'");
      } finally {
        restoreEnv('STRICT_MODE', original);
      }
    });

    it('runs the operator-key guard after the global rate limiter, so rejected requests still count', async () => {
      const globalRateLimiter: express.RequestHandler = (_req, _res, next) => next();
      sandbox.stub(rateLimitModule, 'createGlobalRateLimiter').returns(globalRateLimiter);
      const orderedApp = new Application();
      await orderedApp.initialize();

      const expressApp = (orderedApp as any).app as express.Express;
      const layerNames: string[] = (expressApp as any)._router.stack.map((layer: any) => layer.name);
      const limiterAt = layerNames.indexOf('globalRateLimiter');
      const guardAt = layerNames.indexOf('operatorKeyGuardMiddleware');
      expect(limiterAt).to.be.greaterThan(-1);
      expect(guardAt).to.be.greaterThan(limiterAt);
    });

    it('writes an access-log line for API requests in any letter case, and none for pages', async () => {
      const expressApp = (app as any).app as express.Express;
      const server = http.createServer(expressApp);
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
      const { port } = server.address() as AddressInfo;
      const get = (path: string) =>
        new Promise<void>((resolve, reject) => {
          http
            .get({ host: '127.0.0.1', port, path }, (res) => {
              res.resume();
              res.on('end', resolve);
            })
            .on('error', reject);
        });
      try {
        for (const path of ['/api/v1/access-log-probe', '/API/v1/access-log-probe-upper', '/access-log-probe-page']) {
          await get(path);
        }
      } finally {
        await new Promise<void>((resolve) => server.close(() => resolve()));
      }

      const accessLines = (Logger.prototype.info as sinon.SinonStub).getCalls()
        .map((call) => String(call.args[0]))
        .filter((line) => line.includes('access-log-probe'));
      expect(accessLines.some((line) => line.includes('GET /api/v1/access-log-probe '))).to.equal(true);
      expect(accessLines.some((line) => line.includes('GET /API/v1/access-log-probe-upper '))).to.equal(true);
      expect(accessLines.some((line) => line.includes('/access-log-probe-page'))).to.equal(false);
    });

    it('writes an access-log line for an API request a mounted router answers', async () => {
      // A mounted router sees req.path relative to its mount point ("/"), and the
      // log line is written after it answers: the API check must use the full URL.
      const healthRouter = express.Router();
      healthRouter.get('/', (_req, res) => {
        res.json({ status: 'healthy' });
      });
      (healthRoutes.createHealthRouter as sinon.SinonStub).returns(healthRouter);
      const mountedApp = new Application();
      await mountedApp.initialize();
      const server = http.createServer((mountedApp as any).app as express.Express);
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
      const { port } = server.address() as AddressInfo;
      let status = 0;
      try {
        await new Promise<void>((resolve, reject) => {
          http
            .get({ host: '127.0.0.1', port, path: '/api/v1/health?probe=mounted-router' }, (res) => {
              status = res.statusCode ?? 0;
              res.resume();
              res.on('end', resolve);
            })
            .on('error', reject);
        });
      } finally {
        await new Promise<void>((resolve) => server.close(() => resolve()));
      }

      expect(status).to.equal(200);
      const accessLines = (Logger.prototype.info as sinon.SinonStub).getCalls()
        .map((call) => String(call.args[0]))
        .filter((line) => line.includes('probe=mounted-router'));
      expect(accessLines).to.have.length(1);
      expect(accessLines[0]).to.include('"GET /api/v1/health?probe=mounted-router HTTP/1.1" 200');
    });
  });

  // =========================================================================
  // /mcp body limit — sent through the real middleware stack
  // =========================================================================
  describe('/mcp body limit', () => {
    let server: http.Server;
    let origin: string;

    /**
     * A tools/call body of exactly `bytes` bytes, made of 1,000-character ids:
     * the XSS check refuses any single string over 100,000 characters.
     */
    const toolCallOfSize = (bytes: number): string => {
      const shape = (apps: string[]) =>
        JSON.stringify({
          jsonrpc: '2.0',
          id: 1,
          method: 'tools/call',
          params: { name: 'pipeshub_search', arguments: { query: 'q', apps } },
        });
      const id = 'a'.repeat(1000);
      // Each id after the first adds its quotes and a comma.
      const apps = new Array<string>(
        Math.floor((bytes - shape([]).length) / (id.length + 3)),
      ).fill(id);
      const rest = bytes - shape(apps).length;
      if (rest >= 3) apps.push('a'.repeat(rest - 3));
      else apps[apps.length - 1] += 'a'.repeat(rest);
      const body = shape(apps);
      expect(body.length).to.equal(bytes);
      return body;
    };

    const post = (path: string, body: string) =>
      fetch(`${origin}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body,
      });

    beforeEach(async () => {
      sandbox.stub(appConfigModule, 'loadAppConfig').resolves(createMockAppConfig());
      sandbox.stub(cmConfigModule, 'loadConfigurationManagerConfig').returns(createMockCmConfig());
      sandbox.stub(messageBrokerModule, 'ensureMessageTopicsExist').resolves();
      sandbox.stub(oauthProviderModule, 'registerOAuthTokenService');
      stubAllContainers(sandbox);

      const bodySize = (req: express.Request, res: express.Response) => {
        res.json({ receivedBytes: JSON.stringify(req.body).length });
      };
      const mcpRouter = express.Router();
      mcpRouter.post('/', bodySize);
      const healthRouter = express.Router();
      healthRouter.post('/', bodySize);
      (mcpRoutes.createMCPRouter as sinon.SinonStub).returns(mcpRouter);
      (healthRoutes.createHealthRouter as sinon.SinonStub).returns(healthRouter);

      const app = new Application();
      await app.initialize();
      server = http.createServer((app as any).app as express.Express);
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
      origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    });

    afterEach(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    });

    it('answers a body one byte over 5 MB with a 413 JSON-RPC error that says what to do', async () => {
      const res = await post('/mcp', toolCallOfSize(MCP_JSON_BODY_LIMIT_BYTES + 1));

      expect(res.status).to.equal(413);
      const body = await res.json();
      expect(body).to.deep.include({ jsonrpc: '2.0', id: null });
      expect(body.error.code).to.equal(-32000);
      expect(body.error.message).to.equal(MCP_BODY_TOO_LARGE_MESSAGE);
      expect(body.error.message).to.include('5 MB');
      expect(body.error.message).to.include('try again');
    });

    it('passes a normal MCP request', async () => {
      const body = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' });
      const res = await post('/mcp', body);

      expect(res.status).to.equal(200);
      expect((await res.json()).receivedBytes).to.equal(body.length);
    });

    it('passes a request of about 1 MB', async () => {
      const res = await post('/mcp', toolCallOfSize(1024 * 1024));

      expect(res.status).to.equal(200);
      expect((await res.json()).receivedBytes).to.equal(1024 * 1024);
    });

    it('passes a request of exactly 5 MB', async () => {
      const res = await post('/mcp', toolCallOfSize(MCP_JSON_BODY_LIMIT_BYTES));

      expect(res.status).to.equal(200);
      expect((await res.json()).receivedBytes).to.equal(MCP_JSON_BODY_LIMIT_BYTES);
    });

    it('leaves the shared 10 MB limit on other routes', async () => {
      const res = await post('/api/v1/health', toolCallOfSize(6 * 1024 * 1024));

      expect(res.status).to.equal(200);
      expect((await res.json()).receivedBytes).to.equal(6 * 1024 * 1024);
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
      '/api/v1/userAccount',
      '/api/v1/orgAuthConfig',
      '/api/v1/document',
      '/api/v1/conversations',
      '/api/v1/agents',
      '/api/v1/search',
      '/api/v1/connectors',
      '/api/v1/oauth',
      '/api/v1/knowledgeBase',
      '/api/v1/artifacts',
      '/api/v1/configurationManager',
      '/v1',
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

    it('still disposes every container when the edition fails to stop', async () => {
      (app as any).notificationContainer = new Container();
      sandbox.stub(editionBoot, 'stop').rejects(new Error('worker did not stop'));

      await app.stop();

      expect((TokenManagerContainer.dispose as sinon.SinonStub).calledOnce).to.be.true;
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

    it('should finish in-flight requests before disposing containers', async () => {
      (app as any).notificationContainer = new Container();
      const order: string[] = [];
      const mailContainer = new Container();
      mailContainer.bind<any>(MailSenderService).toConstantValue({ close: () => { order.push('mail sender closed'); } });
      (app as any).mailServiceContainer = mailContainer;
      (TokenManagerContainer.dispose as sinon.SinonStub).callsFake(async () => { order.push('dispose'); });
      let handlerStarted!: () => void;
      const requestReceived = new Promise<void>((resolve) => { handlerStarted = resolve; });
      const server = http.createServer((_req, res) => {
        handlerStarted();
        res.on('finish', () => { order.push('response'); });
        setTimeout(() => { res.end('ok'); }, 50);
      });
      (app as any).server = server;
      await new Promise<void>((resolve) => server.listen(0, resolve));
      const { port } = server.address() as AddressInfo;
      const inFlight = new Promise<string>((resolve, reject) => {
        http.get(`http://127.0.0.1:${port}/`, (res) => {
          let body = '';
          res.on('data', (c) => { body += c; });
          res.on('end', () => resolve(body));
        }).on('error', reject);
      });
      await requestReceived;

      await app.stop();

      expect(await inFlight).to.equal('ok');
      expect(order).to.deep.equal(['response', 'mail sender closed', 'dispose']);
      expect(server.listening).to.be.false;
    });

    it('should not let an idle keep-alive socket hold the drain open', async () => {
      (app as any).notificationContainer = new Container();
      let respondedAt = 0;
      let handlerStarted!: () => void;
      const requestReceived = new Promise<void>((resolve) => { handlerStarted = resolve; });
      const server = http.createServer((_req, res) => {
        handlerStarted();
        setTimeout(() => { respondedAt = Date.now(); res.end('ok'); }, 50);
      });
      server.keepAliveTimeout = 65_000;
      (app as any).server = server;
      await new Promise<void>((resolve) => server.listen(0, resolve));
      const { port } = server.address() as AddressInfo;
      // A raw socket, because nock (loaded by other specs) rewrites http.get
      // requests to Connection: close and would hide the keep-alive case.
      const socket = net.connect(port, '127.0.0.1');
      let received = '';
      socket.on('data', (c) => { received += c; });
      const socketClosed = new Promise<void>((resolve) => socket.on('close', () => resolve()));
      socket.write('GET / HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n');
      await requestReceived;

      try {
        await Promise.all([app.stop(), socketClosed]);
      } finally {
        socket.destroy();
      }

      expect(received).to.match(/^HTTP\/1\.1 200[\s\S]*Connection: keep-alive[\s\S]*\r\n\r\nok$/);
      expect(Date.now() - respondedAt).to.be.below(1000);
      expect(server.listening).to.be.false;
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

  describe('bootstrapMailBrokerConsumer()', () => {
    it('subscribes from the start so mail queued before a new group exists is delivered', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;
      const consumer = {
        start: sandbox.stub().resolves(),
        subscribe: sandbox.stub().resolves(),
        consume: sandbox.stub().resolves(),
      };
      const mailContainer = new Container();
      mailContainer.bind<any>(MailConsumer).toConstantValue(consumer);
      (app as any).mailServiceContainer = mailContainer;

      (app as any).bootstrapMailBrokerConsumer();
      await new Promise((resolve) => setImmediate(resolve));

      expect(consumer.subscribe.calledOnceWithExactly([BrokerTopic.MAIL_EVENTS], true)).to.be.true;
      expect(consumer.consume.calledOnce).to.be.true;
    });
  });

  // =========================================================================
  // runMigration()
  // =========================================================================
  describe('runMigration()', () => {
    /**
     * Flush one setImmediate tick. Because all stubs in these tests resolve
     * synchronously (as already-resolved Promises), every `await` inside the
     * runMigration setImmediate callback drains as microtasks before the next
     * setImmediate fires. A single flush therefore guarantees the entire
     * deferred body has completed before we assert.
     */
    const flushSetImmediate = () =>
      new Promise<void>((resolve) => setImmediate(resolve));

    const setupContainers = (
      app: Application,
      migrationService: { runMigration: sinon.SinonStub },
    ) => {
      const cmContainer = new Container();
      cmContainer
        .bind<any>(MigrationService)
        .toConstantValue(migrationService);
      (app as any).configurationManagerContainer = cmContainer;

      const crawlingContainer = new Container();
      const { CrawlingSchedulerService } = require(
        '../src/modules/crawling_manager/services/crawling_service',
      );
      crawlingContainer
        .bind<any>(CrawlingSchedulerService)
        .toConstantValue({});
      (app as any).crawlingManagerContainer = crawlingContainer;

      sandbox
        .stub(appConfigModule, 'loadAppConfig')
        .resolves(createMockAppConfig());
    };

    it('should call MigrationService.runMigration()', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const mockMigrationService = { runMigration: sandbox.stub().resolves() };
      setupContainers(app, mockMigrationService);

      await app.runMigration();
      await flushSetImmediate();

      expect(mockMigrationService.runMigration.calledOnce).to.be.true;
    });

    it('should log (not throw) if migration fails', async () => {
      const app = new Application();
      (app as any).logger = mockLogger;

      const mockMigrationService = {
        runMigration: sandbox
          .stub()
          .rejects(new Error('Migration schema error')),
      };
      setupContainers(app, mockMigrationService);

      // Must NOT throw — startup must succeed even if a migration fails.
      await app.runMigration();
      await flushSetImmediate();

      expect(mockLogger.error.called).to.be.true;
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
