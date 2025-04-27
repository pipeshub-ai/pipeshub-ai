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
import {
  createConversationalRouter,
  createSemanticSearchRouter,
} from './modules/enterprise_search/routes/es.routes';
import { EnterpriseSearchAgentContainer } from './modules/enterprise_search/container/es.container';
import { requestContextMiddleware } from './libs/middlewares/request.context';

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
import { PrometheusService } from './libs/services/prometheus/prometheus.service';
import { StorageContainer } from './modules/storage/container/storage.container';
import { NotificationContainer } from './modules/notification/container/notification.container';
import {
  AppConfig,
  loadAppConfig,
} from './modules/tokens_manager/config/config';
import { NotificationService } from './modules/notification/service/notification.service';
import {
  createSwaggerContainer,
  SwaggerConfig,
  SwaggerService,
} from './modules/docs/swagger.container';
import { registerStorageSwagger } from './modules/storage/docs/swagger';
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

      // Configure Express
      this.configureMiddleware(appConfig);
      this.configureRoutes();
      this.setupSwagger();
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
    let connectorPublicUrl;
    if (
      !appConfig.connectorBackend ||
      appConfig.connectorBackend.startsWith('localhost')
    ) {
      connectorPublicUrl = process.env.CONNECTOR_PUBLIC_BACKEND;
    } else {
      connectorPublicUrl = appConfig.connectorBackend;
    }
    // Security middleware
    this.app.use(helmet());

    // Request context middleware
    this.app.use(requestContextMiddleware);

    // CORS
    this.app.use(
      cors({
        origin: process.env.ALLOWED_ORIGINS?.split(',') || '*',
        credentials: true,
        exposedHeaders: ['x-session-token', 'content-disposition'],
      }),
    );

    const isDev = process.env.NODE_ENV !== 'production';

    this.app.use((_req, res, next) => {
      const csp = isDev
        ? "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:;"
        : `script-src 'self' https://cdnjs.cloudflare.com ${connectorPublicUrl}; worker-src 'self' blob:;`;
      res.setHeader('Content-Security-Policy', csp);
      next();
    });

    // Body parsing
    this.app.use(express.json({ limit: '10mb' }));
    this.app.use(express.urlencoded({ extended: true }));
    this.app.use(
      morgan('combined', {
        stream: {
          write: (message: string) => this.logger.info(message.trim()),
        },
      }),
    );
    // Todo: Apply Rate Limit Middleware
  }

  private configureRoutes(): void {
    // Health check routes
    this.app.use(
      '/api/v1/health',
      createHealthRouter(
        this.tokenManagerContainer,
        this.knowledgeBaseContainer,
        this.configurationManagerContainer,
      ),
    );

    this.app.use(
      '/api/v1/users',
      createUserRouter(this.entityManagerContainer),
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

    // knowledge base routes
    this.app.use(
      '/api/v1/knowledgeBase',
      createKnowledgeBaseRouter(this.knowledgeBaseContainer),
    );

    // configuration manager routes
    this.app.use(
      '/api/v1/configurationManager',
      createConfigurationManagerRouter(this.configurationManagerContainer),
    );

    this.app.use(
      '/api/v1/mail',
      createMailServiceRouter(this.mailServiceContainer),
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
      this.notificationContainer
        .get<NotificationService>(NotificationService)
        .shutdown();
      await NotificationContainer.dispose();
      await StorageContainer.dispose();
      await UserManagerContainer.dispose();
      await AuthServiceContainer.dispose();
      await EnterpriseSearchAgentContainer.dispose();
      await TokenManagerContainer.dispose();
      await KnowledgeBaseContainer.dispose();
      await ConfigurationManagerContainer.dispose();
      await MailServiceContainer.dispose();

      this.logger.info('Application stopped successfully');
    } catch (error) {
      this.logger.error('Error stopping application', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  private setupSwagger() {
    try {
      // Create the Swagger configuration
      const swaggerConfig: SwaggerConfig = {
        title: 'PipesHub API',
        version: '1.0.0',
        description: 'RESTful API for PipesHub services',
        contact: {
          name: 'API Support',
          email: 'contact@pipeshub.com',
        },
        basePath: '/api-docs',
      };

      // Create container
      const swaggerContainer = createSwaggerContainer();

      // Get SwaggerService from container - IMPORTANT: Use the class directly, not as a string token
      const swaggerService = swaggerContainer.get(SwaggerService);

      // Initialize with app and config
      swaggerService.initialize(this.app, swaggerConfig);

      // Register module documentation
      registerStorageSwagger(swaggerService);
      // Register other modules as needed

      // Setup the Swagger UI routes
      swaggerService.setupSwaggerRoutes();

      this.logger.info('Swagger documentation initialized successfully');
    } catch (error) {
      this.logger.error('Failed to initialize Swagger documentation', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }
}
