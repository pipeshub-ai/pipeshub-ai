import { apiClient } from '@/lib/api';
import type {
  Connector,
  ConnectorListResponse,
  ConnectorScope,
  ConnectorSchemaResponse,
  ConnectorConfig,
  FilterOptionsResponse,
  ConnectorStatsResponse,
} from './types';

const BASE_URL = '/api/v1/connectors';

export const ConnectorsApi = {
  // ── List & Registry ──

  /**
   * Fetch active (configured) connectors for a given scope.
   */
  async getActiveConnectors(
    scope: ConnectorScope,
    page = 1,
    limit = 20
  ): Promise<ConnectorListResponse> {
    const { data } = await apiClient.get<ConnectorListResponse>(BASE_URL, {
      params: { scope, page, limit },
    });
    return data;
  },

  /**
   * Fetch the full connector registry (all available connectors).
   */
  async getRegistryConnectors(
    scope: ConnectorScope,
    page = 1,
    limit = 20
  ): Promise<ConnectorListResponse> {
    const { data } = await apiClient.get<ConnectorListResponse>(
      `${BASE_URL}/registry`,
      { params: { scope, page, limit } }
    );
    return data;
  },

  // ── Schema ──

  /** Fetch connector schema from registry */
  async getConnectorSchema(
    connectorType: string
  ): Promise<ConnectorSchemaResponse> {
    const { data } = await apiClient.get<ConnectorSchemaResponse>(
      `${BASE_URL}/registry/${connectorType}/schema`
    );
    return data;
  },

  // ── Instance Management ──

  /** Create a new connector instance */
  async createConnectorInstance(payload: {
    connectorType: string;
    instanceName: string;
    scope: 'personal' | 'team';
    authType: string;
    config: { auth: Record<string, unknown> };
    baseUrl?: string;
  }) {
    const { data } = await apiClient.post(BASE_URL, payload);
    return data;
  },

  /** Delete a connector instance */
  async deleteConnectorInstance(connectorId: string) {
    const { data } = await apiClient.delete(`${BASE_URL}/${connectorId}`);
    return data;
  },

  /** Update connector instance name */
  async updateConnectorInstanceName(connectorId: string, instanceName: string) {
    const { data } = await apiClient.put(`${BASE_URL}/${connectorId}/name`, {
      instanceName,
    });
    return data;
  },

  // ── Configuration ──

  /** Fetch saved config for an existing connector instance */
  async getConnectorConfig(connectorId: string): Promise<ConnectorConfig> {
    const { data } = await apiClient.get<{ success: boolean; config: ConnectorConfig }>(
      `${BASE_URL}/${connectorId}/config`
    );
    return data.config;
  },

  /** Save auth config only */
  async saveAuthConfig(
    connectorId: string,
    payload: {
      auth: Record<string, unknown>;
      baseUrl: string;
    }
  ) {
    const { data } = await apiClient.put(
      `${BASE_URL}/${connectorId}/config/auth`,
      payload
    );
    return data;
  },

  /** Save filters + sync config */
  async saveFiltersSyncConfig(
    connectorId: string,
    payload: {
      sync: {
        selectedStrategy: string;
        customValues?: Record<string, unknown>;
        [key: string]: unknown;
      };
      filters: {
        sync?: { values?: Record<string, unknown> };
        indexing?: { values?: Record<string, unknown> };
      };
      baseUrl: string;
    }
  ) {
    const { data } = await apiClient.put(
      `${BASE_URL}/${connectorId}/config/filters-sync`,
      payload
    );
    return data;
  },

  // ── OAuth ──

  /** Get OAuth authorization URL (opens in popup for user consent) */
  async getOAuthAuthorizationUrl(
    connectorId: string
  ): Promise<{ authorizationUrl: string; state: string }> {
    const baseUrl = window.location.origin;
    const { data } = await apiClient.get<{
      authorizationUrl: string;
      state: string;
    }>(
      `${BASE_URL}/${connectorId}/oauth/authorize`,
      { params: { baseUrl } }
    );
    return data;
  },

  // ── Filter Options (dynamic) ──

  /** Fetch available filter options for a specific filter field */
  async getFilterFieldOptions(
    connectorId: string,
    filterKey: string,
    params?: {
      page?: number;
      limit?: number;
      search?: string;
      cursor?: string;
    }
  ): Promise<FilterOptionsResponse> {
    const { data } = await apiClient.get<FilterOptionsResponse>(
      `${BASE_URL}/${connectorId}/filters/${filterKey}/options`,
      { params }
    );
    return data;
  },

  // ── Toggle ──

  /** Toggle sync or agent for a connector instance */
  async toggleConnector(connectorId: string, type: 'sync' | 'agent') {
    const { data } = await apiClient.post(
      `${BASE_URL}/${connectorId}/toggle`,
      { type }
    );
    return data;
  },

  // ── Instance Details ──

  /** Fetch a specific connector instance */
  async getConnectorInstance(connectorId: string): Promise<Connector> {
    const { data } = await apiClient.get<Connector>(
      `${BASE_URL}/${connectorId}`
    );
    return data;
  },

  /** Start sync for a connector instance */
  async startSync(connectorId: string) {
    const { data } = await apiClient.post(
      `${BASE_URL}/${connectorId}/toggle`,
      { type: 'sync' }
    );
    return data;
  },

  // ── Reindex Failed ──

  /** Resync records for a connector */
  async resyncConnector(connectorId: string, connectorName: string) {
    const { data } = await apiClient.post(
      '/api/v1/knowledgeBase/resync/connector',
      {
        connectorName,
        connectorId,
      }
    );
    return data;
  },

  // ── Stats ──

  /** Fetch indexing stats for a connector instance */
  async getConnectorStats(
    connectorId: string
  ): Promise<ConnectorStatsResponse> {
    const { data } = await apiClient.get<ConnectorStatsResponse>(
      `/api/v1/knowledgeBase/stats/${connectorId}`
    );
    return data;
  },
};
