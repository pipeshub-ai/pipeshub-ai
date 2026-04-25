import type { ConnectorConfig, SyncCustomField } from '../types';

/**
 * Matches legacy `sync-section.tsx`: disable when `field.nonEditable` and the instance
 * already has a persisted value in `config.sync.values[name]` or `config.sync.customValues[name]`.
 * Uses presence checks (not truthiness) so `false` and `0` still lock BOOLEAN/NUMBER fields.
 * Empty string and empty arrays are treated as no persisted value (same as legacy for []).
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

  if (raw === undefined || raw === null) return false;
  if (raw === '') return false;
  if (typeof raw === 'boolean' || typeof raw === 'number') return true;
  if (Array.isArray(raw)) return raw.length > 0;
  return Boolean(raw);
}
