/**
 * API Documentation Service
 * Provides unified OpenAPI documentation from merged-openapi.yaml
 */
import { injectable, inject } from 'inversify';
import * as yaml from 'js-yaml';
import * as fs from 'fs';
import * as path from 'path';
import axios from 'axios';
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
  private mergedSpec: any = null;
  private pythonSpec: any = null;
  private modules: ModuleInfo[] = [];
  private pythonServiceUrl: string;
  private logger: Logger;
  private initialized: boolean = false;

  constructor(@inject('Logger') logger: Logger) {
    this.logger = logger || Logger.getInstance({ service: 'ApiDocsService' });
    this.pythonServiceUrl = process.env.PYTHON_CONNECTOR_URL || 'http://localhost:8000';
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

      // Try to load Python spec
      await this.loadPythonSpec();

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
        id: 'user-management',
        name: 'User Management',
        description: 'User, team, and organization management',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['Users', 'Teams', 'Organizations', 'User Groups'],
        source: 'nodejs',
        order: 2,
      },
      {
        id: 'storage',
        name: 'Storage',
        description: 'Document upload, storage, and version control',
        version: '1.0.0',
        basePath: '/api/v1/document',
        tags: ['Document Upload', 'Document Management', 'Document Buffer', 'Version Control', 'Storage Internal'],
        source: 'nodejs',
        order: 3,
      },
      {
        id: 'knowledge-base',
        name: 'Knowledge Base',
        description: 'Knowledge base and folder management',
        version: '1.0.0',
        basePath: '/api/v1/knowledgeBase',
        tags: ['Knowledge Bases', 'Folders', 'Records', 'Permissions', 'Upload', 'Connector'],
        source: 'nodejs',
        order: 4,
      },
      {
        id: 'enterprise-search',
        name: 'Enterprise Search',
        description: 'Conversational AI and semantic search',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['Conversations', 'Semantic Search'],
        source: 'nodejs',
        order: 5,
      },
      {
        id: 'connector-manager',
        name: 'Connector Manager',
        description: 'Third-party integrations, OAuth flows, and data synchronization',
        version: '1.0.0',
        basePath: '/api/v1/connectors',
        tags: ['Core Connectors', 'Connector Configuration', 'Connector Control', 'Connector OAuth', 'Connector Filters', 'Connector Records', 'Connector Statistics', 'Connector Streaming', 'Connector Webhooks'],
        source: 'nodejs',
        order: 6,
      },
      {
        id: 'configuration-manager',
        name: 'Configuration Manager',
        description: 'System-wide configuration management',
        version: '1.0.0',
        basePath: '/api/v1/configurationManager',
        tags: ['Storage Configuration', 'SMTP Configuration', 'Auth Configuration', 'Database Configuration', 'Platform Settings', 'AI Models Configuration', 'Branding Configuration'],
        source: 'nodejs',
        order: 7,
      },
      {
        id: 'crawling-manager',
        name: 'Crawling Manager',
        description: 'Data crawling job scheduling and monitoring',
        version: '1.0.0',
        basePath: '/api/v1/crawlingManager',
        tags: ['Crawling Jobs', 'Queue Management'],
        source: 'nodejs',
        order: 8,
      },
      {
        id: 'mail',
        name: 'Mail Service',
        description: 'Email sending and SMTP configuration',
        version: '1.0.0',
        basePath: '/api/v1/mail',
        tags: ['Email Operations', 'Email Configuration'],
        source: 'nodejs',
        order: 9,
      },
      {
        id: 'python-connector',
        name: 'Python Connector Service',
        description: 'Knowledge base, records, connectors, and streaming (Python backend)',
        version: '1.0.0',
        basePath: '/api/v1',
        tags: ['Knowledge Base', 'Records', 'Connectors', 'OAuth', 'Streaming'],
        source: 'python',
        order: 10,
      },
    ];
  }

  /**
   * Load Python spec if available
   */
  private async loadPythonSpec(): Promise<void> {
    try {
      const pythonSpec = await this.fetchPythonSpec();
      if (pythonSpec) {
        this.pythonSpec = pythonSpec;
        // Update Python module tags
        const pythonModule = this.modules.find(m => m.id === 'python-connector');
        if (pythonModule) {
          pythonModule.tags = pythonSpec.tags?.map((t: any) => t.name) || [];
        }
        this.logger.info('Python connector spec loaded successfully');
      }
    } catch (error) {
      this.logger.warn('Failed to fetch Python connector spec', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /**
   * Fetch OpenAPI spec from Python service or local file
   */
  private async fetchPythonSpec(): Promise<any | null> {
    // Try to fetch from running Python service first
    try {
      const response = await axios.get(`${this.pythonServiceUrl}/openapi.json`, {
        timeout: 5000,
      });
      this.logger.info('Python spec loaded from running service');
      return response.data;
    } catch {
      // Fall back to local file
      try {
        const localPath = path.join(
          process.cwd(),
          '../../python/app/docs/openapi.yaml'
        );
        if (fs.existsSync(localPath)) {
          const spec = yaml.load(fs.readFileSync(localPath, 'utf8'));
          this.logger.info('Python spec loaded from local file: ' + localPath);
          return spec;
        }
      } catch (err) {
        this.logger.warn('Failed to load Python spec from local file', {
          error: err instanceof Error ? err.message : 'Unknown error',
        });
      }
      return null;
    }
  }

  /**
   * Get unified API documentation
   */
  getUnifiedDocs(): UnifiedApiDocs {
    const endpoints: EndpointInfo[] = [];
    const schemas: Record<string, any> = {};

    // Extract endpoints from merged spec
    if (this.mergedSpec?.paths) {
      for (const [pathKey, pathValue] of Object.entries(this.mergedSpec.paths)) {
        const pathObj = pathValue as any;
        for (const [method, operation] of Object.entries(pathObj)) {
          if (['get', 'post', 'put', 'patch', 'delete'].includes(method)) {
            const op = operation as any;
            const tags = op.tags || [];
            const moduleId = this.findModuleByTags(tags);
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
    }

    // Extract endpoints from Python spec if available
    if (this.pythonSpec?.paths) {
      for (const [pathKey, pathValue] of Object.entries(this.pythonSpec.paths)) {
        const pathObj = pathValue as any;
        for (const [method, operation] of Object.entries(pathObj)) {
          if (['get', 'post', 'put', 'patch', 'delete'].includes(method)) {
            const op = operation as any;
            endpoints.push({
              path: pathKey,
              method: method.toUpperCase(),
              summary: op.summary || '',
              description: op.description || '',
              operationId: op.operationId || '',
              tags: op.tags || [],
              parameters: op.parameters || [],
              requestBody: op.requestBody,
              responses: op.responses || {},
              security: op.security,
              moduleId: 'python-connector',
            });
          }
        }
      }
    }

    // Extract schemas from merged spec
    if (this.mergedSpec?.components?.schemas) {
      Object.assign(schemas, this.mergedSpec.components.schemas);
    }

    // Extract schemas from Python spec
    if (this.pythonSpec?.components?.schemas) {
      for (const [schemaName, schemaValue] of Object.entries(this.pythonSpec.components.schemas)) {
        const prefixedName = `python_${schemaName}`;
        schemas[prefixedName] = schemaValue;
        if (!schemas[schemaName]) {
          schemas[schemaName] = schemaValue;
        }
      }
    }

    // Group modules into categories
    const categories: CategoryInfo[] = [
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
        modules: this.modules.filter(m => ['connector-manager', 'python-connector'].includes(m.id)),
      },
      {
        id: 'system',
        name: 'System',
        description: 'Configuration, crawling, and mail services',
        modules: this.modules.filter(m => ['configuration-manager', 'crawling-manager', 'mail'].includes(m.id)),
      },
    ];

    return {
      info: {
        title: this.mergedSpec?.info?.title || 'PipesHub API',
        version: this.mergedSpec?.info?.version || '1.0.0',
        description: this.mergedSpec?.info?.description || 'Unified API documentation for PipesHub services',
        contact: this.mergedSpec?.info?.contact || {
          name: 'API Support',
          email: 'support@pipeshub.com',
        },
      },
      categories,
      modules: this.modules.sort((a, b) => a.order - b.order),
      endpoints,
      schemas,
    };
  }

  /**
   * Find module ID by endpoint tags
   */
  private findModuleByTags(tags: string[]): string {
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

    // For Python module, return the Python spec
    if (moduleId === 'python-connector') {
      return this.pythonSpec;
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

    // Add from Python spec
    if (this.pythonSpec) {
      if (this.pythonSpec.tags) {
        for (const tag of this.pythonSpec.tags) {
          if (!combinedTags.find(t => t.name === tag.name)) {
            combinedTags.push(tag);
          }
        }
      }
      if (this.pythonSpec.paths) {
        Object.assign(combinedPaths, this.pythonSpec.paths);
      }
      if (this.pythonSpec.components?.schemas) {
        Object.assign(combinedSchemas, this.pythonSpec.components.schemas);
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

  /**
   * Refresh Python spec
   */
  async refreshPythonSpec(): Promise<boolean> {
    try {
      const pythonSpec = await this.fetchPythonSpec();
      if (pythonSpec) {
        this.pythonSpec = pythonSpec;
        const pythonModule = this.modules.find(m => m.id === 'python-connector');
        if (pythonModule) {
          pythonModule.tags = pythonSpec.tags?.map((t: any) => t.name) || [];
        }
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }
}
