import axios from 'src/utils/axios';
import {
  RegistryToolset,
  Toolset,
  ToolsetAuthStatus,
  Tool,
} from '../types/agent';

// ============================================================================
// Instance-based types (new architecture)
// ============================================================================

export interface ToolsetInstance {
  /** Admin-created instance UUID */
  _id: string;
  instanceName: string;
  toolsetType: string;
  authType: 'OAUTH' | 'API_TOKEN' | 'BEARER_TOKEN' | 'USERNAME_PASSWORD' | 'NONE';
  oauthConfigId?: string;
  orgId: string;
  createdBy: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  // Enriched from registry
  displayName?: string;
  description?: string;
  iconPath?: string;
  toolCount?: number;
}

/** Merged view item from GET /my-toolsets */
export interface MyToolset {
  instanceId: string;
  instanceName: string;
  toolsetType: string;
  authType: 'OAUTH' | 'API_TOKEN' | 'BEARER_TOKEN' | 'USERNAME_PASSWORD' | 'NONE';
  oauthConfigId?: string;
  displayName: string;
  description: string;
  iconPath: string;
  category: string;
  supportedAuthTypes: string[];
  toolCount: number;
  tools: Array<{ name: string; fullName: string; description: string }>;
  isConfigured: boolean;
  isAuthenticated: boolean;
  createdBy?: string;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
}

/**
 * Toolset API Service
 * Handles all toolset-related API calls for registry, configuration, and auth status
 */
class ToolsetApiService {
  /**
   * Extract toolset_type from synthetic toolset ID
   * Format: {user_id}_{toolset_type}
   * Backend expects just toolset_type in the route
   */
  private static extractToolsetType(toolsetId: string): string {
    // If it's already just a toolset type (no underscore with user_id), return as is
    if (!toolsetId.includes('_')) {
      return toolsetId.toLowerCase();
    }
    // Extract toolset_type from synthetic ID format: {user_id}_{toolset_type}
    const parts = toolsetId.split('_');
    if (parts.length >= 2) {
      // Toolset type is everything after the last underscore
      return parts.slice(1).join('_').toLowerCase();
    }
    return toolsetId.toLowerCase();
  }

  /**
   * Get all toolsets from Python registry
   * Returns toolsets defined in code with @Toolset decorator
   * 
   * Performance options:
   * - includeTools=true: Full tool details for drag-and-drop (agent builder)
   * - includeTools=false: Just tool count (toolsets page - faster, smaller response)
   * 
   * Toolsets are grouped by category (app, database, utility, etc.)
   */
  static async getRegistryToolsets(params?: {
    page?: number;
    limit?: number;
    search?: string;
    includeTools?: boolean;
    includeToolCount?: boolean;
    groupByCategory?: boolean;
  }): Promise<{
    toolsets: RegistryToolset[];
    categorizedToolsets?: Record<string, RegistryToolset[]>;
    pagination: {
      page: number;
      limit: number;
      total: number;
      totalPages: number;
      hasNext: boolean;
      hasPrev: boolean;
    };
  }> {
    try {
      const queryParams = new URLSearchParams();
      if (params?.page) queryParams.append('page', params.page.toString());
      if (params?.limit) queryParams.append('limit', params.limit.toString());
      if (params?.search) queryParams.append('search', params.search);
      
      // Include full tool details (default: false for performance)
      if (params?.includeTools !== undefined) {
        queryParams.append('include_tools', params.includeTools.toString());
      } else {
        queryParams.append('include_tools', 'false'); // Default to false for performance
      }
      
      // Include tool count (default: true - lightweight)
      if (params?.includeToolCount !== undefined) {
        queryParams.append('include_tool_count', params.includeToolCount.toString());
      } else {
        queryParams.append('include_tool_count', 'true'); // Default to true
      }
      
      if (params?.groupByCategory !== undefined) {
        queryParams.append('group_by_category', params.groupByCategory.toString());
      } else {
        queryParams.append('group_by_category', 'true'); // Default to true
      }

      const response = await axios.get(
        `/api/v1/toolsets/registry?${queryParams.toString()}`
      );
      return {
        toolsets: response.data.toolsets || [],
        categorizedToolsets: response.data.categorizedToolsets,
        pagination: response.data.pagination || {
          page: 1,
          limit: 20,
          total: 0,
          totalPages: 0,
          hasNext: false,
          hasPrev: false,
        },
      };
    } catch (error) {
      console.error('Failed to fetch registry toolsets:', error);
      throw error;
    }
  }

