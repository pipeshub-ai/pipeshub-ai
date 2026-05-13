import { apiClient } from '@/lib/api';
import type { ConnectorOAuthCallbackRaw } from '@/app/(main)/connectors/oauth/connector-oauth-callback-response';
import type {
  Connector,
  ConnectorListResponse,
  ConnectorScope,
  ConnectorSchemaResponse,
  ConnectorConfig,
  FilterOptionsResponse,
  ConnectorStatsResponse,
} from './types';
import { CONNECTOR_INSTANCE_STATUS } from './constants';
import { trimConnectorConfig } from './utils/trim-config';
import { expandRelativeDatetimeFiltersForSave } from './utils/expand-relative-datetime-filters-for-save';

const BASE_URL = '/api/v1/connectors';

/** Normalized DELETE /connectors/:id body for optimistic UI merge. */
export type DeleteConnectorInstanceMerge = {
  _key: string;
  type: string;
  status: string | null;
};

/** Fields read from DELETE /connectors/:id JSON (may be partial or empty). */
interface DeleteConnectorInstanceResponseBody {
  _key?: string;
  type?: string;
  status?: string | null;
}

function resolveDeleteInstanceResponseStatus(
  body: DeleteConnectorInstanceResponseBody
): string | null {
  if (!('status' in body)) {
    return CONNECTOR_INSTANCE_STATUS.DELETING;
  }
  const s = body.status;
  if (typeof s === 'string' && s.length > 0) {
    return s;
  }
  if (s === null) {
    return CONNECTOR_INSTANCE_STATUS.DELETING;
  }
  return CONNECTOR_INSTANCE_STATUS.DELETING;
}

function parseDeleteConnectorInstanceBody(
  data: DeleteConnectorInstanceResponseBody | null | undefined,
  fallbackConnectorId: string
): DeleteConnectorInstanceMerge {
  if (data && typeof data === 'object' && typeof data._key === 'string') {
    return {
      _key: data._key,
      type: typeof data.type === 'string' ? data.type : '',
      status: resolveDeleteInstanceResponseStatus(data),
    };
  }
  return {
    _key: fallbackConnectorId,
    type: '',
    status: CONNECTOR_INSTANCE_STATUS.DELETING,
  };
}

export const ConnectorsApi = {
  // ── List & Registry ──

  /**
   * Fetch active (configured) connectors for a given scope.
   */
  async getActiveConnectors(
    scope: ConnectorScope,
    page = 1,
    limit = 100
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
    limit = 100
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

  /** Delete a connector instance; response is normalized for optimistic store merge. */
  async deleteConnectorInstance(connectorId: string): Promise<DeleteConnectorInstanceMerge> {
    const { data } = await apiClient.delete<DeleteConnectorInstanceResponseBody | null>(
      `${BASE_URL}/${connectorId}`,
      { suppressErrorToast: true }
    );
    return parseDeleteConnectorInstanceBody(data ?? null, connectorId);
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
    const f = payload.filters;
    const body =
      f != null
        ? {
            ...payload,
            filters: {
              ...f,
              ...(f.sync
                ? {
                    sync: {
                      ...f.sync,
                      values: trimConnectorConfig(
                        expandRelativeDatetimeFiltersForSave(
                          (f.sync.values ?? {}) as Record<string, unknown>
                        )
                      ),
                    },
                  }
                : {}),
              ...(f.indexing
                ? {
                    indexing: {
                      ...f.indexing,
                      values: trimConnectorConfig(
                        expandRelativeDatetimeFiltersForSave(
                          (f.indexing.values ?? {}) as Record<string, unknown>
                        )
                      ),
                    },
                  }
                : {}),
            },
          }
        : payload;

    const { data } = await apiClient.put(
      `${BASE_URL}/${connectorId}/config/filters-sync`,
      body
    );
    return data;
  },

  // ── OAuth ──

  /** List saved OAuth app registrations for a connector type (same contract as legacy UI). */
  async listOAuthConfigs(
    connectorType: string,
    page = 1,
    limit = 100,
    search?: string
  ): Promise<{ oauthConfigs: unknown[]; pagination?: unknown }> {
    const params: Record<string, string | number> = { page, limit };
    if (search) params.search = search;
    const { data } = await apiClient.get<{
      oauthConfigs?: unknown[];
      pagination?: unknown;
    }>(`/api/v1/oauth/${encodeURIComponent(connectorType)}`, { params });
    return {
      oauthConfigs: data.oauthConfigs ?? [],
      pagination: data.pagination,
    };
  },

  /** Fetch one OAuth config by id (admin fallback when list entry has no embedded config). */
  async getOAuthConfig(connectorType: string, oauthConfigId: string): Promise<Record<string, unknown>> {
    const { data } = await apiClient.get<{ oauthConfig?: Record<string, unknown> }>(
      `/api/v1/oauth/${encodeURIComponent(connectorType)}/${encodeURIComponent(oauthConfigId)}`
    );
    return (data.oauthConfig ?? {}) as Record<string, unknown>;
  },

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

  /** Fetch a specific connector instance. Backend returns `{ success, connector }`. */
  async getConnectorInstance(connectorId: string): Promise<Connector> {
    const { data } = await apiClient.get<{ success: boolean; connector: Connector }>(
      `${BASE_URL}/${connectorId}`
    );
    if (!data?.connector) {
      throw new Error(`getConnectorInstance: empty response for ${connectorId}`);
    }
    return data.connector;
  },

  // ── Reindex Failed ──

  /**
   * Resync records for a connector instance.
   * `connectorType` must be the connector **type** (e.g. "Google Drive"), matching the legacy UI.
   */
  async resyncConnector(connectorId: string, connectorType: string, fullSync?: boolean) {
    if (!connectorType) {
      throw new Error('resyncConnector: connectorType is required');
    }
    const { data } = await apiClient.post(
      '/api/v1/knowledgeBase/resync/connector',
      {
        connectorName: connectorType,
        connectorId,
        ...(fullSync !== undefined ? { fullSync } : {}),
      }
    );
    return data;
  },

  /** Reindex failed (and optionally filtered) records for a connector */
  async reindexFailedConnector(
    connectorId: string,
    app: string,
    statusFilters?: string[]
  ) {
    const { data } = await apiClient.post(
      '/api/v1/knowledgeBase/reindex-failed/connector',
      {
        app,
        connectorId,
        ...(statusFilters?.length ? { statusFilters } : {}),
      }
    );
    return data;
  },

  /**
   * Complete connector OAuth in a popup: forwards to Node, which proxies the connector service.
   * Same contract as the legacy SPA `/connectors/oauth/callback/...` page.
   */
  async completeConnectorOAuthCallback(params: {
    code: string;
    state: string;
    oauthError: string | null;
    baseUrl: string;
  }): Promise<ConnectorOAuthCallbackRaw> {
    const query: Record<string, string> = {
      code: params.code,
      state: params.state,
      baseUrl: params.baseUrl,
    };
    if (params.oauthError) {
      query.error = params.oauthError;
    }
    const { data } = await apiClient.get<ConnectorOAuthCallbackRaw>('/api/v1/connectors/oauth/callback', {
      params: query,
      suppressErrorToast: true,
    });
    return data ?? {};
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
