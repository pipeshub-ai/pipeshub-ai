/**
 * API Documentation Service
 * Provides unified OpenAPI documentation from merged-openapi.yaml
 */
import { injectable, inject } from 'inversify';
import * as yaml from 'js-yaml';
import * as fs from 'fs';
import * as path from 'path';
import { Logger } from '../../libs/services/logger.service';

/**
 * Module metadata for documentation
 */
export interface ModuleInfo {
  id: string;
  name: string;
  description: string;
  version: string;
  basePath: string;
  tags: string[];
  source: 'nodejs';
  order: number;
}

/**
 * Endpoint information for documentation
 */
export interface EndpointInfo {
  path: string;
  method: string;
  summary: string;
  description: string;
  operationId: string;
  tags: string[];
  parameters: any[];
  requestBody?: any;
  responses: any;
  security?: any[];
  moduleId: string;
}

/**
 * Category grouping for sidebar
 */
export interface CategoryInfo {
  id: string;
  name: string;
  description: string;
  modules: ModuleInfo[];
}

/**
 * Unified API documentation structure
 */
export interface UnifiedApiDocs {
  info: {
    title: string;
    version: string;
    description: string;
    contact?: {
      name?: string;
      email?: string;
    };
  };
  categories: CategoryInfo[];
  modules: ModuleInfo[];
  endpoints: EndpointInfo[];
  schemas: Record<string, any>;
}

@injectable()
export class ApiDocsService {
  private mergedSpec: Record<string, any> | null = null;
  private modules: ModuleInfo[] = [];
  private logger: Logger;
  private initialized: boolean = false;

  constructor(@inject('Logger') logger: Logger) {
    this.logger = logger || Logger.getInstance({ service: 'ApiDocsService' });
  }

