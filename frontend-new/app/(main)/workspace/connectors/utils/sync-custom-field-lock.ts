import type { ConnectorConfig, SyncCustomField } from '../types';

/**
 * Matches legacy `sync-section.tsx`: disable when `field.nonEditable` and the instance
 * already has a persisted value (`config.sync.values[name]` is truthy). Also checks
 * `config.sync.customValues[name]` so APIs that only nest saved sync fields there still lock.
 */
export function isNonEditableSyncFieldLocked(
  field: SyncCustomField,
  connectorConfig: ConnectorConfig | null
): boolean {
  if (!field.nonEditable) return false;
  const sync = connectorConfig?.config?.sync;
  if (!sync) return false;

  const raw =
    (sync.values as Record<string, unknown> | undefined)?.[field.name] ??
    (sync.customValues as Record<string, unknown> | undefined)?.[field.name];

  return Boolean(raw);
}
