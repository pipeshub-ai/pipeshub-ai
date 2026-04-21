// ========================================
// Connector page types
// ========================================

/** Scope determines whether we're viewing team or personal connectors. */
export type ConnectorScope = 'team' | 'personal';

/** Filter tabs displayed for team connectors. */
export type TeamFilterTab = 'all' | 'configured' | 'not_configured';

/** Filter tabs displayed for personal connectors. */
export type PersonalFilterTab = 'all' | 'active' | 'inactive';

/**
 * Core connector object returned by both the active-list
 * and registry endpoints.
 */
export interface Connector {
  _key?: string;
  name: string;
  type: string;
  appGroup: string;
  appDescription: string;
  appCategories: string[];
  iconPath: string;
  supportedAuthTypes: string[];
  supportsRealtime: boolean;
  supportsSync: boolean;
  supportsAgent: boolean;
  scope: string;
  /** Only present on active connectors. */
  isActive: boolean;
  isAgentActive: boolean;
  isConfigured: boolean;
  isAuthenticated: boolean;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
  createdBy?: string;
  updatedBy?: string;
  authType?: string;
  connectorInfo?: Record<string, unknown> | null;
  config?: Record<string, unknown>;
  /**
   * Transient operational status from the backend.
   * Prefer comparing to `CONNECTOR_INSTANCE_STATUS` from `./constants`; other strings may appear before the UI is updated.
   */
  status?: string | null;
}

/** API list response shape. */
export interface ConnectorListResponse {
  success: boolean;
  connectors: Connector[];
}

// ========================================
// Auth types
// ========================================

export type AuthType =
  | 'OAUTH'
  | 'OAUTH_ADMIN_CONSENT'
  | 'OAUTH_CERTIFICATE'
  | 'API_TOKEN'
  | 'USERNAME_PASSWORD'
  | 'BEARER_TOKEN'
  | 'CUSTOM'
  | 'NONE';

// ========================================
// Sync types
// ========================================

export type SyncStrategy = 'WEBHOOK' | 'SCHEDULED' | 'MANUAL' | 'REALTIME';

// ========================================
// Field types (shared across auth, sync, filter)
// ========================================

export interface FieldValidation {
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  format?: string;
}

export interface AuthSchemaField {
  name: string;
  displayName: string;
  placeholder?: string;
  description?: string;
  fieldType:
    | 'TEXT'
    | 'PASSWORD'
    | 'EMAIL'
    | 'URL'
    | 'TEXTAREA'
    | 'SELECT'
    | 'MULTISELECT'
    | 'CHECKBOX'
    | 'NUMBER'
    | 'FILE';
  required?: boolean;
  defaultValue?: unknown;
  options?: string[];
  validation?: FieldValidation;
  isSecret?: boolean;
  /**
   * Optional labeled example values rendered below the input as a compact
   * copyable note. Intended for fields where a single placeholder can't
   * carry all the variants a user may need (e.g. Azure AI endpoints).
   */
  examples?: { label: string; value: string }[];
}

export interface SyncCustomField {
  name: string;
  displayName: string;
  description?: string;
  fieldType:
    | 'TEXT'
    | 'PASSWORD'
    | 'EMAIL'
    | 'URL'
    | 'TEXTAREA'
    | 'SELECT'
    | 'MULTISELECT'
    | 'CHECKBOX'
    | 'NUMBER'
    | 'FILE'
    | 'JSON'
    | 'BOOLEAN';
  required?: boolean;
  defaultValue?: unknown;
  options?: string[];
  validation?: FieldValidation;
  isSecret?: boolean;
}

export interface FilterSchemaField {
  name: string;
  displayName: string;
  description?: string;
  fieldType?:
    | 'TEXT'
    | 'SELECT'
    | 'MULTISELECT'
    | 'DATE'
    | 'DATERANGE'
    | 'NUMBER'
    | 'BOOLEAN'
    | 'TAGS';
  filterType?: 'list' | 'datetime' | 'text' | 'string' | 'number' | 'boolean' | 'multiselect';
  category?: 'sync' | 'indexing';
  required?: boolean;
  defaultValue?: unknown;
  defaultOperator?: string;
  operators?: string[];
  optionSourceType?: 'manual' | 'static' | 'dynamic';
  options?: { id: string; label: string }[];
}

