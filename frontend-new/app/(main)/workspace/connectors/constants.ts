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
} as const;

// ========================================
// Connector sync strategy display labels
// ========================================

export const STRATEGY_LABELS: Record<string, string> = {
  SCHEDULED: 'Scheduled',
  MANUAL: 'Manual',
  WEBHOOK: 'Webhook',
  REALTIME: 'Real-time',
};

// ========================================
// Scheduled sync interval display labels (in minutes)
// ========================================

export const INTERVAL_LABELS: Record<number, string> = {
  5: 'Every 5 Minutes',
  15: 'Every 15 Minutes',
  30: 'Every 30 Minutes',
  60: 'Every 1 Hour',
  240: 'Every 4 Hours',
  480: 'Every 8 Hours',
  720: 'Every 12 Hours',
  1440: 'Every 1 Day',
  10080: 'Every 1 Week',
};

// ========================================
// Scheduled sync interval options for select dropdowns
// ========================================

export const INTERVAL_OPTIONS: { label: string; value: number }[] = [
  { label: '5 Minutes', value: 5 },
  { label: '15 Minutes', value: 15 },
  { label: '30 Minutes', value: 30 },
  { label: '1 Hour', value: 60 },
  { label: '4 Hours', value: 240 },
  { label: '8 Hours', value: 480 },
  { label: '12 Hours', value: 720 },
  { label: '1 Day', value: 1440 },
  { label: '1 Week', value: 10080 },
];
