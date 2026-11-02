import type { ApiCompiledOperation, ApiCompiledSpec, ApiToolset, ApiToolsetConfig } from '../types';

export function operation(toolName: string, kind: ApiCompiledOperation['kind'], method = 'get'): ApiCompiledOperation {
  return {
    toolName,
    method,
    path: `/${toolName}`,
    summary: `Summary of ${toolName}`,
    kind,
    description: '',
    bodyRequired: false,
    inputSchema: {},
    requiresAuth: true,
  };
}

export function compiledSpec(overrides: Partial<ApiCompiledSpec> = {}): ApiCompiledSpec {
  return {
    title: 'Orders API',
    version: '1.0',
    openapiVersion: '3.1.0',
    description: '',
    servers: [{ url: 'https://orders.internal/v1', description: '' }, { url: 'https://staging', description: '' }],
    authSchemes: [{ name: 'apiKey', type: 'api_key', description: '', location: 'header', paramName: 'X-Key', scopes: {} }],
    operations: [operation('listOrders', 'read'), operation('createOrder', 'write', 'post'), operation('deleteOrder', 'destructive', 'delete')],
    warnings: [],
    specHash: 'h',
    ...overrides,
  };
}

export function toolset(overrides: Partial<ApiToolset> = {}): ApiToolset {
  return {
    id: 'a'.repeat(32),
    name: 'Orders',
    description: '',
    specTitle: 'Orders API',
    authType: 'api_key',
    useSharedCredential: false,
    isConnected: false,
    operations: [operation('listOrders', 'read'), operation('createOrder', 'write', 'post')],
    updatedAt: 1,
    ...overrides,
  };
}

export function toolsetConfig(overrides: Partial<ApiToolsetConfig> = {}): ApiToolsetConfig {
  return {
    ...toolset(),
    baseUrl: 'https://orders.internal/v1',
    authScheme: { name: 'apiKey', type: 'api_key', description: '', scopes: {} },
    scopes: [],
    timeoutSeconds: 30,
    specVersion: '1.0',
    openapiVersion: '3.1.0',
    oauthClientId: null,
    hasOauthClientSecret: false,
    createdBy: 'u1',
    createdAt: 1,
    ...overrides,
  };
}
