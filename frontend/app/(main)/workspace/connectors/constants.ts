// ========================================
// Connector schema field names (wire keys from registry / backend)
// ========================================

/**
 * Service account JSON credential field. Sync custom-field validation skips
 * `maxLength` for this name so large pasted JSON is not rejected by a small schema cap.
 */
export const CONNECTOR_SERVICE_ACCOUNT_JSON_FIELD_NAME = 'serviceAccountJson' as const;

// ========================================
// Connector instance operational status (backend + optimistic UI)
// ========================================

export const CONNECTOR_INSTANCE_STATUS = {
  DELETING: 'DELETING',
  SYNCING: 'SYNCING',
  FULL_SYNCING: 'FULL_SYNCING',
  IDLE: 'IDLE',
} as const;

/** `details.code` of the 409 Node returns when the Local FS owner device is not connected. */
export const LOCAL_FS_DESKTOP_OFFLINE = 'DESKTOP_OFFLINE';
/** `details.code` when no device owns the connector yet: it must be enabled from the desktop app. */
export const LOCAL_FS_DESKTOP_UNCLAIMED = 'DESKTOP_UNCLAIMED';
/** `details.code` when the enabling device differs from the connector's owner device. Toggle-on only. */
export const LOCAL_FS_DESKTOP_OWNED_BY_OTHER_DEVICE = 'DESKTOP_OWNED_BY_OTHER_DEVICE';

export const LOCAL_FS_DESKTOP_OFFLINE_TOAST_DURATION_MS = 5000;

// ========================================
// Connector sync strategy display labels
// ========================================

export const STRATEGY_LABEL_KEYS: Record<string, string> = {
  SCHEDULED: 'workspace.connectors.syncStrategies.SCHEDULED',
  MANUAL: 'workspace.connectors.syncStrategies.MANUAL',
  WEBHOOK: 'workspace.connectors.syncStrategies.WEBHOOK',
  REALTIME: 'workspace.connectors.syncStrategies.REALTIME',
};

// ========================================
// Scheduled sync interval display labels (in minutes)
// ========================================

export const INTERVAL_LABEL_KEYS: Record<number, string> = {
  5: 'workspace.connectors.syncIntervals.5',
  15: 'workspace.connectors.syncIntervals.15',
  30: 'workspace.connectors.syncIntervals.30',
  60: 'workspace.connectors.syncIntervals.60',
  240: 'workspace.connectors.syncIntervals.240',
  480: 'workspace.connectors.syncIntervals.480',
  720: 'workspace.connectors.syncIntervals.720',
  1440: 'workspace.connectors.syncIntervals.1440',
  10080: 'workspace.connectors.syncIntervals.10080',
};

// ========================================
// Scheduled sync interval options for select dropdowns
// ========================================

export const INTERVAL_OPTIONS: { labelKey: string; value: number }[] = [
  { labelKey: 'workspace.connectors.syncIntervalOptions.5', value: 5 },
  { labelKey: 'workspace.connectors.syncIntervalOptions.15', value: 15 },
  { labelKey: 'workspace.connectors.syncIntervalOptions.30', value: 30 },
  { labelKey: 'workspace.connectors.syncIntervalOptions.60', value: 60 },
  { labelKey: 'workspace.connectors.syncIntervalOptions.240', value: 240 },
  { labelKey: 'workspace.connectors.syncIntervalOptions.480', value: 480 },
  { labelKey: 'workspace.connectors.syncIntervalOptions.720', value: 720 },
  { labelKey: 'workspace.connectors.syncIntervalOptions.1440', value: 1440 },
  { labelKey: 'workspace.connectors.syncIntervalOptions.10080', value: 10080 },
];