  /**
   * Get toolset schema/configuration for a specific toolset type
   * Returns auth schemas, supported auth types, and field definitions
   */
  static async getToolsetSchema(toolsetType: string): Promise<any> {
    try {
      const response = await axios.get(
        `/api/v1/toolsets/registry/${toolsetType}/schema`
      );
      return response.data.toolset || response.data;
    } catch (error) {
      console.error('Failed to fetch toolset schema:', error);
      throw error;
    }
  }

  /**
   * Get all tools from registry (flat list)
   * Alternative endpoint for just tools without toolset grouping
   */
  static async getAllTools(params?: {
    appName?: string;
    tag?: string;
    search?: string;
  }): Promise<Tool[]> {
    try {
      const queryParams = new URLSearchParams();
      if (params?.appName) queryParams.append('app_name', params.appName);
      if (params?.tag) queryParams.append('tag', params.tag);
      if (params?.search) queryParams.append('search', params.search);

      const response = await axios.get(
        `/api/v1/toolsets/tools?${queryParams.toString()}`
      );
      return response.data || [];
    } catch (error) {
      console.error('Failed to fetch tools:', error);
      throw error;
    }
  }

  /**
   * Get toolsets with tools (alternative endpoint)
   * Returns same data as getRegistryToolsets but from /tools/toolsets endpoint
   */
  static async getToolsetsWithTools(params?: {
    page?: number;
    limit?: number;
    search?: string;
  }): Promise<{
    toolsets: RegistryToolset[];
    pagination: any;
  }> {
    try {
      const queryParams = new URLSearchParams();
      if (params?.page) queryParams.append('page', params.page.toString());
      if (params?.limit) queryParams.append('limit', params.limit.toString());
      if (params?.search) queryParams.append('search', params.search);

      const response = await axios.get(
        `/api/v1/tools/toolsets?${queryParams.toString()}`
      );
      return {
        toolsets: response.data.toolsets || [],
        pagination: response.data.pagination || {},
      };
    } catch (error) {
      console.error('Failed to fetch toolsets with tools:', error);
      throw error;
    }
  }

  /**
   * Get configured toolsets for current authenticated user (from database nodes)
   * Returns toolset nodes created for the user with auth status from etcd
   * User ID is extracted from auth token on backend
   */
  static async getConfiguredToolsets(): Promise<{ toolsets: Toolset[] }> {
    try {
      const response = await axios.get('/api/v1/toolsets/configured');
      return {
        toolsets: response.data.toolsets || [],
      };
    } catch (error) {
      console.error('Failed to fetch configured toolsets:', error);
      throw error;
    }
  }

  /**
   * Check toolset authentication status from etcd
   */
  static async checkToolsetStatus(
    toolsetId: string
  ): Promise<{
    isConfigured: boolean;
    isAuthenticated: boolean;
    authType?: string;
    toolsetName?: string;
    displayName?: string;
  }> {
    try {
      const toolsetType = this.extractToolsetType(toolsetId);
      const response = await axios.get(
        `/api/v1/toolsets/${toolsetType}/status`
      );
      return response.data;
    } catch (error) {
      console.error('Failed to check toolset status:', error);
      return {
        isConfigured: false,
        isAuthenticated: false,
      };
    }
  }

