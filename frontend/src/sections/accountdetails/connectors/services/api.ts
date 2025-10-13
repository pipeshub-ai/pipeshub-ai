/**
 * Connector API Service
 * 
 * Service layer for interacting with connector backend APIs.
 * Handles all HTTP requests related to connectors, including
 * registry, instances, configuration, OAuth, and filters.
 */

import axios from "src/utils/axios";
import { Connector, ConnectorConfig, ConnectorRegistry } from "../types/types";

const BASE_URL = '/api/v1/connectors';

export class ConnectorApiService {
  // ============================================================================
  // Registry APIs
  // ============================================================================

  /**
   * Get all available connector types from registry
   */
  static async getConnectorRegistry(): Promise<ConnectorRegistry[]> {
    const response = await axios.get(`${BASE_URL}/registry`);
    if (!response.data) throw new Error('Failed to fetch connector registry');
    return response.data.connectors || [];
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
   */
  static async getConnectorInstances(): Promise<Connector[]> {
    const response = await axios.get(`${BASE_URL}`);
    if (!response.data) throw new Error('Failed to fetch connector instances');
    return response.data.connectors || [];
  }

  /**
   * Create a new connector instance
   */
  static async createConnectorInstance(
    connectorType: string,
    instanceName: string,
    config?: any
  ): Promise<{ connectorId: string; connectorType: string; instanceName: string }> {
    const baseUrl = window.location.origin;
    const response = await axios.post(`${BASE_URL}`, {
      connectorType,
      instanceName,
      config,
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
   */
  static async getConfiguredConnectorInstances(): Promise<Connector[]> {
    const response = await axios.get(`${BASE_URL}/configured`);
    if (!response.data) throw new Error('Failed to fetch configured connector instances');
    return response.data.connectors || [];
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
    const response = await axios.put(`${BASE_URL}/${connectorId}/config`, {
      ...config,
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
  static async toggleConnectorInstance(connectorId: string): Promise<boolean> {
    const response = await axios.post(`${BASE_URL}/${connectorId}/toggle`);
    if (!response.data) throw new Error('Failed to toggle connector instance');
    return response.data.success;
  }

  // ============================================================================
  // Legacy APIs (Backward Compatibility)
  // ============================================================================

  /**
   * @deprecated Use getConnectorInstances instead
   */
  static async getConnectors(): Promise<Connector[]> {
    return this.getConnectorInstances();
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
    const instances = await this.getConnectorInstances();
    const instance = instances.find(i => i.name === connectorName || i.type === connectorName);
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
    const instances = await this.getConnectorInstances();
    const instance = instances.find(i => i.name === connectorName || i.type === connectorName);
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
    const instances = await this.getConnectorInstances();
    const instance = instances.find(i => i.name === connectorName || i.type === connectorName);
    if (!instance || !instance._key) {
      throw new Error(`Connector instance not found for name: ${connectorName}`);
    }
    return this.toggleConnectorInstance(instance._key);
  }

  /**
   * @deprecated Use getOAuthAuthorizationUrl with connectorId instead
   */
  static async getOAuthAuthorizationUrlByName(
    connectorName: string
  ): Promise<{ authorizationUrl: string; state: string }> {
    // This is a compatibility shim
    const instances = await this.getConnectorInstances();
    const instance = instances.find(i => i.name === connectorName || i.type === connectorName);
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
    const instances = await this.getConnectorInstances();
    const instance = instances.find(i => i.name === connectorName || i.type === connectorName);
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
    const instances = await this.getConnectorInstances();
    const instance = instances.find(i => i.name === connectorName || i.type === connectorName);
    if (!instance || !instance._key) {
      throw new Error(`Connector instance not found for name: ${connectorName}`);
    }
    return this.saveConnectorInstanceFilters(instance._key, filters);
  }
}