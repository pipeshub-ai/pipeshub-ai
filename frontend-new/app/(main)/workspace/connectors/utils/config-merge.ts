// ========================================
// Config ↔ Schema merging + form init
// ========================================

import type {
  ConnectorConfig,
  ConnectorSchemaResponse,
  ConnectorAuthConfig,
  ConnectorSyncConfig,
  ConnectorFiltersConfig,
  DocumentationLink,
  AuthSchemaField,
  PanelFormData,
  SyncStrategy,
} from '../types';

/** The merged structure used to initialize form state. */
export interface MergedConfig {
  config: {
    documentationLinks: DocumentationLink[];
    auth: ConnectorAuthConfig;
    sync: ConnectorSyncConfig;
    filters: ConnectorFiltersConfig;
  };
}

/**
 * Extract auth field values from the config response.
 *
 * The API may return field values in one of two shapes:
 *   1. Nested:  config.auth.values = { clientId: '...', ... }
 *   2. Flat:    config.auth = { clientId: '...', authType: '...', connectorType: '...' }
 *
 * We prefer (1) when present. For (2), we use the schema field names as an
 * allowlist so we never accidentally treat API metadata as form values.
 */
function extractAuthValues(
  configAuth: Partial<ConnectorAuthConfig> | undefined,
  schema: ConnectorSchemaResponse['schema'],
  resolvedAuthType: string
): Record<string, unknown> {
  if (!configAuth) return {};

  // Case 1 — values sub-object exists and is non-empty
  if (configAuth.values && Object.keys(configAuth.values).length > 0) {
    return { ...configAuth.values };
  }

  // Case 2 — flat auth object: use schema field names as an allowlist
  const authSchema =
    schema.auth?.schemas?.[resolvedAuthType] ||
    schema.auth?.schema;
  const fieldNames = (authSchema?.fields ?? []).map((f) => f.name);

  if (fieldNames.length > 0) {
    const flat = configAuth as Record<string, unknown>;
    const result: Record<string, unknown> = {};
    for (const key of fieldNames) {
      if (key in flat) result[key] = flat[key];
    }
    return result;
  }

  return {};
}

/**
 * Overlay saved config values onto the schema structure.
 * Schema provides field definitions; config provides saved values.
 */
export function mergeConfigWithSchema(
  configResponse: ConnectorConfig | null,
  schemaResponse: ConnectorSchemaResponse['schema']
): MergedConfig {
  const storedAuthType =
    configResponse?.config?.auth?.type ||
    configResponse?.authType ||
    schemaResponse.auth?.supportedAuthTypes?.[0] ||
    '';

  const authValues = extractAuthValues(
    configResponse?.config?.auth,
    schemaResponse,
    storedAuthType
  );

  // Sync custom values: prefer nested customValues, fall back to schema-guided flat extraction
  const rawSyncCustomValues = configResponse?.config?.sync?.customValues;
  const syncCustomValues: Record<string, unknown> =
    rawSyncCustomValues && Object.keys(rawSyncCustomValues).length > 0
      ? { ...rawSyncCustomValues }
      : (() => {
          const syncFieldNames = (schemaResponse.sync?.customFields ?? []).map((f) => f.name);
          const flat = (configResponse?.config?.sync ?? {}) as Record<string, unknown>;
          const result: Record<string, unknown> = {};
          for (const key of syncFieldNames) {
            if (key in flat) result[key] = flat[key];
          }
          return result;
        })();

  // Sync filter values: prefer nested values, fall back to schema-guided flat extraction
  const rawSyncFilterValues = configResponse?.config?.filters?.sync?.values;
  const syncFilterValues: Record<string, unknown> =
    rawSyncFilterValues && Object.keys(rawSyncFilterValues).length > 0
      ? { ...rawSyncFilterValues }
      : (() => {
          const filterFieldNames = (
            schemaResponse.filters?.sync?.schema?.fields ?? []
          ).map((f) => f.name);
          const flat = (configResponse?.config?.filters?.sync ?? {}) as Record<string, unknown>;
          const result: Record<string, unknown> = {};
          for (const key of filterFieldNames) {
            if (key in flat) result[key] = flat[key];
          }
          return result;
        })();

  const rawIndexingFilterValues = configResponse?.config?.filters?.indexing?.values;
  const indexingFilterValues: Record<string, unknown> =
    rawIndexingFilterValues && Object.keys(rawIndexingFilterValues).length > 0
      ? { ...rawIndexingFilterValues }
      : (() => {
          const filterFieldNames = (
            schemaResponse.filters?.indexing?.schema?.fields ?? []
          ).map((f) => f.name);
          const flat = (configResponse?.config?.filters?.indexing ?? {}) as Record<string, unknown>;
          const result: Record<string, unknown> = {};
          for (const key of filterFieldNames) {
            if (key in flat) result[key] = flat[key];
          }
          return result;
        })();

  return {
    config: {
      documentationLinks: schemaResponse.documentationLinks || [],
      auth: {
        ...schemaResponse.auth,
        type: storedAuthType,
        values: authValues,
      },
      sync: {
        ...schemaResponse.sync,
        selectedStrategy:
          configResponse?.config?.sync?.selectedStrategy ||
          schemaResponse.sync?.supportedStrategies?.[0] ||
          'MANUAL',
        scheduledConfig: {
          ...schemaResponse.sync?.scheduledConfig,
          ...configResponse?.config?.sync?.scheduledConfig,
        },
        customValues: { ...schemaResponse.sync?.customValues, ...syncCustomValues },
      },
      filters: {
        sync: {
          ...schemaResponse.filters?.sync,
          values: { ...schemaResponse.filters?.sync?.values, ...syncFilterValues },
        },
        indexing: {
          ...schemaResponse.filters?.indexing,
          values: { ...schemaResponse.filters?.indexing?.values, ...indexingFilterValues },
        },
      },
    },
  };
}

