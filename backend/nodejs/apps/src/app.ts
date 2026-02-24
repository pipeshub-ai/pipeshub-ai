import express, { Express } from 'express';
import path from 'path';
import helmet from 'helmet';
import cors from 'cors';
import morgan from 'morgan';
import http from 'http';
import { Container } from 'inversify';
import { TokenManagerContainer } from './modules/tokens_manager/container/token-manager.container';
import { Logger } from './libs/services/logger.service';
import { createHealthRouter } from './modules/tokens_manager/routes/health.routes';
import { ErrorMiddleware } from './libs/middlewares/error.middleware';
import { createUserRouter } from './modules/user_management/routes/users.routes';
import { createUserGroupRouter } from './modules/user_management/routes/userGroups.routes';
import { createOrgRouter } from './modules/user_management/routes/org.routes';
import { OAuthTokenService } from './modules/oauth_provider/services/oauth_token.service';
import { registerOAuthTokenService } from './libs/services/oauth-token-service.provider';
import {
  createConversationalRouter,
  createSemanticSearchRouter,
  createAgentConversationalRouter,
} from './modules/enterprise_search/routes/es.routes';
import { EnterpriseSearchAgentContainer } from './modules/enterprise_search/container/es.container';
import { requestContextMiddleware } from './libs/middlewares/request.context';
import { xssSanitizationMiddleware } from './libs/middlewares/xss-sanitization.middleware';

import { createUserAccountRouter } from './modules/auth/routes/userAccount.routes';
import { UserManagerContainer } from './modules/user_management/container/userManager.container';
import { AuthServiceContainer } from './modules/auth/container/authService.container';
import { createSamlRouter } from './modules/auth/routes/saml.routes';
import { createOrgAuthConfigRouter } from './modules/auth/routes/orgAuthConfig.routes';
import { KnowledgeBaseContainer } from './modules/knowledge_base/container/kb_container';
import { createKnowledgeBaseRouter } from './modules/knowledge_base/routes/kb.routes';
import { createStorageRouter } from './modules/storage/routes/storage.routes';
import { createConfigurationManagerRouter } from './modules/configuration_manager/routes/cm_routes';
import { loadConfigurationManagerConfig } from './modules/configuration_manager/config/config';
import { ConfigurationManagerContainer } from './modules/configuration_manager/container/cm_container';
import { MailServiceContainer } from './modules/mail/container/mailService.container';
import { createMailServiceRouter } from './modules/mail/routes/mail.routes';
import { createConnectorRouter } from './modules/tokens_manager/routes/connectors.routes';
import { createOAuthRouter } from './modules/tokens_manager/routes/oauth.routes';
import { PrometheusService } from './libs/services/prometheus/prometheus.service';
import { StorageContainer } from './modules/storage/container/storage.container';
import { NotificationContainer } from './modules/notification/container/notification.container';
import {
  loadAppConfig,
  AppConfig,
} from './modules/tokens_manager/config/config';
import { NotificationService } from './modules/notification/service/notification.service';
import { createGlobalRateLimiter } from './libs/middlewares/rate-limit.middleware';
import { ApiDocsContainer } from './modules/api-docs/docs.container';
import { createApiDocsRouter } from './modules/api-docs/docs.routes';
import { CrawlingManagerContainer } from './modules/crawling_manager/container/cm_container';
import createCrawlingManagerRouter from './modules/crawling_manager/routes/cm_routes';
import { MigrationService } from './modules/configuration_manager/services/migration.service';
import { checkAndMigrateIfNeeded } from './libs/keyValueStore/migration/kvStoreMigration.service';
import { StoreType } from './libs/keyValueStore/constants/KeyValueStoreType';
import { createTeamsRouter } from './modules/user_management/routes/teams.routes';
import { OAuthProviderContainer } from './modules/oauth_provider/container/oauth.provider.container';
import {
  createOAuthProviderRouter,
  createOAuthClientsRouter,
  createOIDCDiscoveryRouter,
} from './modules/oauth_provider/routes';
import { ensureKafkaTopicsExist, REQUIRED_KAFKA_TOPICS } from './libs/services/kafka-admin.service';
import { ToolsetsContainer } from './modules/toolsets/container/toolsets.container';
import { createToolsetsRouter } from './modules/toolsets/routes/toolsets_routes';

