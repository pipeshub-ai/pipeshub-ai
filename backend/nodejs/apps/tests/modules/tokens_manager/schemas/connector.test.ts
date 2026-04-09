import 'reflect-metadata';
import { expect } from 'chai';
import {
  createInstanceSchema,
  idParamSchema,
  listSchema,
  typeParamSchema,
  updateInstanceNameSchema,
  updateInstanceAuthConfigSchema,
  updateInstanceFiltersSyncConfigSchema,
  getOAuthAuthorizationUrlSchema,
  handleOAuthCallbackSchema,
  getFilterFieldOptionsSchema,
  toggleSchema,
  successSchema,
  toggleResponseSchema,
  paginationResponseSchema,
  scopeCountsResponseSchema,
  instanceConfigResponseSchema,
  authConfigUpdateResponseSchema,
  filtersSyncConfigUpdateResponseSchema,
  nameUpdateResponseSchema,
  filterFieldOptionsResponseSchema,
  oauthAuthorizeResponseSchema,
  oauthCallbackResponseSchema,
  registryResponseSchema,
  schemaResponseSchema,
  instancesResponseSchema,
  activeInactiveResponseSchema,
  activeAgentInstancesResponseSchema,
  configuredResponseSchema,
  createResponseSchema,
  instanceDetailResponseSchema,
  deleteResponseSchema,
} from '../../../../src/modules/tokens_manager/schemas/connector.schema';

// ============================================================================
// Test helpers
// ============================================================================

const validConnectorId = 'ebd5cb6a-a2dd-4065-af23-e70b392ffd1b';

// ============================================================================
// createInstanceSchema
// ============================================================================

