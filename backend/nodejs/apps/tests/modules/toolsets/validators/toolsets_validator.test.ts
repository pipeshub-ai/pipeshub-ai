import 'reflect-metadata'
import { expect } from 'chai'
import {
  // Request schemas
  toolsetListSchema,
  toolsetTypeParamSchema,
  toolsetInstanceIdParamSchema,
  deleteToolsetOAuthConfigParamsSchema,
  updateToolsetOAuthConfigSchema,
  createToolsetInstanceSchema,
  getInstanceOAuthAuthorizationUrlSchema,
  handleOAuthCallbackSchema,
  updateUserToolsetInstanceSchema,
  updateAgentToolsetInstanceSchema,
  authenticateToolsetInstanceSchema,
  updateToolsetInstanceSchema,
  getMyToolsetsSchema,
  getToolsetInstancesSchema,
  getAgentToolsetsSchema,
  removeAgentToolsetCredentialsSchema,
  reauthenticateAgentToolsetSchema,
  getAgentToolsetOAuthUrlSchema,
  authenticateAgentToolsetSchema,
  // Response schemas
  toolsetListResponseSchema,
  getToolsetInstancesResponseSchema,
  oauthCallbackResponseSchema,
  createToolsetInstanceResponseSchema,
  updateToolsetInstanceResponseSchema,
  deleteToolsetInstanceResponseSchema,
  getMyToolsetsResponseSchema,
  getToolsetInstanceResponseSchema,
  getToolsetSchemaResponseSchema,
  authenticateToolsetInstanceResponseSchema,
  updateUserToolsetCredentialsResponseSchema,
  removeToolsetCredentialsResponseSchema,
  reauthenticateToolsetInstanceResponseSchema,
  getInstanceOAuthAuthorizationUrlResponseSchema,
  getInstanceStatusResponseSchema,
  listToolsetOAuthConfigsResponseSchema,
  updateToolsetOAuthConfigResponseSchema,
  deleteToolsetOAuthConfigResponseSchema,
  authenticateAgentToolsetResponseSchema,
  getAgentToolsetsResponseSchema,
  updateAgentToolsetCredentialsResponseSchema,
  removeAgentToolsetCredentialsResponseSchema,
  reauthenticateAgentToolsetResponseSchema,
  getAgentToolsetOAuthUrlResponseSchema,
} from '../../../../src/modules/toolsets/validators/toolsets_validator'

// ============================================================================
// Shared fixtures
// ============================================================================

const validStoredInstance = {
  _id: 'toolsetInstances/inst-1',
  instanceName: 'My Instance',
  toolsetType: 'github',
  authType: 'API_TOKEN',
  orgId: 'org-1',
  createdBy: 'user-1',
  createdAtTimestamp: 1700000000000,
  updatedAtTimestamp: 1700000000001,
}

const validMyToolsetEntry = {
  instanceId: 'inst-1',
  instanceName: 'My Instance',
  toolsetType: 'github',
  authType: 'API_TOKEN',
  displayName: 'GitHub',
  description: 'GitHub integration',
  iconPath: '/icons/github.svg',
  category: 'development',
  supportedAuthTypes: ['API_TOKEN'],
  toolCount: 3,
  tools: [{ name: 'create_issue', fullName: 'github.create_issue', description: 'Create issue' }],
  isConfigured: true,
  isAuthenticated: true,
  isFromRegistry: false,
}

const validPaginationWithFlags = {
  page: 1,
  limit: 20,
  total: 5,
  totalPages: 1,
  hasNext: false,
  hasPrev: false,
}

const validFilterCounts = { all: 5, authenticated: 3, notAuthenticated: 2 }