/**
 * Initialize form data from the merged config.
 * Sets default values for fields that have no saved value.
 */
export function initializeFormData(
  merged: MergedConfig,
  selectedAuthType?: string
): PanelFormData {
  const authType =
    selectedAuthType || merged.config.auth?.type || '';

  // Get the auth schema for the selected auth type
  const authSchema =
    merged.config.auth?.schemas?.[authType] ||
    merged.config.auth?.schema ||
    { fields: [] };
  const fields: AuthSchemaField[] = authSchema.fields || [];

  // Build auth data with defaults
  const authData: Record<string, unknown> = { ...merged.config.auth?.values };
  for (const field of fields) {
    if (authData[field.name] === undefined) {
      if (field.defaultValue !== undefined) {
        authData[field.name] = field.defaultValue;
      } else if (
        ['TEXT', 'PASSWORD', 'EMAIL', 'URL', 'TEXTAREA'].includes(field.fieldType)
      ) {
        authData[field.name] = '';
      }
    }
  }

  // Build sync data with defaults
  const syncConfig = merged.config.sync;
  const syncCustomValues: Record<string, unknown> = { ...syncConfig?.customValues };
  for (const field of syncConfig?.customFields || []) {
    if (syncCustomValues[field.name] === undefined && field.defaultValue !== undefined) {
      // Coerce default value to the proper JS type based on field type
      if (field.fieldType === 'NUMBER') {
        const num = Number(field.defaultValue);
        syncCustomValues[field.name] = isNaN(num) ? field.defaultValue : num;
      } else if (field.fieldType === 'BOOLEAN') {
        syncCustomValues[field.name] =
          field.defaultValue === 'true' || field.defaultValue === true;
      } else {
        syncCustomValues[field.name] = field.defaultValue;
      }
    }
  }

  // Build filter data
  const syncFilterValues: Record<string, unknown> = {
    ...merged.config.filters?.sync?.values,
  };
  const indexingFilterValues: Record<string, unknown> = {
    ...merged.config.filters?.indexing?.values,
  };

  return {
    auth: authData,
    sync: {
      selectedStrategy: (syncConfig?.selectedStrategy || 'MANUAL') as SyncStrategy,
      scheduledConfig: {
        intervalMinutes: syncConfig?.scheduledConfig?.intervalMinutes || 60,
        startDateTime: syncConfig?.scheduledConfig?.startDateTime,
        timezone: syncConfig?.scheduledConfig?.timezone || 'UTC',
      },
      customValues: syncCustomValues,
    },
    filters: {
      sync: syncFilterValues,
      indexing: indexingFilterValues,
    },
  };
}
