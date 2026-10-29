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
  icon?: string;
  /** The accordions under this nav item, in sidebar order. */
  tags: string[];
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
  /** Path prefix from the operation's `servers` (or the spec's), e.g. `/api/v1`. */
  basePath: string;
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

/** An entry of the spec's `x-pipeshub-nav`: a sidebar heading, its nav items and their tags. */
interface NavCategorySpec {
  name?: string;
  description?: string;
  modules?: NavModuleSpec[];
}

interface NavModuleSpec {
  name?: string;
  description?: string;
  icon?: string;
  tags?: string[];
}

const specOverlays: string[] = [];

const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete'];

/** Heading of the sidebar for tags with endpoints that the nav does not list. */
const OTHER_CATEGORY = 'Other';

const slug = (name: string): string =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');

/** `{instance_url}/api/v1` -> `/api/v1`; an absolute URL is kept whole. */
const serverBasePath = (servers: unknown): string => {
  const url = Array.isArray(servers) ? (servers[0] as { url?: unknown } | undefined)?.url : undefined;
  return typeof url === 'string' ? url.replace(/^\{[^}]+\}/, '').replace(/\/$/, '') : '';
};

type SpecNode = Record<string, unknown>;

const isPlainObject = (value: unknown): value is SpecNode =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * Objects merge key by key; arrays keep every distinct item; anything else is replaced.
 * An object is always copied through this walk, never assigned whole, so a nested
 * __proto__ key cannot ride in on a value that replaces a non-object.
 */
function mergeSpec(target: SpecNode, source: SpecNode): void {
  for (const [key, value] of Object.entries(source)) {
    // Keys that would write through to Object.prototype instead of the spec.
    if (key === '__proto__' || key === 'constructor' || key === 'prototype')
      continue;
    const current = target[key];
    if (isPlainObject(value)) {
      const into: SpecNode = isPlainObject(current) ? current : {};
      mergeSpec(into, value);
      target[key] = into;
    } else if (Array.isArray(current) && Array.isArray(value)) {
      const existing: unknown[] = current;
      const added: unknown[] = value;
      const seen = new Set(existing.map((item) => JSON.stringify(item)));
      target[key] = [
        ...existing,
        ...added.filter((item) => !seen.has(JSON.stringify(item))),
      ];
    } else {
      target[key] = value;
    }
  }
}

@injectable()
export class ApiDocsService {
  /** An extra spec file merged into the loaded spec, for paths documented outside this file. */
  static registerSpecOverlay(filePath: string): void {
    if (!specOverlays.includes(filePath)) {
      specOverlays.push(filePath);
    }
  }

  static clearSpecOverlays(): void {
    specOverlays.length = 0;
  }

  private mergedSpec: Record<string, any> | null = null;
  private endpoints: EndpointInfo[] = [];
  private categories: CategoryInfo[] = [];
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