  /**
   * Get toolset configuration with instance, config, and schema (for editing/managing).
   * Returns everything needed to render the config dialog in one call.
   */
  static async getToolsetConfig(
    toolsetId: string
  ): Promise<{
    status: string;
    toolset: {
      toolsetId: string;
      _id: string;
      name: string;
      displayName: string;
      description: string;
      category: string;
      group: string;
      iconPath: string;
      supportedAuthTypes: string[];
      toolCount: number;
      tools: any[];
      userId: string;
      config: {
        auth?: any;
        isAuthenticated?: boolean;
        isConfigured?: boolean;
        [key: string]: any;
      };
      schema: {
        toolset: {
          name: string;
          displayName: string;
          description: string;
          category: string;
          supportedAuthTypes: string[];
          config: {
            auth: {
              schemas?: Record<string, { fields: any[] }>;
              [key: string]: any;
            };
          };
          tools: any[];
          oauthConfig?: any;
        };
      };
      oauthConfig?: any;
      isConfigured: boolean;
      isAuthenticated: boolean;
      authType?: string;
      [key: string]: any;
    };
  }> {
    try {
      const toolsetType = this.extractToolsetType(toolsetId);
      const response = await axios.get(
        `/api/v1/toolsets/${toolsetType}/config`
      );
      return response.data;
    } catch (error) {
      console.error('Failed to get toolset config:', error);
      throw error;
    }
  }

  /**
   * Create a new toolset (creates node and saves config)
   */
  static async createToolset(params: {
    name: string;
    displayName: string;
    type: string;
    auth: {
      type: string;
      clientId?: string;
      clientSecret?: string;
      apiToken?: string;
      oauthAppId?: string;
      scopes?: string[];
      [key: string]: any;
    };
    baseUrl?: string;
  }): Promise<string> {
    try {
      const response = await axios.post(
        '/api/v1/toolsets',
        params
      );
      return response.data.toolsetId || response.data._id;
    } catch (error) {
      console.error('Failed to create toolset:', error);
      throw error;
    }
  }

  /**
   * Update toolset configuration (OAuth credentials, API tokens, etc)
   */
  static async updateToolsetConfig(
    toolsetId: string,
    config: {
      auth: {
        type: string;
        clientId?: string;
        clientSecret?: string;
        apiToken?: string;
        oauthAppId?: string;
        scopes?: string[];
        [key: string]: any;
      };
      baseUrl?: string;
    }
  ): Promise<void> {
    try {
      const toolsetType = this.extractToolsetType(toolsetId);
      await axios.put(
        `/api/v1/toolsets/${toolsetType}/config`,
        config
      );
    } catch (error) {
      console.error('Failed to update toolset config:', error);
      throw error;
    }
  }

  /**
   * Save toolset configuration (OAuth credentials, API tokens, etc)
   * @deprecated Use createToolset or updateToolsetConfig instead
   */
  static async saveToolsetConfig(
    toolsetId: string,
    config: {
      auth: {
        type: string;
        clientId?: string;
        clientSecret?: string;
        apiToken?: string;
        oauthAppId?: string;
        scopes?: string[];
      };
    }
  ): Promise<void> {
    try {
      const toolsetType = this.extractToolsetType(toolsetId);
      await axios.post(
        `/api/v1/toolsets/${toolsetType}/config`,
        config
      );
    } catch (error) {
      console.error('Failed to save toolset config:', error);
      throw error;
    }
  }

  /**
   * Delete toolset configuration
   * User ID is extracted from auth token on backend
   */
  static async deleteToolsetConfig(toolsetName: string): Promise<void> {
    try {
      // toolsetName can be either just the name or the synthetic ID
      // Extract toolset type (backend expects just the type)
      const toolsetType = this.extractToolsetType(toolsetName);
      
      await axios.delete(
        `/api/v1/toolsets/${toolsetType}/config`
      );
    } catch (error) {
      console.error('Failed to delete toolset config:', error);
      throw error;
    }
  }

