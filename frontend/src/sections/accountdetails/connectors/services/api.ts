/**
 * Connector API Service
 * 
 * Service layer for interacting with connector backend APIs.
 * Handles all HTTP requests related to connectors, including
 * registry, instances, configuration, OAuth, and filters.
 */

import axios from "src/utils/axios";
import { Connector, ConnectorConfig, ConnectorRegistry, ConnectorToggleType } from "../types/types";
import { trimConnectorConfig } from "../utils/trim-config";

const BASE_URL = '/api/v1/connectors';

export class ConnectorApiService {
  // ============================================================================
  // Registry APIs
  // ============================================================================

  /**
   * Get all available connector types from registry
   * @param scope - Optional scope filter ('personal' | 'team')
   * @param page - Page number (default: 1)
   * @param limit - Items per page (default: 20)
   * @param search - Optional search query
   */
  static async getConnectorRegistry(
    scope?: 'personal' | 'team',
    page?: number,
    limit?: number,
    search?: string
  ): Promise<{ connectors: ConnectorRegistry[]; pagination: any; registryCountsByScope?: { personal: number; team: number } }> {
    const params: any = {};
    if (scope) params.scope = scope;
    if (typeof page === 'number' && Number.isFinite(page)) params.page = page;
    if (typeof limit === 'number' && Number.isFinite(limit)) params.limit = limit;
    if (search) params.search = search;
    
    const response = await axios.get(`${BASE_URL}/registry`, { params });
    if (!response.data) throw new Error('Failed to fetch connector registry');
    return {
      connectors: response.data.connectors || [],
      pagination: response.data.pagination || {},
      registryCountsByScope: response.data.registryCountsByScope || { personal: 0, team: 0 }
    };
  }

  /**
   * Get connector schema for a specific type
   */
  static async getConnectorSchema(connectorType: string): Promise<any> {
    const response = await axios.get(`${BASE_URL}/registry/${connectorType}/schema`);
    if (!response.data) throw new Error('Failed to fetch connector schema');
    return response.data.schema;
  }

  // ============================================================================
  // Instance Management APIs
  // ============================================================================

  /**
   * Get all configured connector instances
   * @param scope - Optional scope filter ('personal' | 'team')
   * @param page - Page number (default: 1)
   * @param limit - Items per page (default: 20)
   * @param search - Optional search query
   */
  static async getConnectorInstances(
    scope?: 'personal' | 'team',
    page?: number,
    limit?: number,
    search?: string
  ): Promise<{ connectors: Connector[]; pagination: any; scopeCounts?: { personal: number; team: number } }> {
    const params: any = {};
    if (scope) params.scope = scope;
    if (typeof page === 'number' && Number.isFinite(page)) params.page = page;
    if (typeof limit === 'number' && Number.isFinite(limit)) params.limit = limit;
    if (search) params.search = search;
    
    const response = await axios.get(`${BASE_URL}`, { params });
    if (!response.data) throw new Error('Failed to fetch connector instances');
    return {
      connectors: response.data.connectors || [],
      pagination: response.data.pagination || {},
      scopeCounts: response.data.scopeCounts || { personal: 0, team: 0 }
    };
  }

  /**
   * Create a new connector instance
   * @param connectorType - Type of connector from registry
   * @param instanceName - Name for this instance
   * @param scope - Scope for the connector ('personal' | 'team', default: 'personal')
   * @param config - Optional initial configuration
   */
  static async createConnectorInstance(
    connectorType: string,
    instanceName: string,
    scope: 'personal' | 'team' = 'personal',
    config?: any
  ): Promise<{ connectorId: string; connectorType: string; instanceName: string; scope: string }> {
    const baseUrl = window.location.origin;
    // Trim whitespace from instance name and config
    const trimmedInstanceName = instanceName.trim();
    const trimmedConfig = config ? trimConnectorConfig(config) : config;
    const response = await axios.post(`${BASE_URL}`, {
      connectorType,
      instanceName: trimmedInstanceName,
      scope,
      config: trimmedConfig,
      baseUrl,
    });
    if (!response.data) throw new Error('Failed to create connector instance');
    return response.data.connector;
  }

  /**
   * Get a specific connector instance by key
   */
  static async getConnectorInstance(connectorId: string): Promise<Connector> {
    const response = await axios.get(`${BASE_URL}/${connectorId}`);
    if (!response.data) throw new Error('Failed to fetch connector instance');
    return response.data.connector;
  }

  /**
   * Delete a connector instance
   */
  static async deleteConnectorInstance(connectorId: string): Promise<boolean> {
    const response = await axios.delete(`${BASE_URL}/${connectorId}`);
    if (!response.data) throw new Error('Failed to delete connector instance');
    return response.data.success;
  }