const loggerConfig = {
  service: 'Application',
};

export class Application {
  private app: Express;
  private server: http.Server;
  private tokenManagerContainer!: Container;
  private storageServiceContainer!: Container;
  private esAgentContainer!: Container;
  private logger!: Logger;
  private authServiceContainer!: Container;
  private entityManagerContainer!: Container;
  private knowledgeBaseContainer!: Container;
  private configurationManagerContainer!: Container;
  private mailServiceContainer!: Container;
  private notificationContainer!: Container;
  private crawlingManagerContainer!: Container;
  private apiDocsContainer!: Container;
  private oauthProviderContainer!: Container;
  private toolsetsContainer!: Container;
  private port: number;

  constructor() {
    this.app = express();
    this.port = parseInt(process.env.PORT || '3000', 10);
    this.server = http.createServer(this.app);
  }


  async initialize(): Promise<void> {
    try {
      // Initialize Logger
      this.logger = new Logger(loggerConfig);
      // Loads configuration
      const configurationManagerConfig = loadConfigurationManagerConfig();
      const appConfig = await loadAppConfig();

      // Ensure Kafka topics exist (important for Kafka deployments where auto-create is disabled)
      try {
        this.logger.info('Ensuring Kafka topics exist...');
        await ensureKafkaTopicsExist(appConfig.kafka, this.logger, REQUIRED_KAFKA_TOPICS);
        this.logger.info('Kafka topics check completed');
      } catch (kafkaError: any) {
        this.logger.warn(
          `Could not verify/create Kafka topics: ${kafkaError.message}.`
        );
        // Don't throw - allow app to continue; topics might already exist or be created elsewhere
      }

      this.tokenManagerContainer = await TokenManagerContainer.initialize(
        configurationManagerConfig,
      );

      this.configurationManagerContainer =
        await ConfigurationManagerContainer.initialize(
          configurationManagerConfig,
          appConfig,
        );
      // TODO: Initialize Logger separately and not in token manager

      this.storageServiceContainer = await StorageContainer.initialize(
        configurationManagerConfig,
        appConfig,
      );

      this.entityManagerContainer = await UserManagerContainer.initialize(
        configurationManagerConfig,
        appConfig,
      );
      this.authServiceContainer = await AuthServiceContainer.initialize(
        configurationManagerConfig,
        appConfig,
      );
      this.esAgentContainer = await EnterpriseSearchAgentContainer.initialize(
        configurationManagerConfig,
        appConfig,
      );
      this.knowledgeBaseContainer = await KnowledgeBaseContainer.initialize(
        configurationManagerConfig,
        appConfig,
      );

      this.mailServiceContainer =
        await MailServiceContainer.initialize(appConfig);

      this.notificationContainer =
        await NotificationContainer.initialize(appConfig);

      this.crawlingManagerContainer =
        await CrawlingManagerContainer.initialize(
          configurationManagerConfig,
          appConfig,
        );

      this.oauthProviderContainer = await OAuthProviderContainer.initialize(
        configurationManagerConfig,
        appConfig,
      );


      this.toolsetsContainer = await ToolsetsContainer.initialize(
        configurationManagerConfig,
      );

      await this.addOAuthServicesToAuthMiddleware();


      // binding prometheus to all services routes
      this.logger.debug('Binding Prometheus Service with other services');
      this.tokenManagerContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();
      this.entityManagerContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();
      this.authServiceContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();
      this.configurationManagerContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();
      this.configurationManagerContainer
        .bind<MigrationService>(MigrationService)
        .toSelf()
        .inSingletonScope();
      this.storageServiceContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();
      this.esAgentContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();

      this.knowledgeBaseContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();

      this.mailServiceContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();

      this.crawlingManagerContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();

      this.oauthProviderContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();

      this.toolsetsContainer
        .bind<PrometheusService>(PrometheusService)
        .toSelf()
        .inSingletonScope();

      // Initialize API Documentation
      this.apiDocsContainer = await ApiDocsContainer.initialize();

      // Slack events proxy must be mounted before body-parsing middleware
      // so the raw request body is forwarded intact for signature verification
      this.setupSlackEventsProxy();

      // Configure Express
      this.configureMiddleware(appConfig);
      this.configureRoutes();
      this.setupApiDocs();
      this.configureErrorHandling();

      this.notificationContainer
        .get<NotificationService>(NotificationService)
        .initialize(this.server);

      // Serve static frontend files\
      this.app.use(express.static(path.join(__dirname, 'public')));
      // SPA fallback route\
      this.app.get('*', (_req, res) => {
        res.sendFile(path.join(__dirname, 'public', 'index.html'));
      });

      this.logger.info('Application initialized successfully');
    } catch (error: any) {
      this.logger.error(
        `Failed to initialize application: ${error.message}`,
        error.stack,
      );
      throw error;
    }
  }

