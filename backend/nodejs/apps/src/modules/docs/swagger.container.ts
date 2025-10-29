// services/nodejs/apps/src/libs/swagger/swagger.container.ts
import { Container, injectable, inject } from 'inversify';
import { Application, Request, Response } from 'express';
import * as swaggerUi from 'swagger-ui-express';
import * as YAML from 'yamljs';
import { Logger } from '../../libs/services/logger.service';

/**
 * Configuration interface for Swagger documentation
 */
export interface SwaggerConfig {
  /**
   * Base URL where the Swagger documentation will be served
   * @default '/api-docs'
   */
  basePath?: string;

  /**
   * Title for the API documentation
   */
  title: string;

  /**
   * API version
   */
  version: string;

  /**
   * Short description of the API
   */
  description: string;

  /**
   * Contact information
   */
  contact?: {
    name?: string;
    email?: string;
    url?: string;
  };
}

/**
 * Interface for module swagger info
 */
export interface ModuleSwaggerInfo {
  /**
   * Unique identifier for the module
   */
  moduleId: string;

  /**
   * Tag name for grouping endpoints
   */
  tagName: string;

  /**
   * Tag description
   */
  tagDescription: string;

  /**
   * Path to the module's Swagger YAML file
   */
  yamlFilePath: string;

  /**
   * Base URL for the module
   */
  baseUrl: string;
}

/**
 * SwaggerService provides functionality to register module documentation
 * and set up Swagger UI for the application
 */
@injectable()
export class SwaggerService {
  private readonly modules: Map<string, ModuleSwaggerInfo> = new Map();
  private app: Application | null = null;
  private config: SwaggerConfig | null = null;
  private basePath: string = '/api-docs';
  private initialized: boolean = false;
  private combinedSwaggerDoc: any = null;

  constructor(@inject('Logger') private logger: Logger) {
    this.logger = logger || Logger.getInstance({ service: 'SwaggerService' });
  }

  /**
   * Initialize the Swagger service with the Express application and configuration
   * @param app Express application
   * @param config Swagger configuration
   */
  initialize(app: Application, config: SwaggerConfig): void {
    if (this.initialized) {
      this.logger.warn('SwaggerService already initialized');
      return;
    }

    this.app = app;
    this.config = config;
    this.basePath = config.basePath || '/api-docs';

    // Create base swagger document
    this.combinedSwaggerDoc = {
      openapi: '3.0.0',
      info: {
        title: this.config?.title,
        version: this.config?.version,
        description: this.config?.description,
        contact: this.config?.contact || {},
      },
      security: [{ bearerAuth: [] }],
      tags: [],
      paths: {},
      servers: [],
      components: {
        securitySchemes: {
          bearerAuth: {
            in: 'header',
            name: 'Authorization',
            description:
              'Enter your bearer token in the format "Bearer {token}"',
            type: 'apiKey',
          },
        },
        schemas: {},
      },
    };

    this.initialized = true;
    this.logger.info('SwaggerService initialized');
  }

  /**
   * Register a module's Swagger documentation
   * @param moduleInfo Module information including YAML file path
   */
  registerModule(moduleInfo: ModuleSwaggerInfo): void {
    if (!this.initialized) {
      this.logger.error(
        'Cannot register module before SwaggerService is initialized',
      );
      throw new Error('SwaggerService not initialized');
    }

    if (this.modules.has(moduleInfo.moduleId)) {
      this.logger.warn(
        `Module ${moduleInfo.moduleId} already registered for Swagger`,
      );
      return;
    }

    try {
      // Load the module's YAML file
      const moduleSwagger = YAML.load(moduleInfo.yamlFilePath);

      // Add module tag
      const serverExists = this.combinedSwaggerDoc.servers.some(
        (server: any) => server.url === moduleInfo.baseUrl,
      );

      if (!serverExists) {
        this.combinedSwaggerDoc.servers.push({
          url: moduleInfo.baseUrl,
          description: `${moduleInfo.tagName} API`,
        });
      }

      // Merge paths and schemas
      this.mergeSwaggerPaths(moduleSwagger.paths);
      this.mergeSwaggerComponents(moduleSwagger.components);

      // Store module info
      this.modules.set(moduleInfo.moduleId, moduleInfo);

      this.logger.info(
        `Registered Swagger documentation for module: ${moduleInfo.moduleId}`,
      );
    } catch (error) {
      this.logger.warn(
        `Failed to register module ${moduleInfo.moduleId} for Swagger:`,
        {
          error: error instanceof Error ? error.message : 'Unknown error',
          moduleInfo,
        },
      );
      throw error;
    }
  }