  /**
   * Reauthenticate toolset - clears OAuth credentials and marks as unauthenticated.
   * The user must then go through the OAuth flow again.
   * Only applicable to OAuth-configured toolsets.
   */
  static async reauthenticateToolset(toolsetId: string): Promise<void> {
    try {
      const toolsetType = this.extractToolsetType(toolsetId);
      await axios.post(`/api/v1/toolsets/${toolsetType}/reauthenticate`);
    } catch (error) {
      console.error('Failed to reauthenticate toolset:', error);
      throw error;
    }
  }

  /**
   * Get OAuth authorization URL for toolset
   */
  static async getOAuthAuthorizationUrl(
    toolsetId: string,
    baseUrl?: string
  ): Promise<{
    success: boolean;
    authorizationUrl: string;
    state: string;
  }> {
    try {
      const toolsetType = this.extractToolsetType(toolsetId);
      const queryParams = new URLSearchParams();
      if (baseUrl) queryParams.append('base_url', baseUrl);

      const response = await axios.get(
        `/api/v1/toolsets/${toolsetType}/oauth/authorize?${queryParams.toString()}`
      );
      return response.data;
    } catch (error) {
      console.error('Failed to get OAuth authorization URL:', error);
      throw error;
    }
  }

  /**
   * Find or create toolset node in database
   * Used when dragging toolset to agent builder
   */
  static async findOrCreateToolsetNode(params: {
    name: string;
    displayName: string;
    type: string;
    userId: string;
    metadata?: Record<string, any>;
  }): Promise<string> {
    try {
      const response = await axios.post(
        `/api/v1/toolsets/find-or-create`,
        params
      );
      return response.data.toolsetId;
    } catch (error) {
      console.error('Failed to find/create toolset node:', error);
      throw error;
    }
  }

  /**
   * Link tools to a toolset
   */
  static async linkToolsToToolset(
    toolsetId: string,
    tools: Array<{
      toolName: string;
      fullName: string;
      appName: string;
      description: string;
    }>,
    userId: string
  ): Promise<number> {
    try {
      const response = await axios.post(
        `/api/v1/toolsets/${toolsetId}/tools`,
        {
          tools,
          userId,
        }
      );
      return response.data.linkedCount;
    } catch (error) {
      console.error('Failed to link tools to toolset:', error);
      throw error;
    }
  }

  /**
   * Unlink a tool from a toolset
   */
  static async unlinkToolFromToolset(
    toolsetId: string,
    toolId: string
  ): Promise<void> {
    try {
      await axios.delete(
        `/api/v1/toolsets/${toolsetId}/tools/${toolId}`
      );
    } catch (error) {
      console.error('Failed to unlink tool from toolset:', error);
      throw error;
    }
  }

  /**
   * Get tools linked to a toolset
   */
  static async getToolsetTools(toolsetId: string): Promise<Tool[]> {
    try {
      const response = await axios.get(
        `/api/v1/toolsets/${toolsetId}/tools`
      );
      return response.data.tools || [];
    } catch (error) {
      console.error('Failed to get toolset tools:', error);
      throw error;
    }
  }

  // ============================================================================
  // Instance Management (New Admin-Created Instance Architecture)
  // ============================================================================

  /**
   * Get all admin-created toolset instances for the organization.
   */
  static async getToolsetInstances(params?: {
    page?: number;
    limit?: number;
    search?: string;
  }): Promise<{
    instances: ToolsetInstance[];
    pagination: {
      page: number;
      limit: number;
      total: number;
      totalPages: number;
      hasNext: boolean;
      hasPrev: boolean;
    };
  }> {
    try {
      const queryParams = new URLSearchParams();
      if (params?.page) queryParams.append('page', params.page.toString());
      if (params?.limit) queryParams.append('limit', params.limit.toString());
      if (params?.search) queryParams.append('search', params.search);

      const response = await axios.get(
        `/api/v1/toolsets/instances?${queryParams.toString()}`
      );
      return {
        instances: response.data.instances || [],
        pagination: response.data.pagination || {
          page: 1, limit: 50, total: 0, totalPages: 0, hasNext: false, hasPrev: false,
        },
      };
    } catch (error) {
      console.error('Failed to fetch toolset instances:', error);
      throw error;
    }
  }