  /**
   * Initialize the documentation service and load the merged spec
   */
  async initialize(): Promise<void> {
    if (this.initialized) {
      return;
    }

    try {
      // Load the merged OpenAPI spec
      this.loadMergedSpec();

      // Define module metadata
      this.initializeModules();

      this.initialized = true;
      this.logger.info('ApiDocsService initialized successfully');
    } catch (error) {
      // just log and continue
      this.logger.warn('Failed to initialize ApiDocsService', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return;
    }
  }

  /**
   * Load the merged OpenAPI spec from YAML file
   */
  private loadMergedSpec(): void {
    const appRoot = path.resolve(__dirname, '..', '..', '..');
    const paths = [
      path.join(__dirname, 'pipeshub-openapi.yaml'),
      path.join(appRoot, 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'),
    ];
    const specPath = paths.find((p) => fs.existsSync(p));
    if (!specPath) {
      this.logger.warn(`PipesHub OpenAPI spec not found in any of: ${paths.join(', ')}`);
      return;
    }

    try {
      this.mergedSpec = yaml.load(fs.readFileSync(specPath, 'utf8')) as any;
      this.logger.info('Merged OpenAPI spec loaded successfully');
    } catch (error) {
      this.logger.warn('Failed to load merged OpenAPI spec', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return;
    }
  }

  /**
   * Initialize module metadata
   */
  private initializeModules(): void {
    this.modules = [
      {
        id: 'auth',
        name: 'Authentication',
        description: 'User authentication, authorization, and session management',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['User Account', 'Organization Auth Config', 'SAML', 'OAuth'],
        source: 'nodejs',
        order: 1,
      },
      {
        id: 'oauth-app-management',
        name: 'OAuth App Management',
        description: 'OAuth 2.0 authorization server, app registration, and OpenID Connect',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['OAuth Provider', 'OAuth Apps', 'OpenID Connect'],
        source: 'nodejs',
        order: 2,
      },
      {
        id: 'user-management',
        name: 'User Management',
        description: 'User, team, and organization management',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['Users', 'Teams', 'Organizations', 'User Groups'],
        source: 'nodejs',
        order: 3,
      },
      {
        id: 'knowledge-base',
        name: 'Knowledge Base',
        description: 'Knowledge base and folder management',
        version: '1.0.0',
        basePath: '/api/v1/knowledgeBase',
        tags: ['Knowledge Bases', 'Knowledge Hub', 'Folders', 'Records', 'Permissions', 'Upload', 'Connector'],
        source: 'nodejs',
        order: 5,
      },
      {
        id: 'enterprise-search',
        name: 'Enterprise Search',
        description: 'Conversational AI, semantic search, and AI agents',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['Conversations', 'Semantic Search', 'Agents', 'Agent Templates', 'Agent Conversations'],
        source: 'nodejs',
        order: 6,
      },
      {
        id: 'connector-manager',
        name: 'Connector Manager',
        description: 'Third-party integrations, OAuth flows, toolsets, and data synchronization',
        version: '1.0.0',
        basePath: '/api/v1/connectors',
        tags: ['Connector Registry', 'Connector Instances', 'Connector Configuration', 'Connector Control', 'Connector OAuth', 'Connector Filters', 'OAuth Configuration', 'Toolset Registry', 'Toolset Instances', 'Toolset Configuration', 'Toolset OAuth'],
        source: 'nodejs',
        order: 7,
      },
      {
        id: 'configuration-manager',
        name: 'Configuration Manager',
        description: 'System-wide configuration management',
        version: '1.0.0',
        basePath: '/api/v1/configurationManager',
        tags: ['Storage Configuration', 'SMTP Configuration', 'Authentication Configuration', 'Platform Settings', 'AI Models Providers', 'Public URLs', 'Metrics Collection', 'Configuration Manager'],
        source: 'nodejs',
        order: 8,
      },
      {
        id: 'crawling-manager',
        name: 'Crawling Manager',
        description: 'Data crawling job scheduling and monitoring',
        version: '1.0.0',
        basePath: '/api/v1/crawlingManager',
        tags: ['Crawling Jobs'],
        source: 'nodejs',
        order: 9,
      },
      {
        id: 'mcp',
        name: 'MCP',
        description: 'Model Context Protocol endpoints for exposing PipesHub capabilities to MCP-compatible clients',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['MCP'],
        source: 'nodejs',
        order: 10,
      },
    ];
  }

  /**
   * Get unified API documentation
   */
  getUnifiedDocs(): UnifiedApiDocs {
    return {
      info: this._buildApiInfo(),
      categories: this._buildCategories(),
      modules: this.modules.sort((a, b) => a.order - b.order),
      endpoints: this._extractEndpoints(),
      schemas: this._extractSchemas(),
    };
  }

  /**
   * Extract endpoints from merged OpenAPI spec
   */
  private _extractEndpoints(): EndpointInfo[] {
    const endpoints: EndpointInfo[] = [];

    if (!this.mergedSpec?.paths) {
      return endpoints;
    }

    for (const [pathKey, pathValue] of Object.entries(this.mergedSpec.paths)) {
      const pathObj = pathValue as any;
      for (const [method, operation] of Object.entries(pathObj)) {
        if (['get', 'post', 'put', 'patch', 'delete'].includes(method)) {
          const op = operation as any;
          const tags = op.tags || [];
          const xServiceId = op['x-service-id'] as string | undefined;
          const moduleId = this.findModuleByTags(tags, xServiceId);
          endpoints.push({
            path: pathKey,
            method: method.toUpperCase(),
            summary: op.summary || '',
            description: op.description || '',
            operationId: op.operationId || '',
            tags,
            parameters: op.parameters || [],
            requestBody: op.requestBody,
            responses: op.responses || {},
            security: op.security,
            moduleId,
          });
        }
      }
    }

    return endpoints;
  }

  /**
   * Extract schemas from merged OpenAPI spec
   */
  private _extractSchemas(): Record<string, any> {
    if (!this.mergedSpec?.components?.schemas) {
      return {};
    }
    return { ...this.mergedSpec.components.schemas };
  }

  /**
   * Build category groupings for modules
   */
  private _buildCategories(): CategoryInfo[] {
    return [
      {
        id: 'identity',
        name: 'Identity & Access',
        description: 'Authentication, users, and permissions',
        modules: this.modules.filter(m => ['auth', 'user-management'].includes(m.id)),
      },
      {
        id: 'data',
        name: 'Data Management',
        description: 'Knowledge bases and records',
        modules: this.modules.filter(m => ['knowledge-base'].includes(m.id)),
      },
      {
        id: 'search',
        name: 'Search & AI',
        description: 'Enterprise search, conversational AI, and agents',
        modules: this.modules.filter(m => ['enterprise-search', 'mcp'].includes(m.id)),
      },
      {
        id: 'integrations',
        name: 'Integrations',
        description: 'Third-party connectors and data sync',
        modules: this.modules.filter(m => ['connector-manager'].includes(m.id)),
      },
      {
        id: 'system',
        name: 'System',
        description: 'Configuration and crawling services',
        modules: this.modules.filter(m => ['configuration-manager', 'crawling-manager'].includes(m.id)),
      },
      {
        id: 'oauth',
        name: 'OAuth App Management',
        description: 'OAuth 2.0 authorization server and app management',
        modules: this.modules.filter(m => ['oauth-app-management'].includes(m.id)),
      },
    ];
  }

  /**
   * Build API info object
   */
  private _buildApiInfo(): UnifiedApiDocs['info'] {
    return {
      title: this.mergedSpec?.info?.title || 'PipesHub API',
      version: this.mergedSpec?.info?.version || '1.0.0',
      description: this.mergedSpec?.info?.description || 'Unified API documentation for PipesHub services',
      contact: this.mergedSpec?.info?.contact || {
        name: 'API Support',
        email: 'contact@pipeshub.com',
      },
    };
  }

  /**
   * Find module ID by endpoint tags
   * @param tags - The tags associated with the endpoint
   * @param xServiceId - The x-service-id extension value if present (preferred)
   */
  private findModuleByTags(tags: string[], xServiceId?: string): string {
    // First priority: use x-service-id extension if present (most reliable)
    if (xServiceId) {
      const validServiceIds = this.modules.map(m => m.id);
      if (validServiceIds.includes(xServiceId)) {
        return xServiceId;
      }
    }

    // Second priority: direct tag matching
    for (const module of this.modules) {
      for (const tag of tags) {
        if (module.tags.includes(tag)) {
          return module.id;
        }
      }
    }

    return 'unknown';
  }

  /**
   * Get a specific module's OpenAPI spec (filtered from merged spec)
   */
  getModuleSpec(moduleId: string): any | null {
    const module = this.modules.find(m => m.id === moduleId);
    if (!module) {
      return null;
    }

    // Filter paths by module tags
    const filteredPaths: Record<string, any> = {};
    if (this.mergedSpec?.paths) {
      for (const [pathKey, pathValue] of Object.entries(this.mergedSpec.paths)) {
        const pathObj = pathValue as any;
        const filteredMethods: Record<string, any> = {};

        for (const [method, operation] of Object.entries(pathObj)) {
          if (['get', 'post', 'put', 'patch', 'delete'].includes(method)) {
            const op = operation as any;
            const opTags = op.tags || [];
            if (opTags.some((tag: string) => module.tags.includes(tag))) {
              filteredMethods[method] = operation;
            }
          }
        }

        if (Object.keys(filteredMethods).length > 0) {
          filteredPaths[pathKey] = filteredMethods;
        }
      }
    }

    // Filter tags
    const filteredTags = this.mergedSpec?.tags?.filter(
      (t: any) => module.tags.includes(t.name)
    ) || [];

    return {
      openapi: '3.0.0',
      info: {
        title: module.name,
        version: module.version,
        description: module.description,
      },
      servers: [{ url: module.basePath, description: `${module.name} API` }],
      tags: filteredTags,
      paths: filteredPaths,
      components: this.mergedSpec?.components || {},
    };
  }

  /**
   * Get all modules metadata
   */
  getModules(): ModuleInfo[] {
    return this.modules.sort((a, b) => a.order - b.order);
  }

  /**
   * Get combined OpenAPI spec
   */
  getCombinedSpec(): any {
    const combinedPaths: Record<string, any> = {};
    const combinedSchemas: Record<string, any> = {};
    const combinedTags: any[] = [];

    // Add from merged spec
    if (this.mergedSpec) {
      if (this.mergedSpec.tags) {
        combinedTags.push(...this.mergedSpec.tags);
      }
      if (this.mergedSpec.paths) {
        Object.assign(combinedPaths, this.mergedSpec.paths);
      }
      if (this.mergedSpec.components?.schemas) {
        Object.assign(combinedSchemas, this.mergedSpec.components.schemas);
      }
    }

    return {
      openapi: '3.0.0',
      info: this.mergedSpec?.info || {
        title: 'PipesHub API',
        version: '1.0.0',
        description: 'Unified API documentation for PipesHub services',
        contact: {
          name: 'API Support',
          email: 'contact@pipeshub.com',
        },
      },
      servers: [{ url: '/api/v1', description: 'Base API URL' }],
      tags: combinedTags,
      paths: combinedPaths,
      components: {
        securitySchemes: this.mergedSpec?.components?.securitySchemes || {
          bearerAuth: {
            type: 'http',
            scheme: 'bearer',
            bearerFormat: 'JWT',
            description: 'JWT Bearer token for authenticated requests',
          },
        },
        schemas: combinedSchemas,
      },
      security: [{ bearerAuth: [] }],
    };
  }

}