/** Union field type for SchemaFormField component */
export type SchemaField = AuthSchemaField | SyncCustomField | FilterSchemaField;

// ========================================
// Schema response structures
// ========================================

export interface DocumentationLink {
  title: string;
  url: string;
  type: 'setup' | 'api' | 'connector' | 'pipeshub';
}

export interface ConditionalDisplayRule {
  field: string;
  operator:
    | 'equals'
    | 'not_equals'
    | 'contains'
    | 'not_contains'
    | 'greater_than'
    | 'less_than'
    | 'is_empty'
    | 'is_not_empty';
  value?: unknown;
}

export interface ConditionalDisplayConfig {
  [key: string]: { showWhen: ConditionalDisplayRule };
}

export interface AuthSchemaDefinition {
  fields: AuthSchemaField[];
  redirectUri?: string;
  displayRedirectUri?: boolean;
}

export interface ConnectorAuthConfig {
  type?: string;
  supportedAuthTypes: string[];
  displayRedirectUri?: boolean;
  redirectUri?: string;
  conditionalDisplay?: ConditionalDisplayConfig;
  schema?: AuthSchemaDefinition;
  schemas?: Record<string, AuthSchemaDefinition>;
  values: Record<string, unknown>;
  customFields?: AuthSchemaField[];
  customValues?: Record<string, unknown>;
}

export interface ScheduledConfig {
  intervalMinutes?: number;
  cronExpression?: string;
  timezone?: string;
  startTime?: number;
  nextTime?: number;
  endTime?: number;
  maxRepetitions?: number;
  repetitionCount?: number;
  startDateTime?: string;
}

export interface WebhookConfig {
  supported?: boolean;
  webhookUrl?: string;
  events?: string[];
  verificationToken?: string;
  secretKey?: string;
}

export interface RealtimeConfig {
  supported?: boolean;
  connectionType?: string;
}

export interface ConnectorSyncConfig {
  supportedStrategies: SyncStrategy[];
  selectedStrategy?: SyncStrategy;
  webhookConfig?: WebhookConfig;
  scheduledConfig?: ScheduledConfig;
  realtimeConfig?: RealtimeConfig;
  customFields: SyncCustomField[];
  customValues: Record<string, unknown>;
  values?: Record<string, unknown>;
}

export interface FilterCategoryConfig {
  schema?: { fields: FilterSchemaField[] };
  values?: Record<string, unknown>;
  customFields?: FilterSchemaField[];
  customValues?: Record<string, unknown>;
}

export interface ConnectorFiltersConfig {
  sync?: FilterCategoryConfig;
  indexing?: FilterCategoryConfig;
  schema?: { fields: FilterSchemaField[] };
  values?: Record<string, unknown>;
}

export interface ConnectorSchemaResponse {
  success: boolean;
  schema: {
    iconPath: string;
    supportsRealtime: boolean;
    supportsSync: boolean;
    supportsAgent: boolean;
    documentationLinks: DocumentationLink[];
    hideConnector: boolean;
    auth: ConnectorAuthConfig;
    sync: ConnectorSyncConfig;
    filters: ConnectorFiltersConfig;
  };
}

// ========================================
// Config response
// ========================================

export interface ConnectorConfig {
  name: string;
  type: string;
  appGroup: string;
  appGroupId: string;
  authType: string;
  isActive: boolean;
  isConfigured: boolean;
  isAuthenticated?: boolean;
  supportsRealtime: boolean;
  appDescription: string;
  appCategories: string[];
  iconPath: string;
  config: {
    auth: Partial<ConnectorAuthConfig>;
    sync: Partial<ConnectorSyncConfig>;
    filters: Partial<ConnectorFiltersConfig>;
    documentationLinks?: DocumentationLink[];
  };
}