  private configureMiddleware(appConfig: AppConfig): void {
    const isStrictMode = process.env.STRICT_MODE === 'true';
    if (isStrictMode) {
      // Security middleware - configure helmet once with all options
      const envConnectSrcs = process.env.CSP_CONNECT_SRCS?.split(',').filter(Boolean) ?? [];
      const connectSrc = [
        ...new Set([
          "'self'",
          "https://static.cloudflareinsights.com",
          // Login with google urls
          'https://accounts.google.com',
          'https://www.googleapis.com',
          // Login with microsoft urls
          'https://login.microsoftonline.com',
          'https://graph.microsoft.com',
          ...envConnectSrcs,
          appConfig.connectorPublicUrl,
        ]),
      ].filter(Boolean);

      this.app.use(helmet({
        crossOriginOpenerPolicy: { policy: "unsafe-none" }, // Required for MSAL popup
        contentSecurityPolicy: {
          directives: {
            defaultSrc: ["'self'"],
            scriptSrc: [
              "'self'",
              ...(process.env.CSP_SCRIPT_SRCS?.split(',') ?? [
                "https://cdnjs.cloudflare.com",
                "https://login.microsoftonline.com",
                "https://graph.microsoft.com",
                "https://accounts.google.com",
                "https://challenges.cloudflare.com",
                "https://api.iconify.design",
                "https://api.simplesvg.com"
              ]),
            ],
            connectSrc: connectSrc,
            objectSrc: ["'self'", "data:", "blob:"], // PDF rendering
            frameSrc: ["'self'", "blob:"], // PDF rendering in frames
            workerSrc: ["'self'", "blob:"], // PDF.js workers
            childSrc: ["'self'", "blob:"], // PDF rendering
            imgSrc: ["'self'", "data:", "blob:", "https:"], // Images in PDFs
            fontSrc: ["'self'", "data:", "https:"], // Fonts in PDFs
            mediaSrc: ["'self'", "blob:", "data:"] // Media in PDFs
          }
        }
      }));
    }

    // Request context middleware
    this.app.use(requestContextMiddleware);

    // CORS - ensure this matches your frontend domain
    this.app.use(
      cors({
        origin: process.env.ALLOWED_ORIGINS?.split(',') || ['http://localhost:3000'], // Be more specific than '*'
        credentials: true,
        exposedHeaders: ['x-session-token', 'content-disposition'],
        methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
        allowedHeaders: ['Content-Type', 'Authorization', 'x-session-token']
      }),
    );

    // Body parsing
    this.app.use(express.json({ limit: '10mb' }));
    this.app.use(express.urlencoded({ extended: true }));
    this.app.use(xssSanitizationMiddleware);

    // Logging
    this.app.use(
      morgan('combined', {
        stream: {
          write: (message: string) => this.logger.info(message.trim()),
        },
      }),
    );

    // Global rate limiter - applies to all routes
    this.app.use(createGlobalRateLimiter(this.logger, appConfig.maxRequestsPerMinute));
  }