      this.endpoints = this._extractEndpoints();
      this._buildNavigation();

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
      const spec = yaml.load(fs.readFileSync(specPath, 'utf8')) as SpecNode;
      this.mergedSpec = spec;
      for (const overlayPath of specOverlays) {
        try {
          mergeSpec(
            spec,
            yaml.load(fs.readFileSync(overlayPath, 'utf8')) as SpecNode,
          );
        } catch (error) {
          this.logger.warn('Skipped an OpenAPI spec overlay', {
            overlayPath,
            error: error instanceof Error ? error.message : 'Unknown error',
          });
        }
      }
      this.logger.info('Merged OpenAPI spec loaded successfully');
    } catch (error) {
      this.logger.warn('Failed to load merged OpenAPI spec', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return;
    }
  }

  /**
   * Get unified API documentation
   */
  getUnifiedDocs(): UnifiedApiDocs {
    return {
      info: this._buildApiInfo(),
      categories: this.categories,
      modules: this.modules,
      endpoints: this.endpoints,
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
        if (HTTP_METHODS.includes(method)) {
          const op = operation as any;
          endpoints.push({
            path: pathKey,
            method: method.toUpperCase(),
            summary: op.summary || '',
            description: op.description || '',
            operationId: op.operationId || '',
            tags: op.tags?.length ? op.tags : [OTHER_CATEGORY],
            parameters: op.parameters || [],
            requestBody: op.requestBody,
            responses: op.responses || {},
            security: op.security,
            basePath: serverBasePath(op.servers || pathObj.servers || this.mergedSpec.servers),
            moduleId: '',
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
   * Build the sidebar from the spec's `x-pipeshub-nav` (overlays add to it): heading -> nav item -> tags.
   * A tag belongs to the first nav item that lists it; a tag without endpoints is
   * dropped, then an empty nav item or heading. A tag with endpoints listed nowhere
   * gets its own nav item under "Other", so no endpoint is ever left out.
   */
  private _buildNavigation(): void {
    const usedTags = new Set(this.endpoints.flatMap((e) => e.tags));
    const claimed = new Set<string>();
    const categories: CategoryInfo[] = [];

    // The nav is unchecked YAML, so any entry of it may be missing.
    const nav: unknown = this.mergedSpec?.['x-pipeshub-nav'];
    const navSpec = (Array.isArray(nav) ? nav : []) as (
      | NavCategorySpec
      | undefined
    )[];
    for (const categorySpec of navSpec) {
      if (typeof categorySpec?.name !== 'string' || !categorySpec.name) continue;
      // Merge by name, so an overlay can add nav items to a heading of the base spec.
      let category = categories.find((c) => c.name === categorySpec.name);
      if (!category) {
        category = {
          id: slug(categorySpec.name),
          name: categorySpec.name,
          description: categorySpec.description || '',
          modules: [],
        };
        categories.push(category);
      }
      const moduleSpecs = Array.isArray(categorySpec.modules) ? categorySpec.modules : [];
      for (const moduleSpec of moduleSpecs as (NavModuleSpec | undefined)[]) {
        if (typeof moduleSpec?.name !== 'string' || !moduleSpec.name) continue;
        const tags = (Array.isArray(moduleSpec.tags) ? moduleSpec.tags : []).filter(
          (t) => usedTags.has(t) && !claimed.has(t),
        );
        tags.forEach((t) => claimed.add(t));
        const existing = category.modules.find((m) => m.name === moduleSpec.name);
        if (existing) {
          existing.tags.push(...tags);
        } else {
          category.modules.push({
            id: slug(moduleSpec.name),
            name: moduleSpec.name,
            description: moduleSpec.description || '',
            icon: moduleSpec.icon,
            tags,
          });
        }
      }
    }

    const specTags: unknown = this.mergedSpec?.tags;
    const declaredTags = (Array.isArray(specTags) ? specTags : []).filter(
      (t): t is { name: string; description?: string } => typeof t?.name === 'string',
    );
    const unlisted = [...new Set([...declaredTags.map((t) => t.name), ...usedTags])]
      .filter((t) => usedTags.has(t) && !claimed.has(t));
    if (unlisted.length > 0) {
      let other = categories.find((c) => c.name === OTHER_CATEGORY);
      if (!other) {
        other = { id: slug(OTHER_CATEGORY), name: OTHER_CATEGORY, description: '', modules: [] };
        categories.push(other);
      }
      for (const tag of unlisted) {
        other.modules.push({
          // Prefixed so it cannot clash with a nav item of the same name.
          id: `tag-${slug(tag)}`,
          name: tag,
          description: declaredTags.find((t) => t.name === tag)?.description || '',
          tags: [tag],
        });
      }
    }

    for (const category of categories) {
      category.modules = category.modules.filter((m) => m.tags.length > 0);
    }
    this.categories = categories.filter((c) => c.modules.length > 0);
    this.modules = this.categories.flatMap((c) => c.modules);

    for (const endpoint of this.endpoints) {
      const [firstTag = ''] = endpoint.tags;
      endpoint.moduleId = this.modules.find((m) => m.tags.includes(firstTag))?.id || '';
    }
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
   * Get a specific module's OpenAPI spec (filtered from merged spec)
   */
  getModuleSpec(moduleId: string): any | null {
    const module = this.modules.find(m => m.id === moduleId);
    if (!module) {
      return null;
    }

    const filteredPaths: Record<string, any> = {};
    for (const endpoint of this.endpoints) {
      if (!endpoint.tags.some((tag) => module.tags.includes(tag))) continue;
      const method = endpoint.method.toLowerCase();
      filteredPaths[endpoint.path] = {
        ...filteredPaths[endpoint.path],
        [method]: this.mergedSpec?.paths?.[endpoint.path]?.[method],
      };
    }

    return {
      openapi: '3.0.0',
      info: {
        title: module.name,
        version: this.mergedSpec?.info?.version || '1.0.0',
        description: module.description,
      },
      servers: this.mergedSpec?.servers || [],
      tags: this.mergedSpec?.tags?.filter((t: any) => module.tags.includes(t.name)) || [],
      paths: filteredPaths,
      components: this.mergedSpec?.components || {},
    };
  }

  /**
   * Get all modules metadata, in sidebar order
   */
  getModules(): ModuleInfo[] {
    return this.modules;
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