describe('createInstanceSchema', () => {
  it('should accept minimal valid body (required fields only)', () => {
    const result = createInstanceSchema.safeParse({
      body: {
        connectorType: 'Jira',
        instanceName: 'My Jira',
        scope: 'team',
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept full valid body with all optional fields', () => {
    const result = createInstanceSchema.safeParse({
      body: {
        connectorType: 'Jira',
        instanceName: 'My Jira',
        scope: 'personal',
        authType: 'OAUTH',
        baseUrl: 'https://app.example.com',
        config: {
          auth: { oauthConfigId: 'some-id' },
          sync: { selectedStrategy: 'MANUAL' },
          filters: {},
        },
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept scope "personal"', () => {
    const result = createInstanceSchema.safeParse({
      body: { connectorType: 'Slack', instanceName: 'Slack', scope: 'personal' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectorType', () => {
    const result = createInstanceSchema.safeParse({
      body: { instanceName: 'My Jira', scope: 'team' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorType', () => {
    const result = createInstanceSchema.safeParse({
      body: { connectorType: '', instanceName: 'My Jira', scope: 'team' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing instanceName', () => {
    const result = createInstanceSchema.safeParse({
      body: { connectorType: 'Jira', scope: 'team' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty instanceName', () => {
    const result = createInstanceSchema.safeParse({
      body: { connectorType: 'Jira', instanceName: '', scope: 'team' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing scope', () => {
    const result = createInstanceSchema.safeParse({
      body: { connectorType: 'Jira', instanceName: 'My Jira' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject invalid scope value', () => {
    const result = createInstanceSchema.safeParse({
      body: { connectorType: 'Jira', instanceName: 'My Jira', scope: 'global' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body entirely', () => {
    const result = createInstanceSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// idParamSchema
// ============================================================================

describe('idParamSchema', () => {
  it('should accept a valid connectorId param', () => {
    const result = idParamSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept any non-empty string as connectorId', () => {
    const result = idParamSchema.safeParse({
      params: { connectorId: 'any-string-id' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectorId', () => {
    const result = idParamSchema.safeParse({ params: {} });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId', () => {
    const result = idParamSchema.safeParse({
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing params', () => {
    const result = idParamSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// listSchema
// ============================================================================

describe('listSchema', () => {
  it('should accept empty query (all optional)', () => {
    const result = listSchema.safeParse({ query: {} });
    expect(result.success).to.be.true;
  });

  it('should accept valid scope "team"', () => {
    const result = listSchema.safeParse({ query: { scope: 'team' } });
    expect(result.success).to.be.true;
  });

  it('should accept valid scope "personal"', () => {
    const result = listSchema.safeParse({ query: { scope: 'personal' } });
    expect(result.success).to.be.true;
  });

  it('should reject invalid scope', () => {
    const result = listSchema.safeParse({ query: { scope: 'org' } });
    expect(result.success).to.be.false;
  });

  it('should accept valid numeric page and limit as strings (preprocess)', () => {
    const result = listSchema.safeParse({
      query: { page: '2', limit: '50' },
    });
    expect(result.success).to.be.true;
  });

  it('should accept valid numeric page and limit as numbers', () => {
    const result = listSchema.safeParse({
      query: { page: 3, limit: 100 },
    });
    expect(result.success).to.be.true;
  });

  it('should reject page less than 1', () => {
    const result = listSchema.safeParse({ query: { page: '0' } });
    expect(result.success).to.be.false;
  });

  it('should reject limit greater than 200', () => {
    const result = listSchema.safeParse({ query: { limit: '201' } });
    expect(result.success).to.be.false;
  });

  it('should reject limit less than 1', () => {
    const result = listSchema.safeParse({ query: { limit: '0' } });
    expect(result.success).to.be.false;
  });

  it('should accept a search string', () => {
    const result = listSchema.safeParse({ query: { search: 'jira' } });
    expect(result.success).to.be.true;
  });

  it('should treat empty string page as undefined (no error)', () => {
    const result = listSchema.safeParse({ query: { page: '' } });
    expect(result.success).to.be.true;
  });

  it('should treat empty string limit as undefined (no error)', () => {
    const result = listSchema.safeParse({ query: { limit: '' } });
    expect(result.success).to.be.true;
  });

  it('should reject missing query entirely', () => {
    const result = listSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// typeParamSchema
// ============================================================================

describe('typeParamSchema', () => {
  it('should accept a valid connectorType param', () => {
    const result = typeParamSchema.safeParse({
      params: { connectorType: 'Jira' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject empty connectorType', () => {
    const result = typeParamSchema.safeParse({
      params: { connectorType: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorType', () => {
    const result = typeParamSchema.safeParse({ params: {} });
    expect(result.success).to.be.false;
  });

  it('should reject missing params', () => {
    const result = typeParamSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// updateInstanceNameSchema
// ============================================================================

describe('updateInstanceNameSchema', () => {
  it('should accept valid body and params', () => {
    const result = updateInstanceNameSchema.safeParse({
      body: { instanceName: 'New Name' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing instanceName', () => {
    const result = updateInstanceNameSchema.safeParse({
      body: {},
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty instanceName', () => {
    const result = updateInstanceNameSchema.safeParse({
      body: { instanceName: '' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId param', () => {
    const result = updateInstanceNameSchema.safeParse({
      body: { instanceName: 'New Name' },
      params: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId param', () => {
    const result = updateInstanceNameSchema.safeParse({
      body: { instanceName: 'New Name' },
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body', () => {
    const result = updateInstanceNameSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// updateInstanceAuthConfigSchema
// ============================================================================

describe('updateInstanceAuthConfigSchema', () => {
  it('should accept valid body with auth object', () => {
    const result = updateInstanceAuthConfigSchema.safeParse({
      body: { auth: { oauthConfigId: 'some-id', authType: 'OAUTH' } },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept body with optional baseUrl', () => {
    const result = updateInstanceAuthConfigSchema.safeParse({
      body: {
        auth: { clientId: 'id', clientSecret: 'secret' },
        baseUrl: 'https://app.example.com',
      },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept auth as empty object (z.any() allows it)', () => {
    const result = updateInstanceAuthConfigSchema.safeParse({
      body: { auth: {} },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing auth field', () => {
    const result = updateInstanceAuthConfigSchema.safeParse({
      body: {},
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId param', () => {
    const result = updateInstanceAuthConfigSchema.safeParse({
      body: { auth: {} },
      params: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId param', () => {
    const result = updateInstanceAuthConfigSchema.safeParse({
      body: { auth: {} },
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body', () => {
    const result = updateInstanceAuthConfigSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// updateInstanceFiltersSyncConfigSchema
// ============================================================================

describe('updateInstanceFiltersSyncConfigSchema', () => {
  it('should accept empty body (both sync and filters are optional)', () => {
    const result = updateInstanceFiltersSyncConfigSchema.safeParse({
      body: {},
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept body with only sync', () => {
    const result = updateInstanceFiltersSyncConfigSchema.safeParse({
      body: { sync: { selectedStrategy: 'MANUAL' } },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept body with only filters', () => {
    const result = updateInstanceFiltersSyncConfigSchema.safeParse({
      body: { filters: { sync: { values: {} } } },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept body with both sync and filters', () => {
    const result = updateInstanceFiltersSyncConfigSchema.safeParse({
      body: {
        sync: { selectedStrategy: 'SCHEDULED' },
        filters: { indexing: { values: {} } },
      },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectorId', () => {
    const result = updateInstanceFiltersSyncConfigSchema.safeParse({
      body: {},
      params: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId', () => {
    const result = updateInstanceFiltersSyncConfigSchema.safeParse({
      body: {},
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body', () => {
    const result = updateInstanceFiltersSyncConfigSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// getOAuthAuthorizationUrlSchema
// ============================================================================

describe('getOAuthAuthorizationUrlSchema', () => {
  it('should accept valid params with empty query', () => {
    const result = getOAuthAuthorizationUrlSchema.safeParse({
      params: { connectorId: validConnectorId },
      query: {},
    });
    expect(result.success).to.be.true;
  });

  it('should accept valid params with optional baseUrl', () => {
    const result = getOAuthAuthorizationUrlSchema.safeParse({
      params: { connectorId: validConnectorId },
      query: { baseUrl: 'https://app.example.com' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectorId', () => {
    const result = getOAuthAuthorizationUrlSchema.safeParse({
      params: {},
      query: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId', () => {
    const result = getOAuthAuthorizationUrlSchema.safeParse({
      params: { connectorId: '' },
      query: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing params', () => {
    const result = getOAuthAuthorizationUrlSchema.safeParse({ query: {} });
    expect(result.success).to.be.false;
  });

  it('should reject missing query', () => {
    const result = getOAuthAuthorizationUrlSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// handleOAuthCallbackSchema
// ============================================================================

describe('handleOAuthCallbackSchema', () => {
  it('should accept empty query (all fields optional)', () => {
    const result = handleOAuthCallbackSchema.safeParse({ query: {} });
    expect(result.success).to.be.true;
  });

  it('should accept query with code and state (success flow)', () => {
    const result = handleOAuthCallbackSchema.safeParse({
      query: {
        code: 'auth-code-xyz',
        state: 'state-token-abc',
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept query with error (failure flow)', () => {
    const result = handleOAuthCallbackSchema.safeParse({
      query: { error: 'access_denied' },
    });
    expect(result.success).to.be.true;
  });

  it('should accept all optional query fields together', () => {
    const result = handleOAuthCallbackSchema.safeParse({
      query: {
        baseUrl: 'https://app.example.com',
        code: 'auth-code',
        state: 'state-token',
        error: 'some_error',
      },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing query entirely', () => {
    const result = handleOAuthCallbackSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// getFilterFieldOptionsSchema
// ============================================================================

describe('getFilterFieldOptionsSchema', () => {
  it('should accept valid params with empty query', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId, filterKey: 'project_keys' },
      query: {},
    });
    expect(result.success).to.be.true;
  });

  it('should accept valid params with all query fields', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId, filterKey: 'project_keys' },
      query: {
        page: '1',
        limit: '50',
        search: 'PROJ',
        cursor: 'next-cursor-token',
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept page and limit as numbers', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId, filterKey: 'project_keys' },
      query: { page: 2, limit: 100 },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectorId', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { filterKey: 'project_keys' },
      query: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: '', filterKey: 'project_keys' },
      query: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing filterKey', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId },
      query: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty filterKey', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId, filterKey: '' },
      query: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject page less than 1', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId, filterKey: 'project_keys' },
      query: { page: '0' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject limit greater than 200', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId, filterKey: 'project_keys' },
      query: { limit: '201' },
    });
    expect(result.success).to.be.false;
  });

  it('should treat empty string page as undefined (no error)', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId, filterKey: 'project_keys' },
      query: { page: '' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing params', () => {
    const result = getFilterFieldOptionsSchema.safeParse({ query: {} });
    expect(result.success).to.be.false;
  });

  it('should reject missing query', () => {
    const result = getFilterFieldOptionsSchema.safeParse({
      params: { connectorId: validConnectorId, filterKey: 'project_keys' },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// toggleSchema
// ============================================================================

describe('toggleSchema', () => {
  it('should accept type "sync" with valid connectorId', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'sync' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept type "agent" with valid connectorId', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'agent' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept type "sync" with optional fullSync true', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'sync', fullSync: true },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept type "sync" with optional fullSync false', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'sync', fullSync: false },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should reject invalid type value', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'realtime' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing type field', () => {
    const result = toggleSchema.safeParse({
      body: {},
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject non-boolean fullSync', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'sync', fullSync: 'yes' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'sync' },
      params: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'sync' },
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body', () => {
    const result = toggleSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing params', () => {
    const result = toggleSchema.safeParse({
      body: { type: 'sync' },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// RESPONSE VALIDATORS
// ============================================================================

// ============================================================================
// Shared fixtures — reused across multiple response schema tests
// ============================================================================

const validPagination = {
  page: 1,
  limit: 20,
  search: null,
  totalCount: 5,
  totalPages: 1,
  hasPrev: false,
  hasNext: false,
  prevPage: null,
  nextPage: null,
};

const validScopeCounts = { personal: 2, team: 3 };

const validWebhookConfig = {
  supported: false,
  webhookUrl: '',
  events: [],
  verificationToken: '',
  secretKey: '',
};

const validScheduledConfig = {
  intervalMinutes: 60,
  cronExpression: '',
  timezone: 'UTC',
  startTime: 0,
  nextTime: 0,
  endTime: 0,
  maxRepetitions: 0,
  repetitionCount: 0,
};

const validRealtimeConfig = { supported: false, connectionType: 'WEBSOCKET' };

const validAuthConfig = {
  supportedAuthTypes: ['OAUTH'],
  schemas: {},
  oauthConfigs: {},
  values: {},
  customFields: [],
  customValues: {},
  conditionalDisplay: {},
};

const validSyncConfig = {
  supportedStrategies: ['MANUAL'],
  selectedStrategy: 'MANUAL',
  webhookConfig: validWebhookConfig,
  scheduledConfig: validScheduledConfig,
  realtimeConfig: validRealtimeConfig,
  customFields: [],
  customValues: {},
  values: {},
};

const validFiltersConfig = {
  sync: { schema: { fields: [] }, values: {} },
  indexing: { schema: { fields: [] }, values: {} },
};

const validConnectorConfig = {
  iconPath: '/assets/icons/connectors/jira.svg',
  supportsRealtime: false,
  supportsSync: true,
  supportsAgent: true,
  documentationLinks: [],
  hideConnector: false,
  auth: validAuthConfig,
  sync: validSyncConfig,
  filters: validFiltersConfig,
};

const validRegistryItem = {
  name: 'Jira',
  type: 'Jira',
  appGroup: 'Atlassian',
  supportedAuthTypes: ['OAUTH'],
  appDescription: 'Sync issues from Jira Cloud',
  appCategories: ['Storage'],
  iconPath: '/assets/icons/connectors/jira.svg',
  supportsRealtime: false,
  supportsSync: true,
  supportsAgent: true,
  config: validConnectorConfig,
  scope: 'team',
  connectorInfo: null,
};

const validInstanceItem = {
  ...validRegistryItem,
  scope: 'team',
  _key: 'ebd5cb6a-a2dd-4065-af23-e70b392ffd1b',
  authType: 'OAUTH',
  isActive: false,
  isAgentActive: false,
  isConfigured: true,
  isAuthenticated: false,
  isLocked: false,
  status: null,
  createdAtTimestamp: 1775223143709,
  updatedAtTimestamp: 1775414619175,
  createdBy: '69ca204b7402ef0dbf62daba',
  updatedBy: '69ca204b7402ef0dbf62daba',
};

// ============================================================================
// successSchema
// ============================================================================

describe('successSchema', () => {
  it('should accept { success: true }', () => {
    expect(successSchema.safeParse({ success: true }).success).to.be.true;
  });

  it('should accept { success: false }', () => {
    expect(successSchema.safeParse({ success: false }).success).to.be.true;
  });

  it('should reject missing success field', () => {
    expect(successSchema.safeParse({}).success).to.be.false;
  });

  it('should reject non-boolean success', () => {
    expect(successSchema.safeParse({ success: 'true' }).success).to.be.false;
  });
});

// ============================================================================
// toggleResponseSchema
// ============================================================================

describe('toggleResponseSchema', () => {
  it('should accept valid response', () => {
    const result = toggleResponseSchema.safeParse({
      success: true,
      message: 'Connector toggled successfully.',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing message', () => {
    const result = toggleResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject unknown extra keys (strict)', () => {
    const result = toggleResponseSchema.safeParse({
      success: true,
      message: 'ok',
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// paginationResponseSchema
// ============================================================================

describe('paginationResponseSchema', () => {
  it('should accept valid pagination', () => {
    expect(paginationResponseSchema.safeParse(validPagination).success).to.be.true;
  });

  it('should accept non-null search string', () => {
    const result = paginationResponseSchema.safeParse({ ...validPagination, search: 'jira' });
    expect(result.success).to.be.true;
  });

  it('should accept non-null prevPage and nextPage numbers', () => {
    const result = paginationResponseSchema.safeParse({
      ...validPagination,
      page: 2,
      hasPrev: true,
      prevPage: 1,
      hasNext: true,
      nextPage: 3,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing totalCount', () => {
    const { totalCount: _, ...rest } = validPagination;
    expect(paginationResponseSchema.safeParse(rest).success).to.be.false;
  });

  it('should reject non-boolean hasPrev', () => {
    const result = paginationResponseSchema.safeParse({ ...validPagination, hasPrev: 'false' });
    expect(result.success).to.be.false;
  });

  it('should reject unknown extra keys (strict)', () => {
    const result = paginationResponseSchema.safeParse({ ...validPagination, extra: true });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// scopeCountsResponseSchema
// ============================================================================

describe('scopeCountsResponseSchema', () => {
  it('should accept valid scope counts', () => {
    expect(scopeCountsResponseSchema.safeParse(validScopeCounts).success).to.be.true;
  });

  it('should accept zero counts', () => {
    expect(scopeCountsResponseSchema.safeParse({ personal: 0, team: 0 }).success).to.be.true;
  });

  it('should reject missing personal', () => {
    expect(scopeCountsResponseSchema.safeParse({ team: 1 }).success).to.be.false;
  });

  it('should reject missing team', () => {
    expect(scopeCountsResponseSchema.safeParse({ personal: 1 }).success).to.be.false;
  });

  it('should reject non-number counts', () => {
    expect(scopeCountsResponseSchema.safeParse({ personal: '2', team: 3 }).success).to.be.false;
  });
});

// ============================================================================
// oauthAuthorizeResponseSchema
// ============================================================================

describe('oauthAuthorizeResponseSchema', () => {
  it('should accept valid OAuth authorize response', () => {
    const result = oauthAuthorizeResponseSchema.safeParse({
      success: true,
      authorizationUrl: 'https://accounts.google.com/o/oauth2/auth?...',
      state: 'random-state-token',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing authorizationUrl', () => {
    const result = oauthAuthorizeResponseSchema.safeParse({
      success: true,
      state: 'state-token',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing state', () => {
    const result = oauthAuthorizeResponseSchema.safeParse({
      success: true,
      authorizationUrl: 'https://accounts.google.com/...',
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra unknown keys (strict)', () => {
    const result = oauthAuthorizeResponseSchema.safeParse({
      success: true,
      authorizationUrl: 'https://...',
      state: 'state',
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// oauthCallbackResponseSchema
// ============================================================================

describe('oauthCallbackResponseSchema', () => {
  it('should accept valid OAuth callback response', () => {
    const result = oauthCallbackResponseSchema.safeParse({
      success: true,
      redirect_url: 'https://app.example.com/connectors?status=success',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing redirect_url', () => {
    const result = oauthCallbackResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject extra unknown keys (strict)', () => {
    const result = oauthCallbackResponseSchema.safeParse({
      success: true,
      redirect_url: 'https://...',
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// nameUpdateResponseSchema
// ============================================================================

describe('nameUpdateResponseSchema', () => {
  it('should accept valid name update response', () => {
    const result = nameUpdateResponseSchema.safeParse({
      success: true,
      connector: { _key: 'some-uuid', name: 'New Name' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connector field', () => {
    const result = nameUpdateResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject missing _key in connector', () => {
    const result = nameUpdateResponseSchema.safeParse({
      success: true,
      connector: { name: 'New Name' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing name in connector', () => {
    const result = nameUpdateResponseSchema.safeParse({
      success: true,
      connector: { _key: 'some-uuid' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra keys in connector (strict)', () => {
    const result = nameUpdateResponseSchema.safeParse({
      success: true,
      connector: { _key: 'some-uuid', name: 'New Name', extra: true },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// deleteResponseSchema
// ============================================================================

describe('deleteResponseSchema', () => {
  it('should accept valid delete response', () => {
    const result = deleteResponseSchema.safeParse({
      success: true,
      message: 'Connector deletion initiated',
      connectorId: 'ebd5cb6a-a2dd-4065-af23-e70b392ffd1b',
      status: 'DELETING',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing message', () => {
    const result = deleteResponseSchema.safeParse({
      success: true,
      connectorId: 'some-id',
      status: 'DELETING',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId', () => {
    const result = deleteResponseSchema.safeParse({
      success: true,
      message: 'Connector deletion initiated',
      status: 'DELETING',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing status', () => {
    const result = deleteResponseSchema.safeParse({
      success: true,
      message: 'Connector deletion initiated',
      connectorId: 'some-id',
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra unknown keys (strict)', () => {
    const result = deleteResponseSchema.safeParse({
      success: true,
      message: 'ok',
      connectorId: 'id',
      status: 'DELETING',
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// filterFieldOptionsResponseSchema
// ============================================================================

describe('filterFieldOptionsResponseSchema', () => {
  const validOptions = [{ id: 'PST', label: 'Pipeshub Security Test' }];

  it('should accept valid response with no cursor or message', () => {
    const result = filterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: validOptions,
      page: 1,
      limit: 50,
      hasMore: false,
    });
    expect(result.success).to.be.true;
  });

  it('should accept response with optional cursor', () => {
    const result = filterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: validOptions,
      page: 1,
      limit: 50,
      hasMore: true,
      cursor: 'next-cursor-token',
    });
    expect(result.success).to.be.true;
  });

  it('should accept cursor as null', () => {
    const result = filterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: [],
      page: 1,
      limit: 50,
      hasMore: false,
      cursor: null,
    });
    expect(result.success).to.be.true;
  });

  it('should accept optional message field', () => {
    const result = filterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: [],
      page: 1,
      limit: 50,
      hasMore: false,
      message: 'No options found',
    });
    expect(result.success).to.be.true;
  });

  it('should accept empty options array', () => {
    const result = filterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: [],
      page: 1,
      limit: 50,
      hasMore: false,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing options', () => {
    const result = filterFieldOptionsResponseSchema.safeParse({
      success: true,
      page: 1,
      limit: 50,
      hasMore: false,
    });
    expect(result.success).to.be.false;
  });

  it('should reject option missing id', () => {
    const result = filterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: [{ label: 'Only Label' }],
      page: 1,
      limit: 50,
      hasMore: false,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing hasMore', () => {
    const result = filterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: [],
      page: 1,
      limit: 50,
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// createResponseSchema
// ============================================================================

describe('createResponseSchema', () => {
  const validConnector = {
    connectorId: 'ebd5cb6a-a2dd-4065-af23-e70b392ffd1b',
    connectorType: 'Jira',
    instanceName: 'My Jira',
    created: true,
    scope: 'team' as const,
    createdBy: '69ca204b7402ef0dbf62daba',
    isAuthenticated: false,
    isConfigured: true,
  };

  it('should accept valid create response', () => {
    const result = createResponseSchema.safeParse({
      success: true,
      connector: validConnector,
      message: 'Connector instance created successfully.',
    });
    expect(result.success).to.be.true;
  });

  it('should accept scope "personal"', () => {
    const result = createResponseSchema.safeParse({
      success: true,
      connector: { ...validConnector, scope: 'personal' },
      message: 'Connector instance created successfully.',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing message', () => {
    const result = createResponseSchema.safeParse({
      success: true,
      connector: validConnector,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connector', () => {
    const result = createResponseSchema.safeParse({
      success: true,
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId in connector', () => {
    const { connectorId: _, ...rest } = validConnector;
    const result = createResponseSchema.safeParse({
      success: true,
      connector: rest,
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject invalid scope in connector', () => {
    const result = createResponseSchema.safeParse({
      success: true,
      connector: { ...validConnector, scope: 'global' },
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra keys in connector (strict)', () => {
    const result = createResponseSchema.safeParse({
      success: true,
      connector: { ...validConnector, extra: 'field' },
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// instanceConfigResponseSchema
// ============================================================================

describe('instanceConfigResponseSchema', () => {
  const minimalNestedStoredConfig = {
    auth: {},
    sync: {},
    filters: {},
  };

  const validConfig = {
    success: true,
    config: {
      connector_id: 'ebd5cb6a-a2dd-4065-af23-e70b392ffd1b',
      name: 'Ji',
      type: 'Jira',
      appGroup: 'Atlassian',
      authType: 'OAUTH',
      scope: 'team',
      createdBy: '69ca204b7402ef0dbf62daba',
      updatedBy: '69ca204b7402ef0dbf62daba',
      appDescription: 'Sync issues from Jira Cloud',
      appCategories: ['Storage'],
      supportsRealtime: false,
      supportsSync: true,
      supportsAgent: true,
      iconPath: '/assets/icons/connectors/jira.svg',
      config: minimalNestedStoredConfig,
      isActive: false,
      isConfigured: true,
      isAuthenticated: true,
      createdAtTimestamp: 1775223143709,
      updatedAtTimestamp: 1775414619175,
    },
  };

  it('should accept valid config response with default empty nested config (Python GET)', () => {
    expect(instanceConfigResponseSchema.safeParse(validConfig).success).to.be.true;
  });

  it('should reject null createdBy or updatedBy', () => {
    const withNullCreated = instanceConfigResponseSchema.safeParse({
      success: true,
      config: { ...validConfig.config, createdBy: null },
    });
    expect(withNullCreated.success).to.be.false;

    const withNullUpdated = instanceConfigResponseSchema.safeParse({
      success: true,
      config: { ...validConfig.config, updatedBy: null },
    });
    expect(withNullUpdated.success).to.be.false;
  });

  it('should reject null timestamps', () => {
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: {
        ...validConfig.config,
        createdAtTimestamp: null,
        updatedAtTimestamp: null,
      },
    });
    expect(result.success).to.be.false;
  });

  it('should accept config with stored connector config', () => {
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: {
        ...validConfig.config,
        config: {
          auth: { authType: 'OAUTH', connectorType: 'Jira', connectorScope: 'team' },
          sync: { selectedStrategy: 'MANUAL' },
          filters: {},
        },
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept Outlook-style auth extras and empty sync/filters', () => {
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: {
        ...validConfig.config,
        authType: 'OAUTH_ADMIN_CONSENT',
        config: {
          auth: {
            clientId: '27cca42832e78c1f4bda1dfd4352225d',
            clientSecret: '342fd312f8bf3c8971c94622e01e2423',
            tenantId: 'WKFgWHzP7fjyDcHLN6sA12RcC5myRgVJ',
            hasAdminConsent: true,
            authType: 'OAUTH_ADMIN_CONSENT',
            connectorType: 'Outlook',
            connectorScope: 'team',
          },
          sync: {},
          filters: {},
        },
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept stored scheduledConfig with only intervalMinutes and timezone', () => {
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: {
        ...validConfig.config,
        config: {
          auth: {},
          sync: {
            selectedStrategy: 'SCHEDULED',
            scheduledConfig: { intervalMinutes: 30, timezone: 'UTC' },
          },
          filters: {},
        },
      },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connector_id', () => {
    const { connector_id: _, ...rest } = validConfig.config;
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: rest,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing isActive', () => {
    const { isActive: _, ...rest } = validConfig.config;
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: rest,
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra unknown keys in config (strict)', () => {
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: { ...validConfig.config, unknownField: true },
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys inside nested stored config (strict)', () => {
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: {
        ...validConfig.config,
        config: {
          auth: { authType: 'OAUTH', connectorType: 'Jira', connectorScope: 'team' },
          sync: { selectedStrategy: 'MANUAL' },
          filters: {},
          legacyField: true,
        },
      },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing nested config object', () => {
    const { config: _nested, ...rest } = validConfig.config;
    const result = instanceConfigResponseSchema.safeParse({
      success: true,
      config: rest,
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// authConfigUpdateResponseSchema
// ============================================================================

describe('authConfigUpdateResponseSchema', () => {
  const validAuthUpdate = {
    success: true,
    config: {
      auth: { authType: 'OAUTH', connectorType: 'Jira', connectorScope: 'team' },
      credentials: null,
      oauth: null,
    },
    message: 'Authentication configuration saved successfully.',
  };

  it('should accept valid auth update response', () => {
    expect(authConfigUpdateResponseSchema.safeParse(validAuthUpdate).success).to.be.true;
  });

  it('should accept config with no credentials or oauth (both optional)', () => {
    const result = authConfigUpdateResponseSchema.safeParse({
      success: true,
      config: { auth: { authType: 'OAUTH', connectorType: 'Jira', connectorScope: 'team' } },
      message: 'Authentication configuration saved successfully.',
    });
    expect(result.success).to.be.true;
  });

  it('should accept config with sync and filters', () => {
    const result = authConfigUpdateResponseSchema.safeParse({
      success: true,
      config: {
        auth: { authType: 'OAUTH', connectorType: 'Jira', connectorScope: 'team' },
        sync: { selectedStrategy: 'MANUAL' },
        filters: { sync: { values: {} } },
        credentials: null,
        oauth: null,
      },
      message: 'ok',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing message', () => {
    const result = authConfigUpdateResponseSchema.safeParse({
      success: true,
      config: validAuthUpdate.config,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing config', () => {
    const result = authConfigUpdateResponseSchema.safeParse({
      success: true,
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = authConfigUpdateResponseSchema.safeParse({
      ...validAuthUpdate,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });

  it('should reject unknown keys inside config (strict)', () => {
    const result = authConfigUpdateResponseSchema.safeParse({
      success: true,
      config: {
        ...validAuthUpdate.config,
        unknownSection: {},
      },
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// filtersSyncConfigUpdateResponseSchema
// ============================================================================

describe('filtersSyncConfigUpdateResponseSchema', () => {
  const validFiltersSyncUpdate = {
    success: true,
    config: { sync: { selectedStrategy: 'MANUAL' } },
    message: 'Filters and sync configuration saved successfully.',
    syncFiltersChanged: false,
  };

  it('should accept valid filters-sync update response', () => {
    expect(
      filtersSyncConfigUpdateResponseSchema.safeParse(validFiltersSyncUpdate).success,
    ).to.be.true;
  });

  it('should accept syncFiltersChanged as true', () => {
    const result = filtersSyncConfigUpdateResponseSchema.safeParse({
      ...validFiltersSyncUpdate,
      syncFiltersChanged: true,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing syncFiltersChanged', () => {
    const { syncFiltersChanged: _, ...rest } = validFiltersSyncUpdate;
    const result = filtersSyncConfigUpdateResponseSchema.safeParse(rest);
    expect(result.success).to.be.false;
  });

  it('should reject non-boolean syncFiltersChanged', () => {
    const result = filtersSyncConfigUpdateResponseSchema.safeParse({
      ...validFiltersSyncUpdate,
      syncFiltersChanged: 'false',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing message', () => {
    const { message: _, ...rest } = validFiltersSyncUpdate;
    const result = filtersSyncConfigUpdateResponseSchema.safeParse(rest);
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = filtersSyncConfigUpdateResponseSchema.safeParse({
      ...validFiltersSyncUpdate,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });

  it('should reject unknown keys inside config (strict)', () => {
    const result = filtersSyncConfigUpdateResponseSchema.safeParse({
      ...validFiltersSyncUpdate,
      config: { ...validFiltersSyncUpdate.config, legacyKey: true },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// activeInactiveResponseSchema
// ============================================================================

describe('activeInactiveResponseSchema', () => {
  const validActiveInactive = {
    success: true,
    connectors: [],
  };

  it('should accept empty connectors list', () => {
    expect(activeInactiveResponseSchema.safeParse(validActiveInactive).success).to.be.true;
  });

  it('should accept connectors list with a valid instance (no config)', () => {
    const { config: _, ...instanceWithoutConfig } = validInstanceItem;
    const result = activeInactiveResponseSchema.safeParse({
      success: true,
      connectors: [instanceWithoutConfig],
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectors', () => {
    const result = activeInactiveResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = activeInactiveResponseSchema.safeParse({
      ...validActiveInactive,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// activeAgentInstancesResponseSchema
// ============================================================================

describe('activeAgentInstancesResponseSchema', () => {
  it('should accept valid response with pagination', () => {
    const result = activeAgentInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing pagination', () => {
    const result = activeAgentInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectors', () => {
    const result = activeAgentInstancesResponseSchema.safeParse({
      success: true,
      pagination: validPagination,
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = activeAgentInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// instancesResponseSchema
// ============================================================================

describe('instancesResponseSchema', () => {
  it('should accept valid response with empty list', () => {
    const result = instancesResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
    });
    expect(result.success).to.be.true;
  });

  it('should accept response with optional scopeCounts', () => {
    const result = instancesResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
      scopeCounts: validScopeCounts,
    });
    expect(result.success).to.be.true;
  });

  it('should accept valid full instance in connectors list', () => {
    const result = instancesResponseSchema.safeParse({
      success: true,
      connectors: [validInstanceItem],
      pagination: { ...validPagination, totalCount: 1, totalPages: 1 },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing pagination', () => {
    const result = instancesResponseSchema.safeParse({
      success: true,
      connectors: [],
    });
    expect(result.success).to.be.false;
  });

  it('should reject instance missing required _key', () => {
    const { _key: _, ...instanceWithout_key } = validInstanceItem;
    const result = instancesResponseSchema.safeParse({
      success: true,
      connectors: [instanceWithout_key],
      pagination: validPagination,
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// instanceDetailResponseSchema
// ============================================================================

describe('instanceDetailResponseSchema', () => {
  it('should accept valid instance detail response', () => {
    const result = instanceDetailResponseSchema.safeParse({
      success: true,
      connector: validInstanceItem,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connector', () => {
    const result = instanceDetailResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject connector with non-nullable status as number', () => {
    const result = instanceDetailResponseSchema.safeParse({
      success: true,
      connector: { ...validInstanceItem, status: 123 },
    });
    expect(result.success).to.be.false;
  });

  it('should accept connector with status as string', () => {
    const result = instanceDetailResponseSchema.safeParse({
      success: true,
      connector: { ...validInstanceItem, status: 'DELETING' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = instanceDetailResponseSchema.safeParse({
      success: true,
      connector: validInstanceItem,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// configuredResponseSchema
// ============================================================================

describe('configuredResponseSchema', () => {
  const validConfigured = {
    success: true,
    connectors: {
      connectors: [],
      pagination: validPagination,
      scopeCounts: validScopeCounts,
    },
  };

  it('should accept valid configured response', () => {
    expect(configuredResponseSchema.safeParse(validConfigured).success).to.be.true;
  });

  it('should accept with instances in the list', () => {
    const result = configuredResponseSchema.safeParse({
      success: true,
      connectors: {
        connectors: [validInstanceItem],
        pagination: { ...validPagination, totalCount: 1, totalPages: 1 },
        scopeCounts: validScopeCounts,
      },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing scopeCounts (required in configured response)', () => {
    const result = configuredResponseSchema.safeParse({
      success: true,
      connectors: {
        connectors: [],
        pagination: validPagination,
      },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing pagination', () => {
    const result = configuredResponseSchema.safeParse({
      success: true,
      connectors: { connectors: [], scopeCounts: validScopeCounts },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectors wrapper', () => {
    const result = configuredResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// registryResponseSchema
// ============================================================================

describe('registryResponseSchema', () => {
  it('should accept valid registry response with empty list', () => {
    const result = registryResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
    });
    expect(result.success).to.be.true;
  });

  it('should accept response with optional registryCountsByScope', () => {
    const result = registryResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
      registryCountsByScope: validScopeCounts,
    });
    expect(result.success).to.be.true;
  });

  it('should accept a valid full registry item in list', () => {
    const result = registryResponseSchema.safeParse({
      success: true,
      connectors: [validRegistryItem],
      pagination: { ...validPagination, totalCount: 1 },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing pagination', () => {
    const result = registryResponseSchema.safeParse({
      success: true,
      connectors: [],
    });
    expect(result.success).to.be.false;
  });

  it('should reject registry item missing type', () => {
    const { type: _, ...itemWithoutType } = validRegistryItem;
    const result = registryResponseSchema.safeParse({
      success: true,
      connectors: [itemWithoutType],
      pagination: validPagination,
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// schemaResponseSchema
// ============================================================================

describe('schemaResponseSchema', () => {
  it('should accept valid schema response', () => {
    const result = schemaResponseSchema.safeParse({
      success: true,
      schema: validConnectorConfig,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing schema', () => {
    const result = schemaResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject schema missing auth', () => {
    const { auth: _, ...configWithoutAuth } = validConnectorConfig;
    const result = schemaResponseSchema.safeParse({
      success: true,
      schema: configWithoutAuth,
    });
    expect(result.success).to.be.false;
  });

  it('should reject schema missing sync', () => {
    const { sync: _, ...configWithoutSync } = validConnectorConfig;
    const result = schemaResponseSchema.safeParse({
      success: true,
      schema: configWithoutSync,
    });
    expect(result.success).to.be.false;
  });

  it('should reject schema with auth missing supportedAuthTypes', () => {
    const result = schemaResponseSchema.safeParse({
      success: true,
      schema: {
        ...validConnectorConfig,
        auth: { ...validAuthConfig, supportedAuthTypes: undefined },
      },
    });
    expect(result.success).to.be.false;
  });

  it('should reject schema with sync missing selectedStrategy', () => {
    const result = schemaResponseSchema.safeParse({
      success: true,
      schema: {
        ...validConnectorConfig,
        sync: { ...validSyncConfig, selectedStrategy: undefined },
      },
    });
    expect(result.success).to.be.false;
  });
});