  private configureRoutes(): void {
    // Health check routes
    this.app.use(
      '/api/v1/health',
      createHealthRouter(
        this.tokenManagerContainer,
        this.configurationManagerContainer,
      ),
    );

    this.app.use(
      '/api/v1/users',
      createUserRouter(this.entityManagerContainer),
    );
    this.app.use(
      '/api/v1/teams',
      createTeamsRouter(this.entityManagerContainer),
    );
    this.app.use(
      '/api/v1/userGroups',
      createUserGroupRouter(this.entityManagerContainer),
    );
    this.app.use('/api/v1/org', createOrgRouter(this.entityManagerContainer));

    this.app.use('/api/v1/saml', createSamlRouter(this.authServiceContainer));

    this.app.use(
      '/api/v1/userAccount',
      createUserAccountRouter(this.authServiceContainer),
    );
    this.app.use(
      '/api/v1/orgAuthConfig',
      createOrgAuthConfigRouter(this.authServiceContainer),
    );

    // storage routes
    this.app.use(
      '/api/v1/document',
      createStorageRouter(this.storageServiceContainer),
    );

    // enterprise search conversational routes
    this.app.use(
      '/api/v1/conversations',
      createConversationalRouter(this.esAgentContainer),
    );

    // enterprise search agent routes
    this.app.use(
      '/api/v1/agents',
      createAgentConversationalRouter(this.esAgentContainer),
    );

    // enterprise semantic search routes
    this.app.use(
      '/api/v1/search',
      createSemanticSearchRouter(this.esAgentContainer),
    );

    // enterprise search connectors routes
    this.app.use(
      '/api/v1/connectors',
      createConnectorRouter(this.tokenManagerContainer),
    );

    // OAuth config routes
    this.app.use(
      '/api/v1/oauth',
      createOAuthRouter(this.tokenManagerContainer),
    );

    // knowledge base routes
    this.app.use(
      '/api/v1/knowledgeBase',
      createKnowledgeBaseRouter(this.knowledgeBaseContainer, this.notificationContainer),
    );

    // configuration manager routes
    this.app.use(
      '/api/v1/configurationManager',
      createConfigurationManagerRouter(this.configurationManagerContainer),
    );

    // toolsets routes
    this.app.use(
      '/api/v1/toolsets',
      createToolsetsRouter(this.toolsetsContainer)
    );

    this.app.use(
      '/api/v1/mail',
      createMailServiceRouter(this.mailServiceContainer),
    );

    // crawling manager routes
    this.app.use(
      '/api/v1/crawlingManager',
      createCrawlingManagerRouter(this.crawlingManagerContainer),
    );

    // pipeshub OAuth Provider routes
    this.app.use(
      '/api/v1/oauth2',
      createOAuthProviderRouter(this.oauthProviderContainer),
    );

    // OAuth Clients routes (OAuth app management)
    this.app.use(
      '/api/v1/oauth-clients',
      createOAuthClientsRouter(this.oauthProviderContainer),
    );

    // OIDC Discovery routes - mounted at root level per RFC 8414
    // Exposes: GET /.well-known/openid-configuration
    //          GET /.well-known/jwks.json
    this.app.use(
      '/.well-known',
      createOIDCDiscoveryRouter(this.oauthProviderContainer),
    );
  }

  private configureErrorHandling(): void {
    this.app.use(ErrorMiddleware.handleError());
  }

