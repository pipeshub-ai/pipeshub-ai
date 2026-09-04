import express, { Express, Response } from 'express';
import path from 'path';
import helmet from 'helmet';
import cors from 'cors';
import morgan from 'morgan';
import { IncomingMessage } from 'http';
import { redactSensitiveQueryParams } from './libs/utils/log-redaction.utils';
import http from 'http';
import { HttpMethod } from './libs/enums/http-methods.enum';
import { Container } from 'inversify';
import { Logger } from './libs/services/logger.service';
import { createHealthRouter } from './modules/tokens_manager/routes/health.routes';
import { ErrorMiddleware } from './libs/middlewares/error.middleware';
import { OAuthTokenService } from './modules/oauth_provider/services/oauth_token.service';
import { registerOAuthTokenService } from './libs/services/oauth-token-service.provider';
import { requestContextMiddleware } from './libs/middlewares/request.context';
import {
  runWithRequestContext,
  newSystemRoot,
} from './libs/context/request-context';
import { metricsMiddleware } from './libs/middlewares/telemetry.middleware';
import { startOrgMetricsRefresh } from './modules/user_management/services/metrics.refresh.service';
import { xssSanitizationMiddleware } from './libs/middlewares/xss-sanitization.middleware';

import { loadConfigurationManagerConfig } from './modules/configuration_manager/config/config';
import { MigrationService } from './modules/configuration_manager/services/migration.service';
import { createOAuthRouter } from './modules/tokens_manager/routes/oauth.routes';
import { startTelemetry } from './libs/services/telemetry/telemetry.service';
import { KeyValueStoreService } from './libs/services/keyValueStore.service';
// Import shared symbols from `./config` rather than `./modules/*` directly.
import {
  createStorageRouter,
  StorageContainer,
  createConnectorRouter,
  TokenManagerContainer,
  createUserRouter,
  createUserGroupRouter,
  createOrgRouter,
  UserManagerContainer,
  AuthServiceContainer,
  createUserAccountRouter,
  createSamlRouter,
  createOrgAuthConfigRouter,
  SamlController,
  MailServiceContainer,
  createMailServiceRouter,
  EnterpriseSearchAgentContainer,
  createConversationalRouter,
  createSemanticSearchRouter,
  createAgentConversationalRouter,
  createChatSpeechRouter,
  KnowledgeBaseContainer,
  createKnowledgeBaseRouter,
  ConfigurationManagerContainer,
  createConfigurationManagerRouter,
  createWorkspaceAuthRouter,
  createFeatureFlagRouter,
  createOrgConfigRouter,
  createRequestRouter,
  OAuthAppsContainer,
  createOAuthAppsRouter,
} from './config';
import { NotificationContainer } from './modules/notification/container/notification.container';
import { NotificationConsumer } from './modules/notification/service/notification.consumer';
import { createNotificationRouter } from './modules/notification/routes/notification.routes';
import {
  loadAppConfig,
  AppConfig,
} from './modules/tokens_manager/config/config';
import { NotificationService } from './modules/notification/service/notification.service';
import { DesktopProxySocketGateway } from './modules/desktop_proxy/socket/desktop-proxy.gateway';
import { DesktopProxyContainer } from './modules/desktop_proxy/container/desktop-proxy.container';
import { createGlobalRateLimiter } from './libs/middlewares/rate-limit.middleware';
import { ApiDocsContainer } from './modules/api-docs/docs.container';
import { createApiDocsRouter } from './modules/api-docs/docs.routes';
import { CrawlingManagerContainer } from './modules/crawling_manager/container/cm_container';
import createCrawlingManagerRouter from './modules/crawling_manager/routes/cm_routes';
import { CrawlingSchedulerService } from './modules/crawling_manager/services/crawling_service';
import { checkAndMigrateIfNeeded } from './libs/keyValueStore/migration/kvStoreMigration.service';
import { StoreType } from './libs/keyValueStore/constants/KeyValueStoreType';
import { createTeamsRouter } from './modules/user_management/routes/teams.routes';
import { OAuthProviderContainer } from './modules/oauth_provider/container/oauth.provider.container';
import { createOAuthProviderRouter } from './modules/oauth_provider/routes/oauth.provider.routes';
import { createOAuthClientsRouter } from './modules/oauth_provider/routes/oauth.clients.routes';
import { createPatRouter } from './modules/oauth_provider/routes/pat.routes';
import { createOIDCDiscoveryRouter } from './modules/oauth_provider/routes/oid.provider.routes';
import {
  resolveMessageBrokerConfig,
  ensureMessageTopicsExist,
  REQUIRED_TOPICS,
} from './libs/services/message-broker.factory';
import { ToolsetsContainer } from './modules/toolsets/container/toolsets.container';
import { createToolsetsRouter } from './modules/toolsets/routes/toolsets_routes';
import { SkillsContainer } from './modules/skills/container/skills.container';
import { createSkillsRouter } from './modules/skills/routes/skills.routes';
import { McpServersContainer } from './modules/mcp_servers/container/mcp_servers.container';
import { createMcpServersRouter } from './modules/mcp_servers/routes/mcp_servers.routes';
import { createMCPRouter } from './modules/mcp/routes/mcp.routes';
import {
  RedisConnectionProviderFactory,
  closeAllRedisProviders,
} from './libs/services/redis/connectionProviderFactory';

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
  private desktopProxyContainer!: Container;
  private crawlingManagerContainer!: Container;
  private apiDocsContainer!: Container;
  private oauthProviderContainer!: Container;
  private toolsetsContainer!: Container;
  private skillsContainer!: Container;
  private oauthAppsContainer!: Container;
  private mcpServersContainer!: Container;
  private desktopProxySocketGateway: DesktopProxySocketGateway | null = null;
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

      // Import REDIS_PROVIDER_MODULE (R10) before any container -- and
      // therefore any RedisService/RedisDistributedKeyValueStore/streams
      // client -- resolves REDIS_MODE against the provider registry. An EE
      // `memorydb` module that self-registers on import is otherwise never
      // loaded, since nothing else in this process imports it.
      await RedisConnectionProviderFactory.ensureProviderModuleLoaded();

      // Loads configuration
      const configurationManagerConfig = loadConfigurationManagerConfig();
      const appConfig = await loadAppConfig();

      // Ensure message broker topics/streams exist
      try {
        this.logger.info('Ensuring message broker topics exist...');
        const brokerConfig = resolveMessageBrokerConfig(appConfig);
        await ensureMessageTopicsExist(brokerConfig, this.logger, REQUIRED_TOPICS);
        this.logger.info('Message broker topics check completed');
      } catch (brokerError: any) {
        this.logger.warn(
          `Could not verify/create message broker topics: ${brokerError.message}.`
        );
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
        await MailServiceContainer.initialize(appConfig, configurationManagerConfig);

      this.notificationContainer =
        await NotificationContainer.initialize(appConfig);

      this.crawlingManagerContainer =
        await CrawlingManagerContainer.initialize(
          configurationManagerConfig,
          appConfig,
        );
      this.desktopProxyContainer =
        await DesktopProxyContainer.initialize(appConfig, () => this.port);

      this.oauthProviderContainer = await OAuthProviderContainer.initialize(
        configurationManagerConfig,
        appConfig,
      );


      this.toolsetsContainer = await ToolsetsContainer.initialize(
        configurationManagerConfig,
      );

      this.skillsContainer = await SkillsContainer.initialize(
        configurationManagerConfig,
      );

      this.oauthAppsContainer = await OAuthAppsContainer.initialize(
        configurationManagerConfig,
      );
      this.mcpServersContainer = await McpServersContainer.initialize(
        configurationManagerConfig,
      );

      await this.addOAuthServicesToAuthMiddleware();

      this.configurationManagerContainer
        .bind<MigrationService>(MigrationService)
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

      startTelemetry(
        KeyValueStoreService.getInstance(configurationManagerConfig),
      );
      startOrgMetricsRefresh(this.logger);

      this.notificationContainer
        .get<NotificationService>(NotificationService)
        .initialize(this.server);
      this.desktopProxySocketGateway =
        this.desktopProxyContainer.get(DesktopProxySocketGateway);
      this.desktopProxySocketGateway.initialize(this.server);

      this.bootstrapNotificationBrokerConsumer();

      // Serve static frontend files\
      const publicDir = path.join(__dirname, 'public');
      const staticAssetPrefix = `${path.sep}_next${path.sep}static${path.sep}`;
      this.app.use(
        express.static(publicDir, {
          setHeaders: (res, filePath) => {
            // Everything under /_next/static is content-hashed, so a given URL's
            // bytes never change. Every other file (index.html, /_redirects) is
            // replaced in place on deploy and must be revalidated, or a client
            // keeps a shell that references the previous build's chunk hashes.
            res.setHeader(
              'Cache-Control',
              filePath.includes(staticAssetPrefix)
                ? 'public, max-age=31536000, immutable'
                : 'no-cache',
            );
          },
        }),
      );

      // The SPA shell is HTML; serving it for a build asset that express.static
      // did not find breaks the page instead of failing the one request. Browsers
      // reject it ("Refused to execute script ... MIME type ('text/html')"), and
      // CDNs that cache on URL extension rather than Content-Type store that HTML
      // body under the .js URL, so one client requesting a stale chunk poisons the
      // asset for everyone until the entry expires. Fail these closed.
      const buildAssetPath =
        /\.(?:js|mjs|css|map|json|wasm|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|otf|eot)$/i;

      const sendShell = (res: Response, ...segments: string[]): void => {
        res.sendFile(path.join(publicDir, ...segments, 'index.html'), {
          headers: { 'Cache-Control': 'no-cache' },
        });
      };

      // SPA fallback route\
      this.app.get('*', (_req, res) => {
        // The Next.js static export emits a single index.html for the
        // slug-less OAuth callback page, but IdPs redirect to per-slug URLs
        // (e.g. /toolsets/oauth/callback/Gmail) for redirect-URI parity with
        // the legacy SPA. Resolve those to the matching callback HTML before
        // falling back to the root index.html; otherwise the wildcard would
        // send the root page which hydrates as `/` and redirects the popup
        // to /chat, so OAuth never completes.
        const oauthCallbackMatch = _req.path.match(
          /^\/(toolsets|connectors)\/oauth\/callback\/[^/]+\/?$/,
        );
        if (oauthCallbackMatch && oauthCallbackMatch[1]) {
          sendShell(res, oauthCallbackMatch[1], 'oauth', 'callback');
          return;
        }

        // `/record/<recordId>` URLs (shared links, backend citation links,
        // etc.) can't be pre-rendered per id under `output: 'export'` since
        // `generateStaticParams()` would have to enumerate every record id.
        // Serve the single `/record/` HTML shell directly — the client reads
        // the id from `window.location.pathname` — so the URL stays intact
        // (no redirect, no visible `?recordId=` query param) and matches the
        // pattern used above for OAuth callback slugs.
        const recordMatch = _req.path.match(/^\/record\/[^/]+(?:\/.*)?$/);
        if (recordMatch) {
          sendShell(res, 'record');
          return;
        }

        if (_req.path.startsWith('/_next/') || buildAssetPath.test(_req.path)) {
          res.setHeader('Cache-Control', 'no-store');
          res.status(404).type('text/plain').send('Not Found');
          return;
        }

        sendShell(res);
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
        origin: process.env.ALLOWED_ORIGINS?.split(',') || ['http://localhost:3001'], // Be more specific than '*'
        credentials: true,
        exposedHeaders: ['x-session-token', 'content-disposition'],
        methods: [HttpMethod.DELETE, HttpMethod.GET, HttpMethod.OPTIONS, HttpMethod.PATCH, HttpMethod.POST, HttpMethod.PUT],
        allowedHeaders: ['Content-Type', 'Authorization', 'x-session-token', 'x-request-id', 'client-name']
      }),
    );

    // Body parsing
    this.app.use(express.json({ limit: '10mb' }));
    this.app.use(express.urlencoded({ extended: true }));
    this.app.use(xssSanitizationMiddleware);

    // Logging — only log API requests, skip static assets.
    // 'combined' logs the full request URL, and OAuth callbacks arrive as
    // ?code=<authorization code> — a credential exchangeable for tokens by
    // whoever reads the log. Same format, with those values redacted.
    morgan.token('url-redacted', (req: IncomingMessage) =>
      redactSensitiveQueryParams((req as { originalUrl?: string; url?: string }).originalUrl ?? req.url ?? ''),
    );
    // The Referer is client-supplied and can itself be an OAuth callback or a
    // presigned URL, so it carries the same credentials the request line does.
    morgan.token('referrer-redacted', (req: IncomingMessage) => {
      const referrer = req.headers.referer ?? req.headers.referrer;
      return typeof referrer === 'string'
        ? redactSensitiveQueryParams(referrer)
        : '-';
    });
    const combinedRedacted =
      ':remote-addr - :remote-user [:date[clf]] ":method :url-redacted ' +
      'HTTP/:http-version" :status :res[content-length] ":referrer-redacted" ' +
      '":user-agent"';
    this.app.use(
      morgan(combinedRedacted, {
        stream: {
          write: (message: string) => this.logger.info(message.trim()),
        },
        skip: (req) => !req.path.startsWith('/api/'),
      }),
    );

    // Global rate limiter - applies to all routes
    this.app.use(createGlobalRateLimiter(this.logger, appConfig.maxRequestsPerMinute));
    this.app.use(metricsMiddleware());
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

    this.app.use(
      '/api/v1/orgConfig',
      createOrgConfigRouter(this.entityManagerContainer),
    );

    this.app.use(
      '/api/v1/requests',
      createRequestRouter(this.entityManagerContainer),
    );

    this.app.use(
      '/api/v1/featureFlags',
      createFeatureFlagRouter(this.entityManagerContainer),
    );

    this.app.use('/api/v1/saml', createSamlRouter(this.authServiceContainer));

    this.app.use(
      '/api/v1/userAccount',
      createUserAccountRouter(this.authServiceContainer),
    );
    this.app.use(
      '/api/v1/orgAuthConfig',
      createOrgAuthConfigRouter(this.authServiceContainer),
    );

    this.app.use(
      '/api/v1',
      createWorkspaceAuthRouter(this.authServiceContainer),
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

    // chat speech (TTS/STT) proxy routes to the Python AI backend
    this.app.use(
      '/api/v1/chat',
      createChatSpeechRouter(this.esAgentContainer),
    );

    // enterprise semantic search routes
    this.app.use(
      '/api/v1/search',
      createSemanticSearchRouter(this.esAgentContainer),
    );

    // enterprise search connectors routes — pass the crawling container
    // whole so the route factory can resolve any crawling-side service it
    // needs (currently the scheduler).
    this.app.use(
      '/api/v1/connectors',
      createConnectorRouter(
        this.tokenManagerContainer,
        this.crawlingManagerContainer,
      ),
    );

    // OAuth config routes
    this.app.use(
      '/api/v1/oauth',
      createOAuthRouter(this.tokenManagerContainer),
    );

    // knowledge base routes
    this.app.use(
      '/api/v1/knowledgeBase',
      createKnowledgeBaseRouter(this.knowledgeBaseContainer),
    );

    this.app.use(
      '/api/v1/notifications',
      createNotificationRouter(this.entityManagerContainer),
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

    // skills routes (Personal Settings > Skills -> Python query service)
    this.app.use(
      '/api/v1/skills',
      createSkillsRouter(this.skillsContainer)
    );

    // oauth-apps routes — thin proxy to Python connector service
    this.app.use(
      '/api/v1/oauth-apps',
      createOAuthAppsRouter(this.oauthAppsContainer),
    );

    // MCP servers routes (registry + auth + token management -> Python connectors service)
    this.app.use(
      '/api/v1/mcp-servers',
      createMcpServersRouter(this.mcpServersContainer)
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

    this.app.use(
      '/api/v1/personal-access-tokens',
      createPatRouter(this.oauthProviderContainer),
    );

    // MCP (Model Context Protocol) routes
    this.app.use(
      '/mcp',
      createMCPRouter(this.oauthProviderContainer),
    );

    // OIDC Discovery routes - mounted at root level per RFC 8414 & RFC 9728
    // Exposes: GET /.well-known/openid-configuration
    //          GET /.well-known/oauth-authorization-server
    //          GET /.well-known/oauth-protected-resource
    //          GET /.well-known/jwks.json
    this.app.use(
      '/.well-known',
      createOIDCDiscoveryRouter(this.oauthProviderContainer),
    );
  }

  private configureErrorHandling(): void {
    this.app.use(ErrorMiddleware.handleError());
  }

  /** Starts Kafka/Redis notification consumer in the background (does not block init). */
  private bootstrapNotificationBrokerConsumer(): void {
    void (async () => {
      try {
        const consumer = this.notificationContainer.get<NotificationConsumer>(
          NotificationConsumer,
        );
        await consumer.start();
        await consumer.subscribe(['notification'], false);
        await consumer.consume(async () => {
          /* persist + WebSocket delivery implemented in NotificationConsumer */
        });
      } catch (error) {
        this.logger.error('Notification broker consumer failed to start', {
          error: error instanceof Error ? error.message : String(error),
        });
      }
    })();
  }

  async start(): Promise<void> {
    try {
      await new Promise<void>((resolve) => {
        this.server.listen(this.port, () => {
          this.logger.info(`Server started on port ${this.port}`);
          resolve();
        });
      });
      if (this.authServiceContainer) {
        await this.updateSamlStrategies();
      }

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
        const notificationConsumer =
          this.notificationContainer.get<NotificationConsumer>(
            NotificationConsumer,
          );
        await notificationConsumer.stop();
      } catch (err) {
        this.logger.warn('NotificationConsumer not available during shutdown', {
          error: err instanceof Error ? err.message : String(err),
        });
      }
      try {
        this.desktopProxySocketGateway?.shutdown();
        this.desktopProxySocketGateway = null;
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
      await DesktopProxyContainer.dispose();
      await ApiDocsContainer.dispose();
      await OAuthProviderContainer.dispose();

      // Last: the containers above still hand back Redis-backed services
      // while they dispose. Nothing else closes these -- the provider owns
      // every client it handed out (R11), and on cluster that is a socket to
      // every node.
      await closeAllRedisProviders();

      this.logger.info('Application stopped successfully');
    } catch (error) {
      this.logger.error('Error stopping application', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  async runMigration(): Promise<void> {
    // Migrations can take a while (the scheduled-jobs backfill enumerates
    // every org). Defer the body so callers (the startup IIFE) return
    // immediately and the server keeps accepting traffic. Failures here
    // are logged but never crash the process — startup must succeed even
    // if a migration cannot run this boot.
    setImmediate(async () => {
      await runWithRequestContext(
        { rootId: newSystemRoot() },
        async () => {
      try {
        this.logger.info('Running migration...');
        const scheduler =
          this.crawlingManagerContainer.get<CrawlingSchedulerService>(
            CrawlingSchedulerService,
          );
        const appConfig = await loadAppConfig();
        await this.configurationManagerContainer
          .get(MigrationService)
          .runMigration({ scheduler, appConfig });
        this.logger.info('Migration completed successfully');
      } catch (error) {
        this.logger.error('Failed to run migration', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
        },
      );
    });
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

    if (configurationManagerConfig.storeType !== StoreType.Redis || !process.env.ETCD_URL) {
      logger.debug('KV store is not Redis or etcd not available, skipping pre-init migration check');
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
  async updateSamlStrategies(): Promise<void> {
    try {
      const samlController = this.authServiceContainer.get<SamlController>('SamlController');
      await samlController.updateSamlStrategiesWithCallback();
    } catch (error) {
      this.logger.warn('Failed to update passport strategies', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }
}


