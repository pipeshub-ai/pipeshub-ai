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
  source: 'nodejs' | 'python';
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
      this.logger.error('Failed to initialize ApiDocsService', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }

  /**
   * Load the merged OpenAPI spec from YAML file
   */
  private loadMergedSpec(): void {
    const pipeshubOpenapiPath = path.join(__dirname, 'pipeshub-openapi.yaml');

    if (!fs.existsSync(pipeshubOpenapiPath)) {
      throw new Error(`PipesHub OpenAPI spec not found at: ${pipeshubOpenapiPath}`);
    }

    this.mergedSpec = yaml.load(fs.readFileSync(pipeshubOpenapiPath, 'utf8')) as any;
    this.logger.info('Merged OpenAPI spec loaded successfully');
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
        id: 'storage',
        name: 'Storage',
        description: 'Document upload, storage, and version control',
        version: '1.0.0',
        basePath: '/api/v1/document',
        tags: ['Document Upload', 'Document Management', 'Document Buffer', 'Version Control', 'Storage Internal'],
        source: 'nodejs',
        order: 4,
      },
      {
        id: 'knowledge-base',
        name: 'Knowledge Base',
        description: 'Knowledge base and folder management',
        version: '1.0.0',
        basePath: '/api/v1/knowledgeBase',
        tags: ['Knowledge Bases', 'Folders', 'Records', 'Permissions', 'Upload', 'Connector'],
        source: 'nodejs',
        order: 5,
      },
      {
        id: 'enterprise-search',
        name: 'Enterprise Search',
        description: 'Conversational AI and semantic search',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['Conversations', 'Semantic Search'],
        source: 'nodejs',
        order: 6,
      },
      {
        id: 'connector-manager',
        name: 'Connector Manager',
        description: 'Third-party integrations, OAuth flows, and data synchronization',
        version: '1.0.0',
        basePath: '/api/v1/connectors',
        tags: ['Core Connectors', 'Connector Configuration', 'Connector Control', 'Connector OAuth', 'Connector Filters', 'Connector Records', 'Connector Statistics', 'Connector Streaming', 'Connector Webhooks'],
        source: 'nodejs',
        order: 7,
      },
      {
        id: 'configuration-manager',
        name: 'Configuration Manager',
        description: 'System-wide configuration management',
        version: '1.0.0',
        basePath: '/api/v1/configurationManager',
        tags: ['Storage Configuration', 'SMTP Configuration', 'Auth Configuration', 'Database Configuration', 'Platform Settings', 'AI Models Configuration', 'Branding Configuration', 'Metrics Collection'],
        source: 'nodejs',
        order: 8,
      },
      {
        id: 'crawling-manager',
        name: 'Crawling Manager',
        description: 'Data crawling job scheduling and monitoring',
        version: '1.0.0',
        basePath: '/api/v1/crawlingManager',
        tags: ['Crawling Jobs', 'Queue Management'],
        source: 'nodejs',
        order: 9,
      },
      {
        id: 'mail',
        name: 'Mail Service',
        description: 'Email sending and SMTP configuration',
        version: '1.0.0',
        basePath: '/api/v1/mail',
        tags: ['Email Operations', 'Email Configuration'],
        source: 'nodejs',
        order: 10,
      },
      // ==================== INTERNAL PYTHON SERVICES ====================
      // These are internal PipesHub microservices that require scoped service tokens
      {
        id: 'query-service',
        name: 'Query Service',
        description: 'AI search, RAG, and conversational AI (Port 8000). Requires scoped service token.',
        version: '1.0.0',
        basePath: 'http://localhost:8000',
        tags: ['Query Service'],
        source: 'python',
        order: 11,
      },
      {
        id: 'indexing-service',
        name: 'Indexing Service',
        description: 'Document processing and embeddings (Port 8091). Requires scoped service token.',
        version: '1.0.0',
        basePath: 'http://localhost:8091',
        tags: ['Indexing Service'],
        source: 'python',
        order: 12,
      },
      {
        id: 'connector-service-internal',
        name: 'Connector Service',
        description: 'Data source integrations and OAuth (Port 8088). Requires scoped service token.',
        version: '1.0.0',
        basePath: 'http://localhost:8088',
        tags: ['Connector Service'],
        source: 'python',
        order: 13,
      },
      {
        id: 'docling-service',
        name: 'Docling Service',
        description: 'Advanced PDF/document parsing (Port 8081). Internal only.',
        version: '1.0.0',
        basePath: 'http://localhost:8081',
        tags: ['Docling Service'],
        source: 'python',
        order: 14,
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
          // Use x-service-id extension if present, otherwise fall back to tag-based matching
          const xServiceId = op['x-service-id'] as string | undefined;
          const moduleId = this.findModuleByTags(tags, pathKey, op.summary, xServiceId);
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
        description: 'Storage, knowledge bases, and records',
        modules: this.modules.filter(m => ['storage', 'knowledge-base'].includes(m.id)),
      },
      {
        id: 'search',
        name: 'Search & AI',
        description: 'Enterprise search and conversational AI',
        modules: this.modules.filter(m => ['enterprise-search'].includes(m.id)),
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
        description: 'Configuration, crawling, and mail services',
        modules: this.modules.filter(m => ['configuration-manager', 'crawling-manager', 'mail'].includes(m.id)),
      },
      {
        id: 'oauth',
        name: 'OAuth App Management',
        description: 'OAuth 2.0 authorization server and app management',
        modules: this.modules.filter(m => ['oauth-app-management'].includes(m.id)),
      },
      {
        id: 'internal-services',
        name: 'Internal Services',
        description: 'Internal PipesHub microservices (requires scoped token)',
        modules: this.modules.filter(m => ['query-service', 'indexing-service', 'connector-service-internal', 'docling-service'].includes(m.id)),
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
        email: 'support@pipeshub.com',
      },
    };
  }

  /**
   * Find module ID by endpoint tags
   * @param tags - The tags associated with the endpoint
   * @param path - The endpoint path (optional, for legacy fallback)
   * @param summary - The endpoint summary (optional, for legacy fallback)
   * @param xServiceId - The x-service-id extension value if present (preferred)
   */
  private findModuleByTags(tags: string[], path?: string, summary?: string, xServiceId?: string): string {
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

    // Legacy fallback: for 'Internal Services' tag, use path/summary to determine the service
    if (tags.includes('Internal Services') && path) {
      return this.getInternalServiceModuleId(path, summary || '');
    }

    return 'unknown';
  }

  /**
   * Determine which internal service module an endpoint belongs to based on path and summary.
   *
   * @deprecated This method uses brittle string matching as a legacy fallback.
   * Prefer adding `x-service-id` extension to OpenAPI operations for explicit service association.
   * This method is only used for backwards compatibility with specs that don't have x-service-id.
   */
  private getInternalServiceModuleId(path: string, summary: string): string {
    const pathLower = path.toLowerCase();
    const summaryLower = summary.toLowerCase();

    // Check path prefix first
    if (pathLower.startsWith('/query/') || pathLower.includes('/search') || pathLower.includes('/chat')) {
      return 'query-service';
    }
    if (pathLower.startsWith('/indexing/')) {
      return 'indexing-service';
    }
    if (pathLower.startsWith('/connector/')) {
      return 'connector-service-internal';
    }
    if (pathLower.startsWith('/docling/')) {
      return 'docling-service';
    }

    // Check summary for service indicators
    if (summaryLower.includes('[query service]')) {
      return 'query-service';
    }
    if (summaryLower.includes('[indexing service]')) {
      return 'indexing-service';
    }
    if (summaryLower.includes('[connector service]')) {
      return 'connector-service-internal';
    }
    if (summaryLower.includes('[docling service]')) {
      return 'docling-service';
    }

    // Default to query service for unmatched internal endpoints
    return 'query-service';
  }

  /**
   * Get a specific module's OpenAPI spec (filtered from merged spec)
   */
  getModuleSpec(moduleId: string): any | null {
    const module = this.modules.find(m => m.id === moduleId);
    if (!module) {
      return null;
    }

    // For internal service modules, filter from merged spec by tags or path/summary patterns
    const internalServiceIds = ['query-service', 'indexing-service', 'connector-service-internal', 'docling-service'];
    const internalServiceTags: Record<string, string> = {
      'query-service': 'Query Service',
      'indexing-service': 'Indexing Service',
      'connector-service-internal': 'Connector Service',
      'docling-service': 'Docling Service',
    };

    if (internalServiceIds.includes(moduleId)) {
      const serviceTag = internalServiceTags[moduleId];
      // Filter paths from merged spec that belong to this internal service
      const filteredPaths: Record<string, any> = {};
      if (this.mergedSpec?.paths) {
        for (const [pathKey, pathValue] of Object.entries(this.mergedSpec.paths)) {
          const pathObj = pathValue as any;
          const filteredMethods: Record<string, any> = {};

          for (const [method, operation] of Object.entries(pathObj)) {
            if (['get', 'post', 'put', 'patch', 'delete'].includes(method)) {
              const op = operation as any;
              const opTags = op.tags || [];
              const xServiceId = op['x-service-id'] as string | undefined;

              // First priority: use x-service-id extension if present
              if (xServiceId) {
                if (xServiceId === moduleId) {
                  filteredMethods[method] = operation;
                }
                continue;
              }

              // Second priority: check if endpoint belongs to this service's tag
              if (opTags.includes(serviceTag)) {
                filteredMethods[method] = operation;
                continue;
              }

              // Legacy fallback: for 'Internal Services' tag, use path/summary to determine service
              if (opTags.includes('Internal Services')) {
                const endpointModuleId = this.getInternalServiceModuleId(pathKey, op.summary || '');
                if (endpointModuleId === moduleId) {
                  filteredMethods[method] = operation;
                }
              }
            }
          }

          if (Object.keys(filteredMethods).length > 0) {
            filteredPaths[pathKey] = filteredMethods;
          }
        }
      }

      return {
        openapi: '3.0.0',
        info: {
          title: module.name,
          version: module.version,
          description: module.description,
        },
        servers: [{ url: module.basePath, description: `${module.name} (Internal)` }],
        tags: [{ name: serviceTag, description: module.description }],
        paths: filteredPaths,
        components: this.mergedSpec?.components || {},
      };
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
          email: 'support@pipeshub.com',
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
