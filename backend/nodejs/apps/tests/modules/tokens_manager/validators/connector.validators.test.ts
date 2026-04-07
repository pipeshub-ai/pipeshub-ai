import 'reflect-metadata';
import { expect } from 'chai';
import {
  createConnectorInstanceSchema,
  connectorIdParamSchema,
  connectorListSchema,
  connectorTypeParamSchema,
  updateConnectorInstanceNameSchema,
  updateConnectorInstanceAuthConfigSchema,
  updateConnectorInstanceFiltersSyncConfigSchema,
  getOAuthAuthorizationUrlSchema,
  handleOAuthCallbackSchema,
  getFilterFieldOptionsSchema,
  connectorToggleSchema,
  connectorSuccessSchema,
  connectorToggleResponseSchema,
  paginationResponseSchema,
  scopeCountsResponseSchema,
  connectorInstanceConfigResponseSchema,
  connectorAuthConfigUpdateResponseSchema,
  connectorFiltersSyncConfigUpdateResponseSchema,
  connectorNameUpdateResponseSchema,
  connectorFilterFieldOptionsResponseSchema,
  connectorOAuthAuthorizeResponseSchema,
  connectorOAuthCallbackResponseSchema,
  connectorRegistryResponseSchema,
  connectorSchemaResponseSchema,
  connectorInstancesResponseSchema,
  connectorActiveInactiveResponseSchema,
  connectorActiveAgentInstancesResponseSchema,
  connectorConfiguredResponseSchema,
  createConnectorResponseSchema,
  connectorInstanceDetailResponseSchema,
  connectorDeleteResponseSchema,
} from '../../../../src/modules/tokens_manager/validators/connector.validators';

// ============================================================================
// Test helpers
// ============================================================================

const validConnectorId = 'ebd5cb6a-a2dd-4065-af23-e70b392ffd1b';

// ============================================================================
// createConnectorInstanceSchema
// ============================================================================