  /**
   * Set up Swagger UI routes in the Express application
   */
  setupSwaggerRoutes(): void {
    if (!this.initialized || !this.app) {
      this.logger.error('Cannot setup Swagger routes before initialization');
      throw new Error('SwaggerService not initialized');
    }

    try {
      // Set up main Swagger UI endpoint
      this.app.use(
        this.basePath,
        swaggerUi.serve,
        swaggerUi.setup(this.combinedSwaggerDoc, {
          explorer: true,
          customCss: '.swagger-ui .topbar { display: none }',
        }),
      );

      // Set up JSON endpoint for programmatic access
      this.app.get(`${this.basePath}.json`, (_req: Request, res: Response) => {
        res.setHeader('Content-Type', 'application/json');
        res.send(this.combinedSwaggerDoc);
      });

      // Set up individual module documentation endpoints
      Array.from(this.modules.values()).forEach((moduleInfo) => {
        this.app?.get(
          `${this.basePath}/${moduleInfo.moduleId}`,
          (_req: Request, res: Response) => {
            try {
              const moduleSwagger = YAML.load(moduleInfo.yamlFilePath);
              res.setHeader('Content-Type', 'application/json');
              res.send(moduleSwagger);
            } catch (error) {
              this.logger.error(
                `Error serving module swagger for ${moduleInfo.moduleId}:`,
                {
                  error:
                    error instanceof Error ? error.message : 'Unknown error',
                },
              );
              res
                .status(500)
                .send({ error: 'Failed to load module documentation' });
            }
          },
        );
      });

      this.logger.info(`Swagger UI set up at ${this.basePath}`);
    } catch (error) {
      this.logger.error('Failed to set up Swagger routes:', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  /**
   * Private method to merge paths from a module's Swagger into the combined document
   */
  private mergeSwaggerPaths(paths: any): void {
    if (!paths) return;

    Object.keys(paths).forEach((path) => {
      if (!this.combinedSwaggerDoc.paths[path]) {
        // Path doesn't exist in combined doc, add it
        this.combinedSwaggerDoc.paths[path] = paths[path];
      } else {
        // Path exists, merge HTTP methods
        Object.keys(paths[path]).forEach((method) => {
          if (this.combinedSwaggerDoc.paths[path][method]) {
            this.logger.warn(
              `Path ${path} method ${method} already defined in combined Swagger`,
            );
          }
          // Add security requirement to each operation if not already present
          if (paths[path][method] && !paths[path][method].security) {
            paths[path][method].security = [{ bearerAuth: [] }];
          }
          this.combinedSwaggerDoc.paths[path][method] = paths[path][method];
        });
      }
    });
  }

  /**
   * Private method to merge components from a module's Swagger into the combined document
   */
  private mergeSwaggerComponents(components: any): void {
    if (!components) return;

    // Merge schemas
    if (components.schemas) {
      Object.keys(components.schemas).forEach((schema) => {
        if (this.combinedSwaggerDoc.components.schemas[schema]) {
          this.logger.warn(
            `Schema ${schema} already defined in combined Swagger`,
          );
        }
        this.combinedSwaggerDoc.components.schemas[schema] =
          components.schemas[schema];
      });
    }

    // Merge other component types as needed (parameters, responses, etc.)
    [
      'parameters',
      'responses',
      'examples',
      'requestBodies',
      'headers',
      'securitySchemes',
      'links',
      'callbacks',
    ].forEach((componentType) => {
      if (components[componentType]) {
        if (!this.combinedSwaggerDoc.components[componentType]) {
          this.combinedSwaggerDoc.components[componentType] = {};
        }

        Object.keys(components[componentType]).forEach((component) => {
          if (this.combinedSwaggerDoc.components[componentType][component]) {
            this.logger.warn(
              `Component ${componentType}.${component} already defined in combined Swagger`,
            );
          }
          this.combinedSwaggerDoc.components[componentType][component] =
            components[componentType][component];
        });
      }
    });
  }

  /**
   * Get the combined Swagger document
   */
  getSwaggerDocument(): any {
    return this.combinedSwaggerDoc;
  }
}

/**
 * Factory function to create and configure the SwaggerService
 */
export function createSwaggerContainer(): Container {
  const container = new Container();

  // Create a default logger if none is provided
  if (!container.isBound('Logger')) {
    container
      .bind<Logger>('Logger')
      .toConstantValue(Logger.getInstance({ service: 'SwaggerService' }));
  }

  container.bind<SwaggerService>(SwaggerService).toSelf().inSingletonScope();

  return container;
}