  /**
   * Get a single toolset instance by ID.
   */
  static async getToolsetInstance(instanceId: string): Promise<ToolsetInstance> {
    try {
      const response = await axios.get(`/api/v1/toolsets/instances/${instanceId}`);
      return response.data.instance;
    } catch (error) {
      console.error('Failed to fetch toolset instance:', error);
      throw error;
    }
  }

  /**
   * Create a new admin toolset instance.
   * Admin only.
   */
  static async createToolsetInstance(params: {
    instanceName: string;
    toolsetType: string;
    authType: string;
    baseUrl?: string;
    authConfig?: {
      clientId?: string;
      clientSecret?: string;
      [key: string]: any;
    };
    [key: string]: any;
  }): Promise<ToolsetInstance> {
    try {
      const response = await axios.post('/api/v1/toolsets/instances', params);
      return response.data.instance;
    } catch (error) {
      console.error('Failed to create toolset instance:', error);
      throw error;
    }
  }

  /**
   * Update an admin toolset instance (rename, update OAuth credentials, or switch oauthConfigId).
   * Admin only. Changing oauthConfigId or credentials will deauthenticate all users.
   */
  static async updateToolsetInstance(
    instanceId: string,
    params: {
      instanceName?: string;
      baseUrl?: string;
      oauthConfigId?: string;
      authConfig?: {
        clientId?: string;
        clientSecret?: string;
        [key: string]: any;
      };
    }
  ): Promise<ToolsetInstance> {
    try {
      const response = await axios.put(`/api/v1/toolsets/instances/${instanceId}`, params);
      return response.data.instance;
    } catch (error) {
      console.error('Failed to update toolset instance:', error);
      throw error;
    }
  }

  /**
   * Delete an admin toolset instance.
   * Admin only.
   */
  static async deleteToolsetInstance(instanceId: string): Promise<void> {
    try {
      await axios.delete(`/api/v1/toolsets/instances/${instanceId}`);
    } catch (error) {
      console.error('Failed to delete toolset instance:', error);
      throw error;
    }
  }

  /**
   * Get merged view: admin instances + current user's auth status.
   * This replaces getConfiguredToolsets() for the new architecture.
   */
  static async getMyToolsets(params?: {
    search?: string;
  }): Promise<{ toolsets: MyToolset[] }> {
    try {
      const queryParams = new URLSearchParams();
      if (params?.search) queryParams.append('search', params.search);

      const response = await axios.get(
        `/api/v1/toolsets/my-toolsets?${queryParams.toString()}`
      );
      return { toolsets: response.data.toolsets || [] };
    } catch (error) {
      console.error('Failed to fetch my toolsets:', error);
      throw error;
    }
  }

  /**
   * Authenticate an instance with API token, bearer token, or username/password.
   * For OAUTH, use getInstanceOAuthAuthorizationUrl instead.
   */
  static async authenticateToolsetInstance(
    instanceId: string,
    credentials: {
      apiToken?: string;
      bearerToken?: string;
      username?: string;
      password?: string;
      [key: string]: any;
    }
  ): Promise<{ isAuthenticated: boolean }> {
    try {
      const response = await axios.post(
        `/api/v1/toolsets/instances/${instanceId}/authenticate`,
        { credentials }
      );
      return response.data;
    } catch (error) {
      console.error('Failed to authenticate toolset instance:', error);
      throw error;
    }
  }

  /**
   * Remove the current user's credentials for a toolset instance.
   */
  static async removeToolsetCredentials(instanceId: string): Promise<void> {
    try {
      await axios.delete(`/api/v1/toolsets/instances/${instanceId}/credentials`);
    } catch (error) {
      console.error('Failed to remove toolset credentials:', error);
      throw error;
    }
  }

  /**
   * Clear OAuth credentials for re-authentication.
   */
  static async reauthenticateToolsetInstance(instanceId: string): Promise<void> {
    try {
      await axios.post(`/api/v1/toolsets/instances/${instanceId}/reauthenticate`);
    } catch (error) {
      console.error('Failed to reauthenticate toolset instance:', error);
      throw error;
    }
  }