describe('toolsets/validators/toolsets_validator', () => {
  // ==========================================================================
  // REQUEST SCHEMAS
  // ==========================================================================

  // --------------------------------------------------------------------------
  describe('toolsetListSchema', () => {
    it('should accept empty query', () => {
      const result = toolsetListSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
    })

    it('should coerce string page to number', () => {
      const result = toolsetListSchema.safeParse({ query: { page: '2' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.page).to.equal(2)
    })

    it('should coerce string limit to number', () => {
      const result = toolsetListSchema.safeParse({ query: { limit: '50' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.limit).to.equal(50)
    })

    it('should reject empty string page', () => {
      const result = toolsetListSchema.safeParse({ query: { page: '' } })
      expect(result.success).to.be.false
    })

    it('should reject empty string limit', () => {
      const result = toolsetListSchema.safeParse({ query: { limit: '' } })
      expect(result.success).to.be.false
    })

    it('should coerce include_tools string "true" to boolean true', () => {
      const result = toolsetListSchema.safeParse({ query: { include_tools: 'true' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.include_tools).to.be.true
    })

    it('should coerce include_tools any other string to false', () => {
      const result = toolsetListSchema.safeParse({ query: { include_tools: 'false' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.include_tools).to.be.false
    })

    it('should accept a search string', () => {
      const result = toolsetListSchema.safeParse({ query: { search: 'github' } })
      expect(result.success).to.be.true
    })

    it('should reject page below 1', () => {
      const result = toolsetListSchema.safeParse({ query: { page: '0' } })
      expect(result.success).to.be.false
    })

    it('should reject limit above 200', () => {
      const result = toolsetListSchema.safeParse({ query: { limit: '201' } })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('toolsetTypeParamSchema', () => {
    it('should accept valid toolsetType', () => {
      const result = toolsetTypeParamSchema.safeParse({ params: { toolsetType: 'github' } })
      expect(result.success).to.be.true
    })

    it('should reject empty toolsetType', () => {
      const result = toolsetTypeParamSchema.safeParse({ params: { toolsetType: '' } })
      expect(result.success).to.be.false
    })

    it('should reject missing toolsetType', () => {
      const result = toolsetTypeParamSchema.safeParse({ params: {} })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('toolsetInstanceIdParamSchema', () => {
    it('should accept valid instanceId', () => {
      const result = toolsetInstanceIdParamSchema.safeParse({ params: { instanceId: 'inst-1' } })
      expect(result.success).to.be.true
    })

    it('should reject empty instanceId', () => {
      const result = toolsetInstanceIdParamSchema.safeParse({ params: { instanceId: '' } })
      expect(result.success).to.be.false
    })

    it('should reject missing instanceId', () => {
      const result = toolsetInstanceIdParamSchema.safeParse({ params: {} })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('deleteToolsetOAuthConfigParamsSchema', () => {
    it('should accept both required params', () => {
      const result = deleteToolsetOAuthConfigParamsSchema.safeParse({
        params: { toolsetType: 'github', oauthConfigId: 'oc-1' },
      })
      expect(result.success).to.be.true
    })

    it('should reject missing toolsetType', () => {
      const result = deleteToolsetOAuthConfigParamsSchema.safeParse({
        params: { oauthConfigId: 'oc-1' },
      })
      expect(result.success).to.be.false
    })

    it('should reject missing oauthConfigId', () => {
      const result = deleteToolsetOAuthConfigParamsSchema.safeParse({
        params: { toolsetType: 'github' },
      })
      expect(result.success).to.be.false
    })

    it('should reject empty toolsetType', () => {
      const result = deleteToolsetOAuthConfigParamsSchema.safeParse({
        params: { toolsetType: '', oauthConfigId: 'oc-1' },
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('updateToolsetOAuthConfigSchema', () => {
    const validBody = {
      authConfig: { clientId: 'cid', clientSecret: 'secret' },
      baseUrl: 'https://api.github.com',
    }
    const validParams = { toolsetType: 'github', oauthConfigId: 'oc-1' }

    it('should accept valid params and body', () => {
      const result = updateToolsetOAuthConfigSchema.safeParse({ params: validParams, body: validBody })
      expect(result.success).to.be.true
    })

    it('should accept authConfig with optional tenantId', () => {
      const result = updateToolsetOAuthConfigSchema.safeParse({
        params: validParams,
        body: { authConfig: { clientId: 'cid', clientSecret: 'secret', tenantId: 'tid' }, baseUrl: 'https://x.com' },
      })
      expect(result.success).to.be.true
    })

    it('should allow extra keys in authConfig via passthrough', () => {
      const result = updateToolsetOAuthConfigSchema.safeParse({
        params: validParams,
        body: { authConfig: { clientId: 'cid', clientSecret: 'secret', extraKey: 'val' }, baseUrl: 'https://x.com' },
      })
      expect(result.success).to.be.true
    })

    it('should reject missing clientId', () => {
      const result = updateToolsetOAuthConfigSchema.safeParse({
        params: validParams,
        body: { authConfig: { clientSecret: 'secret' }, baseUrl: 'https://x.com' },
      })
      expect(result.success).to.be.false
    })

    it('should reject empty clientSecret', () => {
      const result = updateToolsetOAuthConfigSchema.safeParse({
        params: validParams,
        body: { authConfig: { clientId: 'cid', clientSecret: '' }, baseUrl: 'https://x.com' },
      })
      expect(result.success).to.be.false
    })

    it('should reject empty baseUrl', () => {
      const result = updateToolsetOAuthConfigSchema.safeParse({
        params: validParams,
        body: { authConfig: { clientId: 'cid', clientSecret: 'secret' }, baseUrl: '' },
      })
      expect(result.success).to.be.false
    })

    it('should reject missing toolsetType in params', () => {
      const result = updateToolsetOAuthConfigSchema.safeParse({
        params: { oauthConfigId: 'oc-1' },
        body: validBody,
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('createToolsetInstanceSchema', () => {
    const validBody = { instanceName: 'My Tool', toolsetType: 'github', authType: 'API_TOKEN' }

    it('should accept all required fields', () => {
      const result = createToolsetInstanceSchema.safeParse({ body: validBody })
      expect(result.success).to.be.true
    })

    it('should accept all optional fields', () => {
      const result = createToolsetInstanceSchema.safeParse({
        body: {
          ...validBody,
          authConfig: { apiToken: 'tok' },
          baseUrl: 'https://api.github.com',
          oauthConfigId: 'oc-1',
          oauthInstanceName: 'GitHub OAuth',
        },
      })
      expect(result.success).to.be.true
    })

    it('should reject missing instanceName', () => {
      const result = createToolsetInstanceSchema.safeParse({
        body: { toolsetType: 'github', authType: 'API_TOKEN' },
      })
      expect(result.success).to.be.false
    })

    it('should reject empty toolsetType', () => {
      const result = createToolsetInstanceSchema.safeParse({
        body: { instanceName: 'My Tool', toolsetType: '', authType: 'API_TOKEN' },
      })
      expect(result.success).to.be.false
    })

    it('should reject missing authType', () => {
      const result = createToolsetInstanceSchema.safeParse({
        body: { instanceName: 'My Tool', toolsetType: 'github' },
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getInstanceOAuthAuthorizationUrlSchema', () => {
    it('should accept instanceId param', () => {
      const result = getInstanceOAuthAuthorizationUrlSchema.safeParse({
        params: { instanceId: 'inst-1' },
        query: {},
      })
      expect(result.success).to.be.true
    })

    it('should accept optional base_url query', () => {
      const result = getInstanceOAuthAuthorizationUrlSchema.safeParse({
        params: { instanceId: 'inst-1' },
        query: { base_url: 'https://app.example.com' },
      })
      expect(result.success).to.be.true
    })

    it('should reject empty instanceId', () => {
      const result = getInstanceOAuthAuthorizationUrlSchema.safeParse({
        params: { instanceId: '' },
        query: {},
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('handleOAuthCallbackSchema', () => {
    it('should accept empty query', () => {
      const result = handleOAuthCallbackSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
    })

    it('should accept all optional query fields', () => {
      const result = handleOAuthCallbackSchema.safeParse({
        query: { code: 'auth-code', state: 'state-1', error: 'access_denied', base_url: 'https://x.com' },
      })
      expect(result.success).to.be.true
    })

    it('should accept code only', () => {
      const result = handleOAuthCallbackSchema.safeParse({ query: { code: 'auth-code' } })
      expect(result.success).to.be.true
    })

    it('should accept error only', () => {
      const result = handleOAuthCallbackSchema.safeParse({ query: { error: 'access_denied' } })
      expect(result.success).to.be.true
    })
  })

  // --------------------------------------------------------------------------
  describe('updateUserToolsetInstanceSchema', () => {
    it('should accept instanceId with empty auth', () => {
      const result = updateUserToolsetInstanceSchema.safeParse({
        body: { auth: {} },
        params: { instanceId: 'inst-1' },
      })
      expect(result.success).to.be.true
    })

    it('should accept all auth fields', () => {
      const result = updateUserToolsetInstanceSchema.safeParse({
        body: { auth: { apiToken: 'tok', username: 'user', password: 'pass' } },
        params: { instanceId: 'inst-1' },
      })
      expect(result.success).to.be.true
    })

    it('should reject empty instanceId', () => {
      const result = updateUserToolsetInstanceSchema.safeParse({
        body: { auth: {} },
        params: { instanceId: '' },
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('updateAgentToolsetInstanceSchema', () => {
    it('should accept agentKey and instanceId with auth', () => {
      const result = updateAgentToolsetInstanceSchema.safeParse({
        body: { auth: { apiToken: 'tok' } },
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
      })
      expect(result.success).to.be.true
    })

    it('should reject missing agentKey', () => {
      const result = updateAgentToolsetInstanceSchema.safeParse({
        body: { auth: {} },
        params: { instanceId: 'inst-1' },
      })
      expect(result.success).to.be.false
    })

    it('should reject empty instanceId', () => {
      const result = updateAgentToolsetInstanceSchema.safeParse({
        body: { auth: {} },
        params: { agentKey: 'agent-1', instanceId: '' },
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('authenticateToolsetInstanceSchema', () => {
    it('should accept instanceId with empty auth', () => {
      const result = authenticateToolsetInstanceSchema.safeParse({
        params: { instanceId: 'inst-1' },
        body: { auth: {} },
      })
      expect(result.success).to.be.true
    })

    it('should accept all auth fields', () => {
      const result = authenticateToolsetInstanceSchema.safeParse({
        params: { instanceId: 'inst-1' },
        body: { auth: { apiToken: 'tok', username: 'u', password: 'p', bearerToken: 'bt' } },
      })
      expect(result.success).to.be.true
    })

    it('should strip unknown extra auth fields (no passthrough)', () => {
      const result = authenticateToolsetInstanceSchema.safeParse({
        params: { instanceId: 'inst-1' },
        body: { auth: { apiToken: 'tok', customField: 'val' } },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect((result.data.body.auth as any).customField).to.be.undefined
      }
    })

    it('should reject empty instanceId', () => {
      const result = authenticateToolsetInstanceSchema.safeParse({
        params: { instanceId: '' },
        body: { auth: {} },
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('updateToolsetInstanceSchema', () => {
    it('should accept instanceId with empty body', () => {
      const result = updateToolsetInstanceSchema.safeParse({
        params: { instanceId: 'inst-1' },
        body: {},
      })
      expect(result.success).to.be.true
    })

    it('should accept all optional body fields', () => {
      const result = updateToolsetInstanceSchema.safeParse({
        params: { instanceId: 'inst-1' },
        body: {
          instanceName: 'Updated',
          baseUrl: 'https://example.com',
          oauthConfigId: 'oc-1',
          authConfig: { apiToken: 'tok' },
        },
      })
      expect(result.success).to.be.true
    })

    it('should reject empty instanceId', () => {
      const result = updateToolsetInstanceSchema.safeParse({
        params: { instanceId: '' },
        body: {},
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getMyToolsetsSchema', () => {
    it('should accept empty query', () => {
      const result = getMyToolsetsSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
    })

    it('should coerce string page to number', () => {
      const result = getMyToolsetsSchema.safeParse({ query: { page: '3' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.page).to.equal(3)
    })

    it('should accept authStatus "authenticated"', () => {
      const result = getMyToolsetsSchema.safeParse({ query: { authStatus: 'authenticated' } })
      expect(result.success).to.be.true
    })

    it('should accept authStatus "not-authenticated"', () => {
      const result = getMyToolsetsSchema.safeParse({ query: { authStatus: 'not-authenticated' } })
      expect(result.success).to.be.true
    })

    it('should reject invalid authStatus value', () => {
      const result = getMyToolsetsSchema.safeParse({ query: { authStatus: 'unknown' } })
      expect(result.success).to.be.false
    })

    it('should coerce includeRegistry "true" to boolean true', () => {
      const result = getMyToolsetsSchema.safeParse({ query: { includeRegistry: 'true' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.includeRegistry).to.be.true
    })

    it('should reject limit above 200', () => {
      const result = getMyToolsetsSchema.safeParse({ query: { limit: '201' } })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getToolsetInstancesSchema', () => {
    it('should accept empty query', () => {
      const result = getToolsetInstancesSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
    })

    it('should coerce string limit to number', () => {
      const result = getToolsetInstancesSchema.safeParse({ query: { limit: '10' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.limit).to.equal(10)
    })

    it('should reject empty string page', () => {
      const result = getToolsetInstancesSchema.safeParse({ query: { page: '' } })
      expect(result.success).to.be.false
    })

    it('should accept search query', () => {
      const result = getToolsetInstancesSchema.safeParse({ query: { search: 'jira' } })
      expect(result.success).to.be.true
    })

    it('should reject page below 1', () => {
      const result = getToolsetInstancesSchema.safeParse({ query: { page: '0' } })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getAgentToolsetsSchema', () => {
    it('should accept empty query', () => {
      const result = getAgentToolsetsSchema.safeParse({ query: {} })
      expect(result.success).to.be.true
    })

    it('should coerce string page to number', () => {
      const result = getAgentToolsetsSchema.safeParse({ query: { page: '1' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.page).to.equal(1)
    })

    it('should coerce includeRegistry "true" to true', () => {
      const result = getAgentToolsetsSchema.safeParse({ query: { includeRegistry: 'true' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.includeRegistry).to.be.true
    })

    it('should coerce includeRegistry other value to false', () => {
      const result = getAgentToolsetsSchema.safeParse({ query: { includeRegistry: 'yes' } })
      expect(result.success).to.be.true
      if (result.success) expect(result.data.query.includeRegistry).to.be.false
    })

    it('should reject limit above 200', () => {
      const result = getAgentToolsetsSchema.safeParse({ query: { limit: '999' } })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('removeAgentToolsetCredentialsSchema', () => {
    it('should accept valid agentKey and instanceId', () => {
      const result = removeAgentToolsetCredentialsSchema.safeParse({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
      })
      expect(result.success).to.be.true
    })

    it('should reject missing agentKey', () => {
      const result = removeAgentToolsetCredentialsSchema.safeParse({
        params: { instanceId: 'inst-1' },
      })
      expect(result.success).to.be.false
    })

    it('should reject empty instanceId', () => {
      const result = removeAgentToolsetCredentialsSchema.safeParse({
        params: { agentKey: 'agent-1', instanceId: '' },
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('reauthenticateAgentToolsetSchema', () => {
    it('should accept valid params', () => {
      const result = reauthenticateAgentToolsetSchema.safeParse({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
      })
      expect(result.success).to.be.true
    })

    it('should reject missing agentKey', () => {
      const result = reauthenticateAgentToolsetSchema.safeParse({
        params: { instanceId: 'inst-1' },
      })
      expect(result.success).to.be.false
    })

    it('should reject empty agentKey', () => {
      const result = reauthenticateAgentToolsetSchema.safeParse({
        params: { agentKey: '', instanceId: 'inst-1' },
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getAgentToolsetOAuthUrlSchema', () => {
    it('should accept both params with no query', () => {
      const result = getAgentToolsetOAuthUrlSchema.safeParse({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
        query: {},
      })
      expect(result.success).to.be.true
    })

    it('should accept optional base_url query', () => {
      const result = getAgentToolsetOAuthUrlSchema.safeParse({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
        query: { base_url: 'https://app.example.com' },
      })
      expect(result.success).to.be.true
    })

    it('should reject empty agentKey', () => {
      const result = getAgentToolsetOAuthUrlSchema.safeParse({
        params: { agentKey: '', instanceId: 'inst-1' },
        query: {},
      })
      expect(result.success).to.be.false
    })

    it('should reject missing instanceId', () => {
      const result = getAgentToolsetOAuthUrlSchema.safeParse({
        params: { agentKey: 'agent-1' },
        query: {},
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('authenticateAgentToolsetSchema', () => {
    it('should accept both params with empty auth body', () => {
      const result = authenticateAgentToolsetSchema.safeParse({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
        body: { auth: {} },
      })
      expect(result.success).to.be.true
    })

    it('should accept all auth credential fields', () => {
      const result = authenticateAgentToolsetSchema.safeParse({
        params: { agentKey: 'agent-1', instanceId: 'inst-1' },
        body: { auth: { apiToken: 'tok', bearerToken: 'bt', username: 'u', password: 'p' } },
      })
      expect(result.success).to.be.true
    })

    it('should reject empty agentKey', () => {
      const result = authenticateAgentToolsetSchema.safeParse({
        params: { agentKey: '', instanceId: 'inst-1' },
        body: { auth: {} },
      })
      expect(result.success).to.be.false
    })

    it('should reject missing instanceId', () => {
      const result = authenticateAgentToolsetSchema.safeParse({
        params: { agentKey: 'agent-1' },
        body: { auth: {} },
      })
      expect(result.success).to.be.false
    })
  })

  // ==========================================================================
  // RESPONSE SCHEMAS
  // ==========================================================================

  // --------------------------------------------------------------------------
  describe('toolsetListResponseSchema', () => {
    const validToolset = {
      name: 'github',
      displayName: 'GitHub',
      description: 'GitHub integration',
      category: 'development',
      group: 'code',
      iconPath: '/icons/github.svg',
      supportedAuthTypes: ['API_TOKEN'],
      toolCount: 2,
      tools: [
        {
          name: 'create_issue',
          fullName: 'github.create_issue',
          displayName: 'Create Issue',
          description: 'Create a GitHub issue',
          parameters: [{ name: 'title', type: 'string', description: 'Issue title' }],
          returns: 'Issue object',
          tags: ['write'],
        },
      ],
    }

    it('should accept valid response', () => {
      const result = toolsetListResponseSchema.safeParse({
        status: 'success',
        toolsets: [validToolset],
        categorizedToolsets: { development: [validToolset] },
        pagination: { page: 1, limit: 20, total: 1, totalPages: 1 },
      })
      expect(result.success).to.be.true
    })

    it('should accept empty toolsets array', () => {
      const result = toolsetListResponseSchema.safeParse({
        status: 'success',
        toolsets: [],
        categorizedToolsets: {},
        pagination: { page: 1, limit: 20, total: 0, totalPages: 0 },
      })
      expect(result.success).to.be.true
    })

    it('should reject wrong status literal', () => {
      const result = toolsetListResponseSchema.safeParse({
        status: 'error',
        toolsets: [],
        categorizedToolsets: {},
        pagination: { page: 1, limit: 20, total: 0, totalPages: 0 },
      })
      expect(result.success).to.be.false
    })

    it('should accept tool with null returns', () => {
      const result = toolsetListResponseSchema.safeParse({
        status: 'success',
        toolsets: [{ ...validToolset, tools: [{ ...validToolset.tools[0], returns: null }] }],
        categorizedToolsets: {},
        pagination: { page: 1, limit: 20, total: 1, totalPages: 1 },
      })
      expect(result.success).to.be.true
    })
  })

  // --------------------------------------------------------------------------
  describe('getToolsetInstancesResponseSchema', () => {
    const validInstance = {
      _id: 'inst-1',
      instanceName: 'My Instance',
      toolsetType: 'github',
      authType: 'API_TOKEN',
      orgId: 'org-1',
      createdBy: 'user-1',
      createdAtTimestamp: 1700000000000,
      updatedAtTimestamp: 1700000000001,
      displayName: 'GitHub',
      description: 'GitHub integration',
      iconPath: '/icons/github.svg',
      toolCount: 3,
    }

    it('should accept valid response with instances', () => {
      const result = getToolsetInstancesResponseSchema.safeParse({
        status: 'success',
        instances: [validInstance],
        pagination: validPaginationWithFlags,
      })
      expect(result.success).to.be.true
    })

    it('should accept instance with optional oauthConfigId', () => {
      const result = getToolsetInstancesResponseSchema.safeParse({
        status: 'success',
        instances: [{ ...validInstance, oauthConfigId: 'oc-1' }],
        pagination: validPaginationWithFlags,
      })
      expect(result.success).to.be.true
    })

    it('should accept instance with optional auth field for non-OAuth types', () => {
      const result = getToolsetInstancesResponseSchema.safeParse({
        status: 'success',
        instances: [{ ...validInstance, auth: { type: 'API_TOKEN' } }],
        pagination: validPaginationWithFlags,
      })
      expect(result.success).to.be.true
    })

    it('should strip unknown extra fields not in schema', () => {
      const result = getToolsetInstancesResponseSchema.safeParse({
        status: 'success',
        instances: [{ ...validInstance, extraField: 'val' }],
        pagination: validPaginationWithFlags,
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect((result.data.instances[0] as Record<string, unknown>).extraField).to.be.undefined
      }
    })

    it('should reject missing pagination', () => {
      const result = getToolsetInstancesResponseSchema.safeParse({
        status: 'success',
        instances: [],
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('oauthCallbackResponseSchema', () => {
    it('should accept success true with valid URL', () => {
      const result = oauthCallbackResponseSchema.safeParse({
        success: true,
        redirect_url: 'https://frontend.example.com/callback',
      })
      expect(result.success).to.be.true
    })

    it('should reject success: false', () => {
      const result = oauthCallbackResponseSchema.safeParse({
        success: false,
        redirect_url: 'https://example.com',
      })
      expect(result.success).to.be.false
    })

    it('should reject non-URL redirect_url', () => {
      const result = oauthCallbackResponseSchema.safeParse({
        success: true,
        redirect_url: 'not-a-url',
      })
      expect(result.success).to.be.false
    })

    it('should reject missing redirect_url', () => {
      const result = oauthCallbackResponseSchema.safeParse({ success: true })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('createToolsetInstanceResponseSchema', () => {
    it('should accept valid response', () => {
      const result = createToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: validStoredInstance,
        message: 'Instance created.',
      })
      expect(result.success).to.be.true
    })

    it('should accept instance with optional oauthConfigId', () => {
      const result = createToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: { ...validStoredInstance, oauthConfigId: 'oc-1' },
        message: 'Instance created.',
      })
      expect(result.success).to.be.true
    })

    it('should accept extra fields in instance via passthrough', () => {
      const result = createToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: { ...validStoredInstance, isActive: true },
        message: 'Instance created.',
      })
      expect(result.success).to.be.true
    })

    it('should reject missing message', () => {
      const result = createToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: validStoredInstance,
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('updateToolsetInstanceResponseSchema', () => {
    it('should accept valid response', () => {
      const result = updateToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: validStoredInstance,
        message: 'Instance updated.',
        deauthenticatedUserCount: 2,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing deauthenticatedUserCount', () => {
      const result = updateToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: validStoredInstance,
        message: 'Instance updated.',
      })
      expect(result.success).to.be.false
    })

    it('should reject wrong status', () => {
      const result = updateToolsetInstanceResponseSchema.safeParse({
        status: 'fail',
        instance: validStoredInstance,
        message: 'err',
        deauthenticatedUserCount: 0,
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('deleteToolsetInstanceResponseSchema', () => {
    it('should accept valid response', () => {
      const result = deleteToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        message: 'Instance deleted.',
        instanceId: 'inst-1',
        deletedCredentialsCount: 5,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing instanceId', () => {
      const result = deleteToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        message: 'Instance deleted.',
        deletedCredentialsCount: 0,
      })
      expect(result.success).to.be.false
    })

    it('should reject missing deletedCredentialsCount', () => {
      const result = deleteToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        message: 'Instance deleted.',
        instanceId: 'inst-1',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getMyToolsetsResponseSchema', () => {
    it('should accept valid response', () => {
      const result = getMyToolsetsResponseSchema.safeParse({
        status: 'success',
        toolsets: [validMyToolsetEntry],
        pagination: validPaginationWithFlags,
        filterCounts: validFilterCounts,
      })
      expect(result.success).to.be.true
    })

    it('should accept entry with auth: null', () => {
      const result = getMyToolsetsResponseSchema.safeParse({
        status: 'success',
        toolsets: [{ ...validMyToolsetEntry, auth: null }],
        pagination: validPaginationWithFlags,
        filterCounts: validFilterCounts,
      })
      expect(result.success).to.be.true
    })

    it('should accept entry with optional oauthConfigId: null', () => {
      const result = getMyToolsetsResponseSchema.safeParse({
        status: 'success',
        toolsets: [{ ...validMyToolsetEntry, oauthConfigId: null }],
        pagination: validPaginationWithFlags,
        filterCounts: validFilterCounts,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing filterCounts', () => {
      const result = getMyToolsetsResponseSchema.safeParse({
        status: 'success',
        toolsets: [],
        pagination: validPaginationWithFlags,
      })
      expect(result.success).to.be.false
    })

    it('should reject wrong status', () => {
      const result = getMyToolsetsResponseSchema.safeParse({
        status: 'error',
        toolsets: [],
        pagination: validPaginationWithFlags,
        filterCounts: validFilterCounts,
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getToolsetInstanceResponseSchema', () => {
    const validDetail = {
      _id: 'inst-1',
      instanceName: 'My Instance',
      toolsetType: 'github',
      authType: 'API_TOKEN',
      orgId: 'org-1',
      createdBy: 'user-1',
      createdAtTimestamp: 1700000000000,
      updatedAtTimestamp: 1700000000001,
      displayName: 'GitHub',
      description: 'GitHub integration',
      iconPath: '/icons/github.svg',
      supportedAuthTypes: ['API_TOKEN'],
      toolCount: 3,
    }

    it('should accept valid response without oauthConfig', () => {
      const result = getToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: validDetail,
      })
      expect(result.success).to.be.true
    })

    it('should accept response with oauthConfig', () => {
      const result = getToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: {
          ...validDetail,
          oauthConfig: {
            _id: 'oc-1',
            oauthInstanceName: 'GitHub OAuth',
            clientId: 'cid',
            clientSecret: 'secret',
            authorizeUrl: 'https://github.com/login/oauth/authorize',
            tokenUrl: 'https://github.com/login/oauth/access_token',
            redirectUri: 'https://app.example.com/callback',
            scopes: ['repo', 'user'],
          },
        },
      })
      expect(result.success).to.be.true
    })

    it('should accept instance with optional auth and authenticatedUserCount', () => {
      const result = getToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: { ...validDetail, auth: { apiToken: 'tok' }, authenticatedUserCount: 10 },
      })
      expect(result.success).to.be.true
    })

    it('should accept oauthConfig with provider-specific optional fields', () => {
      const result = getToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: {
          ...validDetail,
          oauthConfig: {
            _id: 'oc-1',
            oauthInstanceName: 'Azure OAuth',
            name: 'microsoft',
            clientId: 'cid',
            clientSecret: 'secret',
            authorizeUrl: 'https://login.microsoftonline.com/tenant/oauth2/authorize',
            tokenUrl: 'https://login.microsoftonline.com/tenant/oauth2/token',
            redirectUri: 'https://app.example.com/callback',
            scopes: ['openid', 'email'],
            tenantId: 'my-tenant-id',
            tokenAccessType: 'offline',
            scopeParameterName: 'scope',
            tokenResponsePath: 'access_token',
          },
        },
      })
      expect(result.success).to.be.true
    })

    it('should strip unknown extra fields not in schema', () => {
      const result = getToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        instance: { ...validDetail, isActive: true },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect((result.data.instance as Record<string, unknown>).isActive).to.be.undefined
      }
    })

    it('should reject missing instance', () => {
      const result = getToolsetInstanceResponseSchema.safeParse({ status: 'success' })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getToolsetSchemaResponseSchema', () => {
    const validAuthField = {
      name: 'apiToken',
      displayName: 'API Token',
      placeholder: 'Enter token',
      description: 'Your API token',
      fieldType: 'password',
      required: true,
      usage: 'AUTHENTICATE' as const,
      defaultValue: null,
      validation: { minLength: 1, maxLength: 500 },
      isSecret: true,
    }

    const validToolsetSchema = {
      status: 'success' as const,
      toolset: {
        name: 'github',
        displayName: 'GitHub',
        description: 'GitHub integration',
        category: 'development',
        supportedAuthTypes: ['API_TOKEN'],
        config: {
          auth: {
            schemas: {
              API_TOKEN: { fields: [validAuthField] },
            },
          },
        },
        oauthConfig: null,
        tools: [
          {
            name: 'create_issue',
            description: 'Create an issue',
            parameters: [{ name: 'title', type: 'string', description: 'Title' }],
            returns: null,
            tags: ['write'],
          },
        ],
      },
    }

    it('should accept valid schema response', () => {
      const result = getToolsetSchemaResponseSchema.safeParse(validToolsetSchema)
      expect(result.success).to.be.true
    })

    it('should accept oauthConfig: null', () => {
      const result = getToolsetSchemaResponseSchema.safeParse(validToolsetSchema)
      expect(result.success).to.be.true
    })

    it('should accept oauthConfig with metadata', () => {
      const result = getToolsetSchemaResponseSchema.safeParse({
        ...validToolsetSchema,
        toolset: {
          ...validToolsetSchema.toolset,
          oauthConfig: {
            iconPath: '/icons/github.svg',
            appGroup: 'development',
            appDescription: 'GitHub OAuth App',
            appCategories: ['vcs'],
          },
        },
      })
      expect(result.success).to.be.true
    })

    it('should accept usage enum values CONFIGURE and BOTH', () => {
      const configureField = { ...validAuthField, usage: 'CONFIGURE' as const }
      const bothField = { ...validAuthField, usage: 'BOTH' as const }
      const r1 = getToolsetSchemaResponseSchema.safeParse({
        ...validToolsetSchema,
        toolset: {
          ...validToolsetSchema.toolset,
          config: { auth: { schemas: { API_TOKEN: { fields: [configureField] } } } },
        },
      })
      const r2 = getToolsetSchemaResponseSchema.safeParse({
        ...validToolsetSchema,
        toolset: {
          ...validToolsetSchema.toolset,
          config: { auth: { schemas: { API_TOKEN: { fields: [bothField] } } } },
        },
      })
      expect(r1.success).to.be.true
      expect(r2.success).to.be.true
    })

    it('should accept auth with OAuth-specific fields and conditionalDisplay', () => {
      const result = getToolsetSchemaResponseSchema.safeParse({
        ...validToolsetSchema,
        toolset: {
          ...validToolsetSchema.toolset,
          config: {
            auth: {
              schemas: { OAUTH: { fields: [validAuthField], redirectUri: 'toolsets/oauth/callback/github', displayRedirectUri: true } },
              supportedAuthTypes: ['OAUTH'],
              displayRedirectUri: true,
              redirectUri: 'toolsets/oauth/callback/github',
              schema: { fields: [] },
              values: {},
              customFields: [],
              customValues: {},
              conditionalDisplay: {
                tenantId: { showWhen: { field: 'authType', operator: 'eq', value: 'SINGLE_TENANT' } },
              },
              authorizeUrl: 'https://github.com/login/oauth/authorize',
              tokenUrl: 'https://github.com/login/oauth/access_token',
              scopes: ['repo', 'user'],
              oauthConfigs: {
                OAUTH: {
                  authorizeUrl: 'https://github.com/login/oauth/authorize',
                  tokenUrl: 'https://github.com/login/oauth/access_token',
                  scopes: ['repo', 'user'],
                },
              },
            },
          },
        },
      })
      expect(result.success).to.be.true
    })

    it('should reject invalid usage enum value', () => {
      const badField = { ...validAuthField, usage: 'READ_ONLY' }
      const result = getToolsetSchemaResponseSchema.safeParse({
        ...validToolsetSchema,
        toolset: {
          ...validToolsetSchema.toolset,
          config: { auth: { schemas: { API_TOKEN: { fields: [badField] } } } },
        },
      })
      expect(result.success).to.be.false
    })

    it('should accept config with _oauth_configs (snake_case from asdict)', () => {
      const oauthAuthField = {
        name: 'clientId',
        display_name: 'Client ID',
        field_type: 'TEXT',
        placeholder: 'Enter your Client ID',
        description: 'The OAuth2 client ID',
        required: true,
        default_value: '',
        min_length: 1,
        max_length: 1000,
        is_secret: false,
        usage: 'BOTH' as const,
      }
      const result = getToolsetSchemaResponseSchema.safeParse({
        ...validToolsetSchema,
        toolset: {
          ...validToolsetSchema.toolset,
          config: {
            auth: { schemas: { OAUTH: { fields: [validAuthField] } } },
            _oauth_configs: {
              OAUTH: {
                connector_name: 'Jira',
                authorize_url: 'https://auth.atlassian.com/authorize',
                token_url: 'https://auth.atlassian.com/oauth/token',
                redirect_uri: 'toolsets/oauth/callback/jira',
                scopes: { personal_sync: [], team_sync: [], agent: ['read:jira-work'] },
                auth_fields: [oauthAuthField],
                token_access_type: null,
                additional_params: {},
                scope_parameter_name: 'scope',
                token_response_path: null,
                icon_path: '/assets/icons/connectors/jira.svg',
                app_group: 'Project Management',
                app_description: 'JIRA OAuth application',
                app_categories: [],
                documentation_links: [],
              },
            },
          },
        },
      })
      expect(result.success).to.be.true
    })

    it('should reject wrong status', () => {
      const result = getToolsetSchemaResponseSchema.safeParse({
        ...validToolsetSchema,
        status: 'error',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('authenticateToolsetInstanceResponseSchema', () => {
    it('should accept isAuthenticated: true', () => {
      const result = authenticateToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        message: 'Authenticated.',
        isAuthenticated: true,
      })
      expect(result.success).to.be.true
    })

    it('should accept isAuthenticated: false', () => {
      const result = authenticateToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        message: 'Failed.',
        isAuthenticated: false,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing isAuthenticated', () => {
      const result = authenticateToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        message: 'Authenticated.',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('updateUserToolsetCredentialsResponseSchema', () => {
    it('should accept valid response', () => {
      const result = updateUserToolsetCredentialsResponseSchema.safeParse({
        status: 'success',
        message: 'Credentials updated.',
      })
      expect(result.success).to.be.true
    })

    it('should reject wrong status', () => {
      const result = updateUserToolsetCredentialsResponseSchema.safeParse({
        status: 'error',
        message: 'Failed.',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('removeToolsetCredentialsResponseSchema', () => {
    it('should accept valid response', () => {
      const result = removeToolsetCredentialsResponseSchema.safeParse({
        status: 'success',
        message: 'Credentials removed.',
      })
      expect(result.success).to.be.true
    })

    it('should reject missing message', () => {
      const result = removeToolsetCredentialsResponseSchema.safeParse({ status: 'success' })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('reauthenticateToolsetInstanceResponseSchema', () => {
    it('should accept valid response', () => {
      const result = reauthenticateToolsetInstanceResponseSchema.safeParse({
        status: 'success',
        message: 'Tokens cleared.',
      })
      expect(result.success).to.be.true
    })

    it('should reject wrong status literal', () => {
      const result = reauthenticateToolsetInstanceResponseSchema.safeParse({
        status: 'ok',
        message: 'Tokens cleared.',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getInstanceOAuthAuthorizationUrlResponseSchema', () => {
    it('should accept valid response', () => {
      const result = getInstanceOAuthAuthorizationUrlResponseSchema.safeParse({
        success: true,
        authorizationUrl: 'https://github.com/login/oauth/authorize?client_id=cid',
        state: 'encoded-state-123',
      })
      expect(result.success).to.be.true
    })

    it('should reject success: false', () => {
      const result = getInstanceOAuthAuthorizationUrlResponseSchema.safeParse({
        success: false,
        authorizationUrl: 'https://github.com/login/oauth/authorize',
        state: 'state',
      })
      expect(result.success).to.be.false
    })

    it('should reject missing authorizationUrl', () => {
      const result = getInstanceOAuthAuthorizationUrlResponseSchema.safeParse({
        success: true,
        state: 'state',
      })
      expect(result.success).to.be.false
    })

    it('should reject missing state', () => {
      const result = getInstanceOAuthAuthorizationUrlResponseSchema.safeParse({
        success: true,
        authorizationUrl: 'https://github.com/login/oauth/authorize',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getInstanceStatusResponseSchema', () => {
    it('should accept valid response with all non-null values', () => {
      const result = getInstanceStatusResponseSchema.safeParse({
        status: 'success',
        instanceId: 'inst-1',
        instanceName: 'My Instance',
        toolsetType: 'github',
        authType: 'API_TOKEN',
        isConfigured: true,
        isAuthenticated: true,
      })
      expect(result.success).to.be.true
    })

    it('should accept nullable instanceName, toolsetType, authType', () => {
      const result = getInstanceStatusResponseSchema.safeParse({
        status: 'success',
        instanceId: 'inst-1',
        instanceName: null,
        toolsetType: null,
        authType: null,
        isConfigured: false,
        isAuthenticated: false,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing isConfigured', () => {
      const result = getInstanceStatusResponseSchema.safeParse({
        status: 'success',
        instanceId: 'inst-1',
        instanceName: null,
        toolsetType: null,
        authType: null,
        isAuthenticated: false,
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('listToolsetOAuthConfigsResponseSchema', () => {
    const validOAuthConfig = {
      _id: 'oc-1',
      oauthInstanceName: 'GitHub OAuth',
      toolsetType: 'github',
      userId: 'user-1',
      orgId: 'org-1',
      createdAtTimestamp: 1700000000000,
      updatedAtTimestamp: 1700000000001,
      clientId: 'cid',
      clientSecret: 'secret',
      redirectUri: 'https://app.example.com/callback',
      authorizeUrl: 'https://github.com/login/oauth/authorize',
      tokenUrl: 'https://github.com/login/oauth/access_token',
      scopes: ['repo'],
      clientSecretSet: true,
    }

    it('should accept valid response with configs', () => {
      const result = listToolsetOAuthConfigsResponseSchema.safeParse({
        status: 'success',
        oauthConfigs: [validOAuthConfig],
        total: 1,
      })
      expect(result.success).to.be.true
    })

    it('should accept empty oauthConfigs array', () => {
      const result = listToolsetOAuthConfigsResponseSchema.safeParse({
        status: 'success',
        oauthConfigs: [],
        total: 0,
      })
      expect(result.success).to.be.true
    })

    it('should accept config with optional tenantId', () => {
      const result = listToolsetOAuthConfigsResponseSchema.safeParse({
        status: 'success',
        oauthConfigs: [{ ...validOAuthConfig, tenantId: 'tid', additionalParams: { prompt: 'consent' } }],
        total: 1,
      })
      expect(result.success).to.be.true
    })

    it('should accept config with additionalParams: null', () => {
      const result = listToolsetOAuthConfigsResponseSchema.safeParse({
        status: 'success',
        oauthConfigs: [{ ...validOAuthConfig, additionalParams: null }],
        total: 1,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing total', () => {
      const result = listToolsetOAuthConfigsResponseSchema.safeParse({
        status: 'success',
        oauthConfigs: [],
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('updateToolsetOAuthConfigResponseSchema', () => {
    it('should accept valid response', () => {
      const result = updateToolsetOAuthConfigResponseSchema.safeParse({
        status: 'success',
        oauthConfigId: 'oc-1',
        message: 'Config updated.',
        deauthenticatedUserCount: 3,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing oauthConfigId', () => {
      const result = updateToolsetOAuthConfigResponseSchema.safeParse({
        status: 'success',
        message: 'Config updated.',
        deauthenticatedUserCount: 0,
      })
      expect(result.success).to.be.false
    })

    it('should reject missing deauthenticatedUserCount', () => {
      const result = updateToolsetOAuthConfigResponseSchema.safeParse({
        status: 'success',
        oauthConfigId: 'oc-1',
        message: 'Config updated.',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('deleteToolsetOAuthConfigResponseSchema', () => {
    it('should accept valid response', () => {
      const result = deleteToolsetOAuthConfigResponseSchema.safeParse({
        status: 'success',
        message: 'Config deleted.',
      })
      expect(result.success).to.be.true
    })

    it('should reject wrong status literal', () => {
      const result = deleteToolsetOAuthConfigResponseSchema.safeParse({
        status: 'deleted',
        message: 'Config deleted.',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('authenticateAgentToolsetResponseSchema', () => {
    it('should accept isAuthenticated: true', () => {
      const result = authenticateAgentToolsetResponseSchema.safeParse({
        status: 'success',
        message: 'Agent toolset authenticated successfully.',
        isAuthenticated: true,
      })
      expect(result.success).to.be.true
    })

    it('should accept isAuthenticated: false', () => {
      const result = authenticateAgentToolsetResponseSchema.safeParse({
        status: 'success',
        message: 'Authentication failed.',
        isAuthenticated: false,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing isAuthenticated', () => {
      const result = authenticateAgentToolsetResponseSchema.safeParse({
        status: 'success',
        message: 'Agent toolset authenticated successfully.',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getAgentToolsetsResponseSchema', () => {
    it('should accept valid response mirroring my-toolsets shape', () => {
      const result = getAgentToolsetsResponseSchema.safeParse({
        status: 'success',
        toolsets: [validMyToolsetEntry],
        pagination: validPaginationWithFlags,
        filterCounts: validFilterCounts,
      })
      expect(result.success).to.be.true
    })

    it('should accept empty toolsets array', () => {
      const result = getAgentToolsetsResponseSchema.safeParse({
        status: 'success',
        toolsets: [],
        pagination: validPaginationWithFlags,
        filterCounts: validFilterCounts,
      })
      expect(result.success).to.be.true
    })

    it('should reject missing filterCounts', () => {
      const result = getAgentToolsetsResponseSchema.safeParse({
        status: 'success',
        toolsets: [],
        pagination: validPaginationWithFlags,
      })
      expect(result.success).to.be.false
    })

    it('should reject wrong status', () => {
      const result = getAgentToolsetsResponseSchema.safeParse({
        status: 'error',
        toolsets: [],
        pagination: validPaginationWithFlags,
        filterCounts: validFilterCounts,
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('updateAgentToolsetCredentialsResponseSchema', () => {
    it('should accept valid response', () => {
      const result = updateAgentToolsetCredentialsResponseSchema.safeParse({
        status: 'success',
        message: 'Agent credentials updated successfully.',
      })
      expect(result.success).to.be.true
    })

    it('should reject wrong status', () => {
      const result = updateAgentToolsetCredentialsResponseSchema.safeParse({
        status: 'error',
        message: 'Failed.',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('removeAgentToolsetCredentialsResponseSchema', () => {
    it('should accept valid response', () => {
      const result = removeAgentToolsetCredentialsResponseSchema.safeParse({
        status: 'success',
        message: 'Agent credentials removed successfully.',
      })
      expect(result.success).to.be.true
    })

    it('should reject missing message', () => {
      const result = removeAgentToolsetCredentialsResponseSchema.safeParse({ status: 'success' })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('reauthenticateAgentToolsetResponseSchema', () => {
    it('should accept valid response', () => {
      const result = reauthenticateAgentToolsetResponseSchema.safeParse({
        status: 'success',
        message: 'Agent credentials cleared. Please re-authenticate.',
      })
      expect(result.success).to.be.true
    })

    it('should reject wrong status literal', () => {
      const result = reauthenticateAgentToolsetResponseSchema.safeParse({
        status: 'ok',
        message: 'Cleared.',
      })
      expect(result.success).to.be.false
    })
  })

  // --------------------------------------------------------------------------
  describe('getAgentToolsetOAuthUrlResponseSchema', () => {
    it('should accept valid response', () => {
      const result = getAgentToolsetOAuthUrlResponseSchema.safeParse({
        success: true,
        authorizationUrl: 'https://github.com/login/oauth/authorize?client_id=cid&state=enc',
        state: 'encoded-state-with-agent-key',
      })
      expect(result.success).to.be.true
    })

    it('should reject success: false', () => {
      const result = getAgentToolsetOAuthUrlResponseSchema.safeParse({
        success: false,
        authorizationUrl: 'https://github.com/login/oauth/authorize',
        state: 'state',
      })
      expect(result.success).to.be.false
    })

    it('should reject missing state', () => {
      const result = getAgentToolsetOAuthUrlResponseSchema.safeParse({
        success: true,
        authorizationUrl: 'https://github.com/login/oauth/authorize',
      })
      expect(result.success).to.be.false
    })

    it('should reject missing authorizationUrl', () => {
      const result = getAgentToolsetOAuthUrlResponseSchema.safeParse({
        success: true,
        state: 'state',
      })
      expect(result.success).to.be.false
    })
  })
})