  /**
   * Update connector instance name
   */
  static async updateConnectorInstanceName(connectorId: string, instanceName: string): Promise<{ connector: { _key: string; name: string } }> {
    const response = await axios.put(`${BASE_URL}/${connectorId}/name`, {
      instanceName,
    });
    if (!response.data) throw new Error('Failed to update connector instance name');
    return response.data;
  }

  /**
   * Get all active connector instances
   */
  static async getActiveConnectorInstances(): Promise<Connector[]> {
    const response = await axios.get(`${BASE_URL}/active`);
    if (!response.data) throw new Error('Failed to fetch active connector instances');
    return response.data.connectors || [];
  }

  /**
   * Get all inactive connector instances
   */
  static async getInactiveConnectorInstances(): Promise<Connector[]> {
    const response = await axios.get(`${BASE_URL}/inactive`);
    if (!response.data) throw new Error('Failed to fetch inactive connector instances');
    return response.data.connectors || [];
  }

  /**
   * Get all configured connector instances
   * @param scope - Optional scope filter ('personal' | 'team')
   * @param page - Page number (default: 1)
   * @param limit - Items per page (default: 20)
   * @param search - Optional search query
   */
  static async getConfiguredConnectorInstances(
    scope?: 'personal' | 'team',
    page?: number,
    limit?: number,
    search?: string
  ): Promise<{ connectors: Connector[]; pagination: any }> {
    const params: any = {};
    if (scope) params.scope = scope;
    if (typeof page === 'number' && Number.isFinite(page)) params.page = page;
    if (typeof limit === 'number' && Number.isFinite(limit)) params.limit = limit;
    if (search) params.search = search;
    
    const response = await axios.get(`${BASE_URL}/configured`, { params });
    if (!response.data) throw new Error('Failed to fetch configured connector instances');
    return {
      connectors: response.data.connectors || [],
      pagination: response.data.pagination || {}
    };
  }

  // ============================================================================
  // Configuration APIs
  // ============================================================================

  /**
   * Get configuration for a connector instance
   */
  static async getConnectorInstanceConfig(connectorId: string): Promise<ConnectorConfig> {
    const response = await axios.get(`${BASE_URL}/${connectorId}/config`);
    if (!response.data) throw new Error('Failed to fetch connector instance config');
    return response.data.config;
  }

  /**
   * Update configuration for a connector instance
   */
  static async updateConnectorInstanceConfig(connectorId: string, config: any): Promise<any> {
    // Trim whitespace from config before sending
    const trimmedConfig = trimConnectorConfig(config);
    const response = await axios.put(`${BASE_URL}/${connectorId}/config`, {
      ...trimmedConfig,
      baseUrl: window.location.origin,
    });
    if (!response.data) throw new Error('Failed to update connector instance config');
    return response.data.config;
  }

  // ============================================================================
  // OAuth APIs
  // ============================================================================

  /**
   * Get OAuth authorization URL for a connector instance
   */
  static async getOAuthAuthorizationUrl(
      connectorId: string
  ): Promise<{ authorizationUrl: string; state: string }> {
    const baseUrl = window.location.origin;
    const response = await axios.get(`${BASE_URL}/${connectorId}/oauth/authorize`, {
      params: { baseUrl },
    });
    if (!response.data) throw new Error('Failed to get OAuth authorization URL');
    return {
      authorizationUrl: response.data.authorizationUrl,
      state: response.data.state,
    };
  }

  // ============================================================================
  // Filter APIs
  // ============================================================================

  /**
   * Get filter options for a connector instance
   */
  static async getConnectorInstanceFilterOptions(connectorId: string): Promise<{ filterOptions: any }> {
    const response = await axios.get(`${BASE_URL}/${connectorId}/filters`);
    if (!response.data) throw new Error('Failed to get connector instance filter options');
    return response.data;
  }

  /**
   * Save filter selections for a connector instance
   */
  static async saveConnectorInstanceFilters(connectorId: string, filters: any): Promise<any> {
    const response = await axios.post(`${BASE_URL}/${connectorId}/filters`, {
      filters,
    });
    if (!response.data) throw new Error('Failed to save connector instance filters');
    return response.data;
  }

  // ============================================================================
  // Toggle API
  // ============================================================================

  /**
   * Toggle connector instance active status
   */
  static async toggleConnectorInstance(connectorId: string, type: ConnectorToggleType): Promise<boolean> {
    const response = await axios.post(`${BASE_URL}/${connectorId}/toggle`, { type });
    if (!response.data) throw new Error('Failed to toggle connector instance');
    return response.data.success;
  }