  /**
   * Get OAuth authorization URL for a toolset instance.
   */
  static async getInstanceOAuthAuthorizationUrl(
    instanceId: string,
    baseUrl?: string
  ): Promise<{ success: boolean; authorizationUrl: string; state: string }> {
    try {
      const queryParams = new URLSearchParams();
      if (baseUrl) queryParams.append('base_url', baseUrl);

      const response = await axios.get(
        `/api/v1/toolsets/instances/${instanceId}/oauth/authorize?${queryParams.toString()}`
      );
      return response.data;
    } catch (error) {
      console.error('Failed to get instance OAuth authorization URL:', error);
      throw error;
    }
  }

  /**
   * Get authentication status for a specific instance (current user).
   */
  static async getInstanceStatus(instanceId: string): Promise<{
    isConfigured: boolean;
    isAuthenticated: boolean;
    authType?: string;
    instanceName?: string;
    toolsetType?: string;
  }> {
    try {
      const response = await axios.get(
        `/api/v1/toolsets/instances/${instanceId}/status`
      );
      return response.data;
    } catch (error) {
      console.error('Failed to get instance status:', error);
      return { isConfigured: false, isAuthenticated: false };
    }
  }

  /**
   * List OAuth configurations for a toolset type (admin).
   * Admins also see clientId, authorizeUrl, tokenUrl, scopes, redirectUri.
   * clientSecret is never returned; clientSecretSet indicates if one is stored.
   */
  static async listToolsetOAuthConfigs(toolsetType: string): Promise<{
    oauthConfigs: OAuthConfigSummary[];
    total: number;
  }> {
    try {
      const response = await axios.get(
        `/api/v1/toolsets/oauth-configs/${toolsetType}`
      );
      return response.data;
    } catch (error) {
      console.error('Failed to list toolset OAuth configs:', error);
      throw error;
    }
  }

  /**
   * Update an admin-level OAuth configuration for a toolset type.
   * This will deauthenticate all users of all instances using this config.
   * Returns the number of deauthenticated users.
   */
  static async updateToolsetOAuthConfig(
    toolsetType: string,
    oauthConfigId: string,
    params: {
      authConfig: {
        clientId?: string;
        clientSecret?: string;
        authorizeUrl?: string;
        tokenUrl?: string;
        scopes?: string[];
        redirectUri?: string;
        [key: string]: any;
      };
      baseUrl?: string;
    }
  ): Promise<{ oauthConfigId: string; message: string; deauthenticatedUserCount: number }> {
    try {
      const response = await axios.put(
        `/api/v1/toolsets/oauth-configs/${toolsetType}/${oauthConfigId}`,
        params
      );
      return response.data;
    } catch (error) {
      console.error('Failed to update toolset OAuth config:', error);
      throw error;
    }
  }

  /**
   * Delete an admin-level OAuth configuration.
   * SAFE DELETE: fails if any toolset instance references this config.
   */
  static async deleteToolsetOAuthConfig(
    toolsetType: string,
    oauthConfigId: string
  ): Promise<void> {
    try {
      await axios.delete(`/api/v1/toolsets/oauth-configs/${toolsetType}/${oauthConfigId}`);
    } catch (error) {
      console.error('Failed to delete toolset OAuth config:', error);
      throw error;
    }
  }
}

// ============================================================================
// Supporting Types
// ============================================================================

/** Summary of an admin-level OAuth config (clientSecret never returned). */
export interface OAuthConfigSummary {
  _id: string;
  oauthInstanceName: string;
  orgId: string;
  toolsetType: string;
  createdBy?: string;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
  // Admin-only fields (populated when is_admin=true)
  clientId?: string;
  /** Whether a clientSecret is stored (never the actual secret) */
  clientSecretSet?: boolean;
  authorizeUrl?: string;
  tokenUrl?: string;
  scopes?: string[];
  redirectUri?: string;
}

export default ToolsetApiService;