describe('createConnectorInstanceSchema', () => {
  it('should accept minimal valid body (required fields only)', () => {
    const result = createConnectorInstanceSchema.safeParse({
      body: {
        connectorType: 'Jira',
        instanceName: 'My Jira',
        scope: 'team',
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept full valid body with all optional fields', () => {
    const result = createConnectorInstanceSchema.safeParse({
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
    const result = createConnectorInstanceSchema.safeParse({
      body: { connectorType: 'Slack', instanceName: 'Slack', scope: 'personal' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectorType', () => {
    const result = createConnectorInstanceSchema.safeParse({
      body: { instanceName: 'My Jira', scope: 'team' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorType', () => {
    const result = createConnectorInstanceSchema.safeParse({
      body: { connectorType: '', instanceName: 'My Jira', scope: 'team' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing instanceName', () => {
    const result = createConnectorInstanceSchema.safeParse({
      body: { connectorType: 'Jira', scope: 'team' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty instanceName', () => {
    const result = createConnectorInstanceSchema.safeParse({
      body: { connectorType: 'Jira', instanceName: '', scope: 'team' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing scope', () => {
    const result = createConnectorInstanceSchema.safeParse({
      body: { connectorType: 'Jira', instanceName: 'My Jira' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject invalid scope value', () => {
    const result = createConnectorInstanceSchema.safeParse({
      body: { connectorType: 'Jira', instanceName: 'My Jira', scope: 'global' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body entirely', () => {
    const result = createConnectorInstanceSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorIdParamSchema
// ============================================================================

describe('connectorIdParamSchema', () => {
  it('should accept a valid connectorId param', () => {
    const result = connectorIdParamSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept any non-empty string as connectorId', () => {
    const result = connectorIdParamSchema.safeParse({
      params: { connectorId: 'any-string-id' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectorId', () => {
    const result = connectorIdParamSchema.safeParse({ params: {} });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId', () => {
    const result = connectorIdParamSchema.safeParse({
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing params', () => {
    const result = connectorIdParamSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorListSchema
// ============================================================================

describe('connectorListSchema', () => {
  it('should accept empty query (all optional)', () => {
    const result = connectorListSchema.safeParse({ query: {} });
    expect(result.success).to.be.true;
  });

  it('should accept valid scope "team"', () => {
    const result = connectorListSchema.safeParse({ query: { scope: 'team' } });
    expect(result.success).to.be.true;
  });

  it('should accept valid scope "personal"', () => {
    const result = connectorListSchema.safeParse({ query: { scope: 'personal' } });
    expect(result.success).to.be.true;
  });

  it('should reject invalid scope', () => {
    const result = connectorListSchema.safeParse({ query: { scope: 'org' } });
    expect(result.success).to.be.false;
  });

  it('should accept valid numeric page and limit as strings (preprocess)', () => {
    const result = connectorListSchema.safeParse({
      query: { page: '2', limit: '50' },
    });
    expect(result.success).to.be.true;
  });

  it('should accept valid numeric page and limit as numbers', () => {
    const result = connectorListSchema.safeParse({
      query: { page: 3, limit: 100 },
    });
    expect(result.success).to.be.true;
  });

  it('should reject page less than 1', () => {
    const result = connectorListSchema.safeParse({ query: { page: '0' } });
    expect(result.success).to.be.false;
  });

  it('should reject limit greater than 200', () => {
    const result = connectorListSchema.safeParse({ query: { limit: '201' } });
    expect(result.success).to.be.false;
  });

  it('should reject limit less than 1', () => {
    const result = connectorListSchema.safeParse({ query: { limit: '0' } });
    expect(result.success).to.be.false;
  });

  it('should accept a search string', () => {
    const result = connectorListSchema.safeParse({ query: { search: 'jira' } });
    expect(result.success).to.be.true;
  });

  it('should treat empty string page as undefined (no error)', () => {
    const result = connectorListSchema.safeParse({ query: { page: '' } });
    expect(result.success).to.be.true;
  });

  it('should treat empty string limit as undefined (no error)', () => {
    const result = connectorListSchema.safeParse({ query: { limit: '' } });
    expect(result.success).to.be.true;
  });

  it('should reject missing query entirely', () => {
    const result = connectorListSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorTypeParamSchema
// ============================================================================

describe('connectorTypeParamSchema', () => {
  it('should accept a valid connectorType param', () => {
    const result = connectorTypeParamSchema.safeParse({
      params: { connectorType: 'Jira' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject empty connectorType', () => {
    const result = connectorTypeParamSchema.safeParse({
      params: { connectorType: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorType', () => {
    const result = connectorTypeParamSchema.safeParse({ params: {} });
    expect(result.success).to.be.false;
  });

  it('should reject missing params', () => {
    const result = connectorTypeParamSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// updateConnectorInstanceNameSchema
// ============================================================================

describe('updateConnectorInstanceNameSchema', () => {
  it('should accept valid body and params', () => {
    const result = updateConnectorInstanceNameSchema.safeParse({
      body: { instanceName: 'New Name' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing instanceName', () => {
    const result = updateConnectorInstanceNameSchema.safeParse({
      body: {},
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty instanceName', () => {
    const result = updateConnectorInstanceNameSchema.safeParse({
      body: { instanceName: '' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId param', () => {
    const result = updateConnectorInstanceNameSchema.safeParse({
      body: { instanceName: 'New Name' },
      params: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId param', () => {
    const result = updateConnectorInstanceNameSchema.safeParse({
      body: { instanceName: 'New Name' },
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body', () => {
    const result = updateConnectorInstanceNameSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// updateConnectorInstanceAuthConfigSchema
// ============================================================================

describe('updateConnectorInstanceAuthConfigSchema', () => {
  it('should accept valid body with auth object', () => {
    const result = updateConnectorInstanceAuthConfigSchema.safeParse({
      body: { auth: { oauthConfigId: 'some-id', authType: 'OAUTH' } },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept body with optional baseUrl', () => {
    const result = updateConnectorInstanceAuthConfigSchema.safeParse({
      body: {
        auth: { clientId: 'id', clientSecret: 'secret' },
        baseUrl: 'https://app.example.com',
      },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept auth as empty object (z.any() allows it)', () => {
    const result = updateConnectorInstanceAuthConfigSchema.safeParse({
      body: { auth: {} },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing auth field', () => {
    const result = updateConnectorInstanceAuthConfigSchema.safeParse({
      body: {},
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId param', () => {
    const result = updateConnectorInstanceAuthConfigSchema.safeParse({
      body: { auth: {} },
      params: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId param', () => {
    const result = updateConnectorInstanceAuthConfigSchema.safeParse({
      body: { auth: {} },
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body', () => {
    const result = updateConnectorInstanceAuthConfigSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// updateConnectorInstanceFiltersSyncConfigSchema
// ============================================================================

describe('updateConnectorInstanceFiltersSyncConfigSchema', () => {
  it('should accept empty body (both sync and filters are optional)', () => {
    const result = updateConnectorInstanceFiltersSyncConfigSchema.safeParse({
      body: {},
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept body with only sync', () => {
    const result = updateConnectorInstanceFiltersSyncConfigSchema.safeParse({
      body: { sync: { selectedStrategy: 'MANUAL' } },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept body with only filters', () => {
    const result = updateConnectorInstanceFiltersSyncConfigSchema.safeParse({
      body: { filters: { sync: { values: {} } } },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept body with both sync and filters', () => {
    const result = updateConnectorInstanceFiltersSyncConfigSchema.safeParse({
      body: {
        sync: { selectedStrategy: 'SCHEDULED' },
        filters: { indexing: { values: {} } },
      },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectorId', () => {
    const result = updateConnectorInstanceFiltersSyncConfigSchema.safeParse({
      body: {},
      params: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId', () => {
    const result = updateConnectorInstanceFiltersSyncConfigSchema.safeParse({
      body: {},
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body', () => {
    const result = updateConnectorInstanceFiltersSyncConfigSchema.safeParse({
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
// connectorToggleSchema
// ============================================================================

describe('connectorToggleSchema', () => {
  it('should accept type "sync" with valid connectorId', () => {
    const result = connectorToggleSchema.safeParse({
      body: { type: 'sync' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept type "agent" with valid connectorId', () => {
    const result = connectorToggleSchema.safeParse({
      body: { type: 'agent' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept type "sync" with optional fullSync true', () => {
    const result = connectorToggleSchema.safeParse({
      body: { type: 'sync', fullSync: true },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should accept type "sync" with optional fullSync false', () => {
    const result = connectorToggleSchema.safeParse({
      body: { type: 'sync', fullSync: false },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.true;
  });

  it('should reject invalid type value', () => {
    const result = connectorToggleSchema.safeParse({
      body: { type: 'realtime' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing type field', () => {
    const result = connectorToggleSchema.safeParse({
      body: {},
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject non-boolean fullSync', () => {
    const result = connectorToggleSchema.safeParse({
      body: { type: 'sync', fullSync: 'yes' },
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId', () => {
    const result = connectorToggleSchema.safeParse({
      body: { type: 'sync' },
      params: {},
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty connectorId', () => {
    const result = connectorToggleSchema.safeParse({
      body: { type: 'sync' },
      params: { connectorId: '' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing body', () => {
    const result = connectorToggleSchema.safeParse({
      params: { connectorId: validConnectorId },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing params', () => {
    const result = connectorToggleSchema.safeParse({
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
// connectorSuccessSchema
// ============================================================================

describe('connectorSuccessSchema', () => {
  it('should accept { success: true }', () => {
    expect(connectorSuccessSchema.safeParse({ success: true }).success).to.be.true;
  });

  it('should accept { success: false }', () => {
    expect(connectorSuccessSchema.safeParse({ success: false }).success).to.be.true;
  });

  it('should reject missing success field', () => {
    expect(connectorSuccessSchema.safeParse({}).success).to.be.false;
  });

  it('should reject non-boolean success', () => {
    expect(connectorSuccessSchema.safeParse({ success: 'true' }).success).to.be.false;
  });
});

// ============================================================================
// connectorToggleResponseSchema
// ============================================================================

describe('connectorToggleResponseSchema', () => {
  it('should accept valid response', () => {
    const result = connectorToggleResponseSchema.safeParse({
      success: true,
      message: 'Connector toggled successfully.',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing message', () => {
    const result = connectorToggleResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject unknown extra keys (strict)', () => {
    const result = connectorToggleResponseSchema.safeParse({
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
// connectorOAuthAuthorizeResponseSchema
// ============================================================================

describe('connectorOAuthAuthorizeResponseSchema', () => {
  it('should accept valid OAuth authorize response', () => {
    const result = connectorOAuthAuthorizeResponseSchema.safeParse({
      success: true,
      authorizationUrl: 'https://accounts.google.com/o/oauth2/auth?...',
      state: 'random-state-token',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing authorizationUrl', () => {
    const result = connectorOAuthAuthorizeResponseSchema.safeParse({
      success: true,
      state: 'state-token',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing state', () => {
    const result = connectorOAuthAuthorizeResponseSchema.safeParse({
      success: true,
      authorizationUrl: 'https://accounts.google.com/...',
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra unknown keys (strict)', () => {
    const result = connectorOAuthAuthorizeResponseSchema.safeParse({
      success: true,
      authorizationUrl: 'https://...',
      state: 'state',
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorOAuthCallbackResponseSchema
// ============================================================================

describe('connectorOAuthCallbackResponseSchema', () => {
  it('should accept valid OAuth callback response', () => {
    const result = connectorOAuthCallbackResponseSchema.safeParse({
      success: true,
      redirect_url: 'https://app.example.com/connectors?status=success',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing redirect_url', () => {
    const result = connectorOAuthCallbackResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject extra unknown keys (strict)', () => {
    const result = connectorOAuthCallbackResponseSchema.safeParse({
      success: true,
      redirect_url: 'https://...',
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorNameUpdateResponseSchema
// ============================================================================

describe('connectorNameUpdateResponseSchema', () => {
  it('should accept valid name update response', () => {
    const result = connectorNameUpdateResponseSchema.safeParse({
      success: true,
      connector: { _key: 'some-uuid', name: 'New Name' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connector field', () => {
    const result = connectorNameUpdateResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject missing _key in connector', () => {
    const result = connectorNameUpdateResponseSchema.safeParse({
      success: true,
      connector: { name: 'New Name' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing name in connector', () => {
    const result = connectorNameUpdateResponseSchema.safeParse({
      success: true,
      connector: { _key: 'some-uuid' },
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra keys in connector (strict)', () => {
    const result = connectorNameUpdateResponseSchema.safeParse({
      success: true,
      connector: { _key: 'some-uuid', name: 'New Name', extra: true },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorDeleteResponseSchema
// ============================================================================

describe('connectorDeleteResponseSchema', () => {
  it('should accept valid delete response', () => {
    const result = connectorDeleteResponseSchema.safeParse({
      success: true,
      message: 'Connector deletion initiated',
      connectorId: 'ebd5cb6a-a2dd-4065-af23-e70b392ffd1b',
      status: 'DELETING',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing message', () => {
    const result = connectorDeleteResponseSchema.safeParse({
      success: true,
      connectorId: 'some-id',
      status: 'DELETING',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId', () => {
    const result = connectorDeleteResponseSchema.safeParse({
      success: true,
      message: 'Connector deletion initiated',
      status: 'DELETING',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing status', () => {
    const result = connectorDeleteResponseSchema.safeParse({
      success: true,
      message: 'Connector deletion initiated',
      connectorId: 'some-id',
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra unknown keys (strict)', () => {
    const result = connectorDeleteResponseSchema.safeParse({
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
// connectorFilterFieldOptionsResponseSchema
// ============================================================================

describe('connectorFilterFieldOptionsResponseSchema', () => {
  const validOptions = [{ id: 'PST', label: 'Pipeshub Security Test' }];

  it('should accept valid response with no cursor or message', () => {
    const result = connectorFilterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: validOptions,
      page: 1,
      limit: 50,
      hasMore: false,
    });
    expect(result.success).to.be.true;
  });

  it('should accept response with optional cursor', () => {
    const result = connectorFilterFieldOptionsResponseSchema.safeParse({
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
    const result = connectorFilterFieldOptionsResponseSchema.safeParse({
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
    const result = connectorFilterFieldOptionsResponseSchema.safeParse({
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
    const result = connectorFilterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: [],
      page: 1,
      limit: 50,
      hasMore: false,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing options', () => {
    const result = connectorFilterFieldOptionsResponseSchema.safeParse({
      success: true,
      page: 1,
      limit: 50,
      hasMore: false,
    });
    expect(result.success).to.be.false;
  });

  it('should reject option missing id', () => {
    const result = connectorFilterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: [{ label: 'Only Label' }],
      page: 1,
      limit: 50,
      hasMore: false,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing hasMore', () => {
    const result = connectorFilterFieldOptionsResponseSchema.safeParse({
      success: true,
      options: [],
      page: 1,
      limit: 50,
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// createConnectorResponseSchema
// ============================================================================

describe('createConnectorResponseSchema', () => {
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
    const result = createConnectorResponseSchema.safeParse({
      success: true,
      connector: validConnector,
      message: 'Connector instance created successfully.',
    });
    expect(result.success).to.be.true;
  });

  it('should accept scope "personal"', () => {
    const result = createConnectorResponseSchema.safeParse({
      success: true,
      connector: { ...validConnector, scope: 'personal' },
      message: 'Connector instance created successfully.',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing message', () => {
    const result = createConnectorResponseSchema.safeParse({
      success: true,
      connector: validConnector,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connector', () => {
    const result = createConnectorResponseSchema.safeParse({
      success: true,
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectorId in connector', () => {
    const { connectorId: _, ...rest } = validConnector;
    const result = createConnectorResponseSchema.safeParse({
      success: true,
      connector: rest,
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject invalid scope in connector', () => {
    const result = createConnectorResponseSchema.safeParse({
      success: true,
      connector: { ...validConnector, scope: 'global' },
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra keys in connector (strict)', () => {
    const result = createConnectorResponseSchema.safeParse({
      success: true,
      connector: { ...validConnector, extra: 'field' },
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorInstanceConfigResponseSchema
// ============================================================================

describe('connectorInstanceConfigResponseSchema', () => {
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
    expect(connectorInstanceConfigResponseSchema.safeParse(validConfig).success).to.be.true;
  });

  it('should accept config with null createdBy and updatedBy', () => {
    const result = connectorInstanceConfigResponseSchema.safeParse({
      success: true,
      config: {
        ...validConfig.config,
        createdBy: null,
        updatedBy: null,
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept config with null timestamps', () => {
    const result = connectorInstanceConfigResponseSchema.safeParse({
      success: true,
      config: {
        ...validConfig.config,
        createdAtTimestamp: null,
        updatedAtTimestamp: null,
      },
    });
    expect(result.success).to.be.true;
  });

  it('should accept config with stored connector config', () => {
    const result = connectorInstanceConfigResponseSchema.safeParse({
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
    const result = connectorInstanceConfigResponseSchema.safeParse({
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
    const result = connectorInstanceConfigResponseSchema.safeParse({
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
    const result = connectorInstanceConfigResponseSchema.safeParse({
      success: true,
      config: rest,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing isActive', () => {
    const { isActive: _, ...rest } = validConfig.config;
    const result = connectorInstanceConfigResponseSchema.safeParse({
      success: true,
      config: rest,
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra unknown keys in config (strict)', () => {
    const result = connectorInstanceConfigResponseSchema.safeParse({
      success: true,
      config: { ...validConfig.config, unknownField: true },
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys inside nested stored config (strict)', () => {
    const result = connectorInstanceConfigResponseSchema.safeParse({
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
    const result = connectorInstanceConfigResponseSchema.safeParse({
      success: true,
      config: rest,
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorAuthConfigUpdateResponseSchema
// ============================================================================

describe('connectorAuthConfigUpdateResponseSchema', () => {
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
    expect(connectorAuthConfigUpdateResponseSchema.safeParse(validAuthUpdate).success).to.be.true;
  });

  it('should accept config with no credentials or oauth (both optional)', () => {
    const result = connectorAuthConfigUpdateResponseSchema.safeParse({
      success: true,
      config: { auth: { authType: 'OAUTH', connectorType: 'Jira', connectorScope: 'team' } },
      message: 'Authentication configuration saved successfully.',
    });
    expect(result.success).to.be.true;
  });

  it('should accept config with sync and filters', () => {
    const result = connectorAuthConfigUpdateResponseSchema.safeParse({
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
    const result = connectorAuthConfigUpdateResponseSchema.safeParse({
      success: true,
      config: validAuthUpdate.config,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing config', () => {
    const result = connectorAuthConfigUpdateResponseSchema.safeParse({
      success: true,
      message: 'ok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = connectorAuthConfigUpdateResponseSchema.safeParse({
      ...validAuthUpdate,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });

  it('should reject unknown keys inside config (strict)', () => {
    const result = connectorAuthConfigUpdateResponseSchema.safeParse({
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
// connectorFiltersSyncConfigUpdateResponseSchema
// ============================================================================

describe('connectorFiltersSyncConfigUpdateResponseSchema', () => {
  const validFiltersSyncUpdate = {
    success: true,
    config: { sync: { selectedStrategy: 'MANUAL' } },
    message: 'Filters and sync configuration saved successfully.',
    syncFiltersChanged: false,
  };

  it('should accept valid filters-sync update response', () => {
    expect(
      connectorFiltersSyncConfigUpdateResponseSchema.safeParse(validFiltersSyncUpdate).success,
    ).to.be.true;
  });

  it('should accept syncFiltersChanged as true', () => {
    const result = connectorFiltersSyncConfigUpdateResponseSchema.safeParse({
      ...validFiltersSyncUpdate,
      syncFiltersChanged: true,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing syncFiltersChanged', () => {
    const { syncFiltersChanged: _, ...rest } = validFiltersSyncUpdate;
    const result = connectorFiltersSyncConfigUpdateResponseSchema.safeParse(rest);
    expect(result.success).to.be.false;
  });

  it('should reject non-boolean syncFiltersChanged', () => {
    const result = connectorFiltersSyncConfigUpdateResponseSchema.safeParse({
      ...validFiltersSyncUpdate,
      syncFiltersChanged: 'false',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing message', () => {
    const { message: _, ...rest } = validFiltersSyncUpdate;
    const result = connectorFiltersSyncConfigUpdateResponseSchema.safeParse(rest);
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = connectorFiltersSyncConfigUpdateResponseSchema.safeParse({
      ...validFiltersSyncUpdate,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });

  it('should reject unknown keys inside config (strict)', () => {
    const result = connectorFiltersSyncConfigUpdateResponseSchema.safeParse({
      ...validFiltersSyncUpdate,
      config: { ...validFiltersSyncUpdate.config, legacyKey: true },
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorActiveInactiveResponseSchema
// ============================================================================

describe('connectorActiveInactiveResponseSchema', () => {
  const validActiveInactive = {
    success: true,
    connectors: [],
  };

  it('should accept empty connectors list', () => {
    expect(connectorActiveInactiveResponseSchema.safeParse(validActiveInactive).success).to.be.true;
  });

  it('should accept connectors list with a valid instance (no config)', () => {
    const { config: _, ...instanceWithoutConfig } = validInstanceItem;
    const result = connectorActiveInactiveResponseSchema.safeParse({
      success: true,
      connectors: [instanceWithoutConfig],
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connectors', () => {
    const result = connectorActiveInactiveResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = connectorActiveInactiveResponseSchema.safeParse({
      ...validActiveInactive,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorActiveAgentInstancesResponseSchema
// ============================================================================

describe('connectorActiveAgentInstancesResponseSchema', () => {
  it('should accept valid response with pagination', () => {
    const result = connectorActiveAgentInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing pagination', () => {
    const result = connectorActiveAgentInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectors', () => {
    const result = connectorActiveAgentInstancesResponseSchema.safeParse({
      success: true,
      pagination: validPagination,
    });
    expect(result.success).to.be.false;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = connectorActiveAgentInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorInstancesResponseSchema
// ============================================================================

describe('connectorInstancesResponseSchema', () => {
  it('should accept valid response with empty list', () => {
    const result = connectorInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
    });
    expect(result.success).to.be.true;
  });

  it('should accept response with optional scopeCounts', () => {
    const result = connectorInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
      scopeCounts: validScopeCounts,
    });
    expect(result.success).to.be.true;
  });

  it('should accept valid full instance in connectors list', () => {
    const result = connectorInstancesResponseSchema.safeParse({
      success: true,
      connectors: [validInstanceItem],
      pagination: { ...validPagination, totalCount: 1, totalPages: 1 },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing pagination', () => {
    const result = connectorInstancesResponseSchema.safeParse({
      success: true,
      connectors: [],
    });
    expect(result.success).to.be.false;
  });

  it('should reject instance missing required _key', () => {
    const { _key: _, ...instanceWithout_key } = validInstanceItem;
    const result = connectorInstancesResponseSchema.safeParse({
      success: true,
      connectors: [instanceWithout_key],
      pagination: validPagination,
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorInstanceDetailResponseSchema
// ============================================================================

describe('connectorInstanceDetailResponseSchema', () => {
  it('should accept valid instance detail response', () => {
    const result = connectorInstanceDetailResponseSchema.safeParse({
      success: true,
      connector: validInstanceItem,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing connector', () => {
    const result = connectorInstanceDetailResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject connector with non-nullable status as number', () => {
    const result = connectorInstanceDetailResponseSchema.safeParse({
      success: true,
      connector: { ...validInstanceItem, status: 123 },
    });
    expect(result.success).to.be.false;
  });

  it('should accept connector with status as string', () => {
    const result = connectorInstanceDetailResponseSchema.safeParse({
      success: true,
      connector: { ...validInstanceItem, status: 'DELETING' },
    });
    expect(result.success).to.be.true;
  });

  it('should reject extra top-level keys (strict)', () => {
    const result = connectorInstanceDetailResponseSchema.safeParse({
      success: true,
      connector: validInstanceItem,
      extra: 'field',
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorConfiguredResponseSchema
// ============================================================================

describe('connectorConfiguredResponseSchema', () => {
  const validConfigured = {
    success: true,
    connectors: {
      connectors: [],
      pagination: validPagination,
      scopeCounts: validScopeCounts,
    },
  };

  it('should accept valid configured response', () => {
    expect(connectorConfiguredResponseSchema.safeParse(validConfigured).success).to.be.true;
  });

  it('should accept with instances in the list', () => {
    const result = connectorConfiguredResponseSchema.safeParse({
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
    const result = connectorConfiguredResponseSchema.safeParse({
      success: true,
      connectors: {
        connectors: [],
        pagination: validPagination,
      },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing pagination', () => {
    const result = connectorConfiguredResponseSchema.safeParse({
      success: true,
      connectors: { connectors: [], scopeCounts: validScopeCounts },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing connectors wrapper', () => {
    const result = connectorConfiguredResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorRegistryResponseSchema
// ============================================================================

describe('connectorRegistryResponseSchema', () => {
  it('should accept valid registry response with empty list', () => {
    const result = connectorRegistryResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
    });
    expect(result.success).to.be.true;
  });

  it('should accept response with optional registryCountsByScope', () => {
    const result = connectorRegistryResponseSchema.safeParse({
      success: true,
      connectors: [],
      pagination: validPagination,
      registryCountsByScope: validScopeCounts,
    });
    expect(result.success).to.be.true;
  });

  it('should accept a valid full registry item in list', () => {
    const result = connectorRegistryResponseSchema.safeParse({
      success: true,
      connectors: [validRegistryItem],
      pagination: { ...validPagination, totalCount: 1 },
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing pagination', () => {
    const result = connectorRegistryResponseSchema.safeParse({
      success: true,
      connectors: [],
    });
    expect(result.success).to.be.false;
  });

  it('should reject registry item missing type', () => {
    const { type: _, ...itemWithoutType } = validRegistryItem;
    const result = connectorRegistryResponseSchema.safeParse({
      success: true,
      connectors: [itemWithoutType],
      pagination: validPagination,
    });
    expect(result.success).to.be.false;
  });
});

// ============================================================================
// connectorSchemaResponseSchema
// ============================================================================

describe('connectorSchemaResponseSchema', () => {
  it('should accept valid schema response', () => {
    const result = connectorSchemaResponseSchema.safeParse({
      success: true,
      schema: validConnectorConfig,
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing schema', () => {
    const result = connectorSchemaResponseSchema.safeParse({ success: true });
    expect(result.success).to.be.false;
  });

  it('should reject schema missing auth', () => {
    const { auth: _, ...configWithoutAuth } = validConnectorConfig;
    const result = connectorSchemaResponseSchema.safeParse({
      success: true,
      schema: configWithoutAuth,
    });
    expect(result.success).to.be.false;
  });

  it('should reject schema missing sync', () => {
    const { sync: _, ...configWithoutSync } = validConnectorConfig;
    const result = connectorSchemaResponseSchema.safeParse({
      success: true,
      schema: configWithoutSync,
    });
    expect(result.success).to.be.false;
  });

  it('should reject schema with auth missing supportedAuthTypes', () => {
    const result = connectorSchemaResponseSchema.safeParse({
      success: true,
      schema: {
        ...validConnectorConfig,
        auth: { ...validAuthConfig, supportedAuthTypes: undefined },
      },
    });
    expect(result.success).to.be.false;
  });

  it('should reject schema with sync missing selectedStrategy', () => {
    const result = connectorSchemaResponseSchema.safeParse({
      success: true,
      schema: {
        ...validConnectorConfig,
        sync: { ...validSyncConfig, selectedStrategy: undefined },
      },
    });
    expect(result.success).to.be.false;
  });
});