// ========================================
// Filter options (dynamic)
// ========================================

export interface FilterOptionsResponse {
  success: boolean;
  options: { id: string; label: string }[];
  page: number;
  limit: number;
  hasMore: boolean;
  cursor?: string;
}

// ========================================
// Panel form data
// ========================================

export interface PanelFormData {
  auth: Record<string, unknown>;
  sync: {
    selectedStrategy: SyncStrategy;
    scheduledConfig: {
      intervalMinutes?: number;
      startDateTime?: string;
      timezone?: string;
    };
    customValues: Record<string, unknown>;
  };
  filters: {
    sync: Record<string, unknown>;
    indexing: Record<string, unknown>;
  };
}

// ========================================
// Panel tab + view types
// ========================================

export type PanelTab = 'authenticate' | 'authorize' | 'configure';
export type PanelView = 'tabs' | 'select-records';
export type AuthCardState = 'empty' | 'success' | 'failed';

// ========================================
// Instance page types
// ========================================

/**
 * Sync status for a connector instance.
 *
 * Derived states (via deriveSyncStatus — always reachable):
 *   ready_to_sync | syncing | sync_complete | sync_failed | sync_disabled | auth_incomplete
 *
 * Requires future backend signal (not reachable today via derivation):
 *   detecting_records — needs a dedicated API field before stats are available
 */
export type InstanceSyncStatus =
  | 'ready_to_sync'
  | 'detecting_records'
  | 'syncing'
  | 'sync_complete'
  | 'sync_failed'
  | 'auth_incomplete'
  | 'sync_disabled';

/** Instance card data (enriched connector with sync info) */
export interface ConnectorInstance extends Connector {
  /** Sync status */
  syncStatus?: InstanceSyncStatus;
  /** Number of records/channels selected */
  recordsSelected?: number;
  /** Sync strategy label */
  syncStrategy?: string;
  /** Sync interval label */
  syncInterval?: string;
  /** User who enabled the connector */
  enabledBy?: { name: string; avatar?: string };
  /** Last synced timestamp */
  lastSynced?: string | null;
  /** Sync progress info */
  syncProgress?: {
    total?: number;
    synced?: number;
    percentage?: number;
    label?: string;
  };
}

/** Management panel tab */
export type InstancePanelTab = 'overview' | 'settings';

/** Indexing status counters from the stats API */
export interface IndexingStatus {
  NOT_STARTED: number;
  IN_PROGRESS: number;
  COMPLETED: number;
  FAILED: number;
  FILE_TYPE_NOT_SUPPORTED: number;
  AUTO_INDEX_OFF: number;
  ENABLE_MULTIMODAL_MODELS: number;
  EMPTY: number;
  QUEUED: number;
  PAUSED: number;
}

/** Stats breakdown by record type */
export interface RecordTypeStats {
  recordType: string;
  total: number;
  indexingStatus: IndexingStatus;
}

/** Response from GET /api/v1/knowledgeBase/stats/{connectorId} */
export interface ConnectorStatsResponse {
  success: boolean;
  data: {
    orgId: string;
    connectorId: string;
    origin: string;
    stats: {
      total: number;
      indexingStatus: IndexingStatus;
    };
    byRecordType: RecordTypeStats[];
  };
}

/** Records status counters (derived from stats for UI display) */
export interface RecordsStatus {
  total: number;
  completed: number;
  failed: number;
  unsupported: number;
  inProgress: number;
  notStarted: number;
  autoIndexOff: number;
  queued: number;
}

/** Indexed record item */
export interface IndexedRecord {
  id: string;
  name: string;
}

/** Instance overview data */
export interface InstanceOverview {
  recordsStatus: RecordsStatus;
  indexedRecords: IndexedRecord[];
  totalSelected: number;
  personalRecordsIndexed?: number;
}