  async start(): Promise<void> {
    try {
      await new Promise<void>((resolve) => {
        this.server.listen(this.port, () => {
          this.logger.info(`Server started on port ${this.port}`);
          resolve();
        });
      });
    } catch (error) {
      this.logger.error('Failed to start server', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async stop(): Promise<void> {
    try {
      this.logger.info('Shutting down application...');
      try {
        this.notificationContainer
          .get<NotificationService>(NotificationService)
          .shutdown();
      } catch (err) {
        this.logger.warn('NotificationService not available during shutdown', 
          { error: err instanceof Error ? err.message : String(err) });
      }
      await NotificationContainer.dispose();
      await StorageContainer.dispose();
      await UserManagerContainer.dispose();
      await AuthServiceContainer.dispose();
      await EnterpriseSearchAgentContainer.dispose();
      await TokenManagerContainer.dispose();
      await KnowledgeBaseContainer.dispose();
      await ConfigurationManagerContainer.dispose();
      await MailServiceContainer.dispose();
      await CrawlingManagerContainer.dispose();
      await ApiDocsContainer.dispose();
      await OAuthProviderContainer.dispose();

      this.logger.info('Application stopped successfully');
    } catch (error) {
      this.logger.error('Error stopping application', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async runMigration(): Promise<void> {
    try {
      this.logger.info('Running migration...');
      //  migrate ai models configurations
      this.logger.info('Migrating ai models configurations');
      await this.configurationManagerContainer.get(MigrationService).runMigration();
      this.logger.info('✅ Ai models configurations migrated');

      this.logger.info('Migration completed successfully');
    } catch (error) {
      this.logger.error('Failed to run migration', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  private setupApiDocs(): void {
    try {
      // Mount the API documentation UI at /api/v1/docs
      this.app.use('/api/v1/docs', createApiDocsRouter(this.apiDocsContainer));
      this.logger.info('API documentation initialized at /api/v1/docs');
    } catch (error) {
      this.logger.error('Failed to initialize API documentation', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Run migration from etcd to Redis BEFORE loading app config.
   * This ensures secrets exist in Redis before we try to read them.
   * Must be called before initialize().
   */
  async preInitMigration(): Promise<void> {
    const logger = Logger.getInstance(loggerConfig);
    const configurationManagerConfig = loadConfigurationManagerConfig();

    if (configurationManagerConfig.storeType !== StoreType.Redis) {
      logger.debug('KV store is not Redis, skipping pre-init migration check');
      return;
    }

    logger.info('Checking KV store migration status before loading config...');
    const migrationResult = await checkAndMigrateIfNeeded({
      etcd: {
        host: configurationManagerConfig.storeConfig.host,
        port: configurationManagerConfig.storeConfig.port,
        dialTimeout: configurationManagerConfig.storeConfig.dialTimeout,
      },
      redis: {
        host: configurationManagerConfig.redisConfig.host,
        port: configurationManagerConfig.redisConfig.port,
        username: configurationManagerConfig.redisConfig.username || undefined,
        password: configurationManagerConfig.redisConfig.password,
        db: configurationManagerConfig.redisConfig.db,
        keyPrefix: configurationManagerConfig.redisConfig.keyPrefix,
        tls: configurationManagerConfig.redisConfig.tls,
      },
    });

    if (migrationResult !== null) {
      if (migrationResult.success) {
        logger.info('KV store migration completed successfully', {
          migratedKeys: migrationResult.migratedKeys.length,
        });
      } else {
        logger.error('KV store migration failed', {
          error: migrationResult.error,
          failedKeys: migrationResult.failedKeys,
        });
        throw new Error(`KV store migration failed: ${migrationResult.error}`);
      }
    } else {
      logger.info('KV store migration not needed (already completed or etcd not available)');
    }
  }

  private setupSlackEventsProxy(): void {
    const slackBotPort = parseInt(process.env.SLACK_BOT_PORT || '3020', 10);

    this.app.use('/slack/events', (req, res) => {
      const proxyReq = http.request(
        {
          hostname: 'localhost',
          port: slackBotPort,
          path: '/slack/events',
          method: req.method,
          headers: req.headers,
        },
        (proxyRes) => {
          res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
          proxyRes.pipe(res);
        },
      );

      proxyReq.on('error', (err) => {
        this.logger.error('Slack events proxy error', {
          error: err instanceof Error ? err.message : String(err),
        });
        if (!res.headersSent) {
          res.status(502).json({ error: 'Slack bot service unavailable' });
        }
      });

      req.pipe(proxyReq);
    });
  }

  async addOAuthServicesToAuthMiddleware(): Promise<void> {
    try {
      const oauthTokenService = this.oauthProviderContainer.get<OAuthTokenService>('OAuthTokenService');
      registerOAuthTokenService(oauthTokenService);
      this.logger.info('OAuth token service registered for AuthMiddleware factory');
    } catch (error) {
      this.logger.warn('Failed to register OAuth token service', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }
}