  /**
   Get all active agent instances
   */
    /**
   * Get all active agent connector instances
   * @param scope - Optional scope filter ('personal' | 'team')
   * @param page - Page number (default: 1)
   * @param limit - Items per page (default: 20)
   * @param search - Optional search query
   */
    static async getActiveAgentConnectorInstances(
      page?: number,
      limit?: number,
      search?: string,
    ): Promise<{ connectors: Connector[]; pagination: any }> {
      const params: any = {};
      if (typeof page === 'number' && Number.isFinite(page)) params.page = page;
      if (typeof limit === 'number' && Number.isFinite(limit)) params.limit = limit;
      if (search) params.search = search;
      
      const response = await axios.get(`${BASE_URL}/agents/active`, { params });
      if (!response.data) throw new Error('Failed to fetch configured connector instances');
      console.log(response.data);
      return {
        connectors: response.data.connectors || [],
        pagination: response.data.pagination || {}
      };
    }
  
  // ============================================================================
  // Legacy APIs (Backward Compatibility)
  // ============================================================================

  /**
   * @deprecated Use getConnectorInstances instead
   */
  static async getConnectors(): Promise<Connector[]> {
    const result = await this.getConnectorInstances();
    return result.connectors;
  }

  /**
   * @deprecated Use getActiveConnectorInstances instead
   */
  static async getActiveConnectors(): Promise<Connector[]> {
    return this.getActiveConnectorInstances();
  }

  /**
   * @deprecated Use getInactiveConnectorInstances instead
   */
  static async getInactiveConnectors(): Promise<Connector[]> {
    return this.getInactiveConnectorInstances();
  }

  /**
   * @deprecated Use getConnectorInstanceConfig instead
   */
  static async getConnectorConfig(connectorName: string): Promise<ConnectorConfig> {
    // This is a compatibility shim - in the new architecture, we need connectorId
    // For now, try to find the instance by name
    const result = await this.getConnectorInstances();
    const instance = result.connectors.find((i: Connector) => i.name === connectorName || i.type === connectorName);
    if (!instance || !instance._key) {
      throw new Error(`Connector instance not found for name: ${connectorName}`);
    }
    return this.getConnectorInstanceConfig(instance._key);
  }

  /**
   * @deprecated Use updateConnectorInstanceConfig instead
   */
  static async updateConnectorConfig(connectorName: string, config: any): Promise<any> {
    // This is a compatibility shim
    const result = await this.getConnectorInstances();
    const instance = result.connectors.find((i: Connector) => i.name === connectorName || i.type === connectorName);
    if (!instance || !instance._key) {
      throw new Error(`Connector instance not found for name: ${connectorName}`);
    }
    return this.updateConnectorInstanceConfig(instance._key, config);
  }

  /**
   * @deprecated Use toggleConnectorInstance instead
   */
  static async toggleConnector(connectorName: string): Promise<boolean> {
    // This is a compatibility shim
    const result = await this.getConnectorInstances();
    const instance = result.connectors.find((i: Connector) => i.name === connectorName || i.type === connectorName);
    if (!instance || !instance._key) {
      throw new Error(`Connector instance not found for name: ${connectorName}`);
    }
    return this.toggleConnectorInstance(instance._key, 'sync');
  }

  /**
   * @deprecated Use getOAuthAuthorizationUrl with connectorId instead
   */
  static async getOAuthAuthorizationUrlByName(
    connectorName: string
  ): Promise<{ authorizationUrl: string; state: string }> {
    // This is a compatibility shim
    const result = await this.getConnectorInstances();
    const instance = result.connectors.find((i: Connector) => i.name === connectorName || i.type === connectorName);
    if (!instance || !instance._key) {
      throw new Error(`Connector instance not found for name: ${connectorName}`);
    }
    return this.getOAuthAuthorizationUrl(instance._key);
  }

  /**
   * @deprecated OAuth callback is now handled automatically via state parameter
   */
  static async handleOAuthCallback(
    connectorName: string,
    code: string,
    state: string
  ): Promise<{ filterOptions: any }> {
    // This method is deprecated as OAuth callback now extracts connectorId from state
    throw new Error('OAuth callback is now handled automatically via the backend');
  }

  /**
   * @deprecated Use getConnectorInstanceFilterOptions instead
   */
  static async getConnectorFilterOptions(connectorName: string): Promise<{ filterOptions: any }> {
    // This is a compatibility shim
    const result = await this.getConnectorInstances();
    const instance = result.connectors.find((i: Connector) => i.name === connectorName || i.type === connectorName);
    if (!instance || !instance._key) {
      throw new Error(`Connector instance not found for name: ${connectorName}`);
    }
    return this.getConnectorInstanceFilterOptions(instance._key);
  }

  /**
   * @deprecated Use saveConnectorInstanceFilters instead
   */
  static async saveConnectorFilters(connectorName: string, filters: any): Promise<any> {
    // This is a compatibility shim
    const result = await this.getConnectorInstances();
    const instance = result.connectors.find((i: Connector) => i.name === connectorName || i.type === connectorName);
    if (!instance || !instance._key) {
      throw new Error(`Connector instance not found for name: ${connectorName}`);
    }
    return this.saveConnectorInstanceFilters(instance._key, filters);
  }
}