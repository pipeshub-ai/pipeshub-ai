// Field validation interface
interface FieldValidation {
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  format?: string;
}

// Base field interface
interface BaseField {
  name: string;
  displayName: string;
  description?: string;
  required?: boolean;
  defaultValue?: any;
  options?: string[];
  validation?: FieldValidation;
  isSecret?: boolean;
}

// Auth schema field
interface AuthSchemaField extends BaseField {
  placeholder?: string;
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
}

// Auth custom field (includes JSON type)
interface AuthCustomField extends BaseField {
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
    | 'JSON';
}

// Sync custom field (includes JSON type)
interface SyncCustomField extends BaseField {
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
    | 'JSON';
}

// Filter value types
export type FilterOperator = string;
export type FilterValue = string | number | boolean | string[] | DatetimeRange | EpochDatetimeRange | null;

// Option source types
type OptionSourceType = 'manual' | 'static' | 'dynamic';

// Filter option for dynamic/static options
interface FilterOption {
  id: string;
  label: string;
}

// Filter options response
interface FilterOptionsResponse {
  success: boolean;
  options: FilterOption[];
  page: number;
  limit: number;
  hasMore: boolean;
  cursor?: string;  // Optional cursor for cursor-based pagination (API-specific)
  message?: string;
}

export interface DatetimeRange {
  start: string | number;
  end: string | number;
}

export interface EpochDatetimeRange {
  start: number | null;
  end: number | null;
}

export interface FilterValueData {
  operator: FilterOperator;
  value: FilterValue;
  type?: 'list' | 'datetime' | 'text' | 'string' | 'number' | 'boolean' | 'multiselect' | 'tags' | 'daterange' | 'datetimerange';
}

// Filter schema field
interface FilterSchemaField extends BaseField {
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
  defaultOperator?: string;
  operators?: string[];
  optionSourceType?: OptionSourceType;
}

// Filter custom field
interface FilterCustomField extends BaseField {
  fieldType:
    | 'TEXT'
    | 'SELECT'
    | 'MULTISELECT'
    | 'DATE'
    | 'DATERANGE'
    | 'NUMBER'
    | 'BOOLEAN'
    | 'TAGS'
    | 'TEXTAREA'
    | 'JSON';
}

// Documentation link interface
interface DocumentationLink {
  title: string;
  url: string;
  type: 'setup' | 'api' | 'connector' | 'pipeshub';
}

// Conditional display rule interface
interface ConditionalDisplayRule {
  field: string;
  operator: 'equals' | 'not_equals' | 'contains' | 'not_contains' | 'greater_than' | 'less_than' | 'is_empty' | 'is_not_empty';
  value?: any;
}

// Conditional display configuration interface
interface ConditionalDisplayConfig {
  [key: string]: {
    showWhen: ConditionalDisplayRule;
  };
}

// Webhook configuration interface
interface WebhookConfig {
  supported?: boolean;
  webhookUrl?: string;
  events?: string[];
  verificationToken?: string;
  secretKey?: string;
}

// Scheduled configuration interface
interface ScheduledConfig {
  intervalMinutes?: number;
  cronExpression?: string;
  timezone?: string;
  startTime?: number;
  nextTime?: number;
  endTime?: number;
  maxRepetitions?: number;
  repetitionCount?: number;
  startDateTime?: string; // ISO string format for display
}

// Realtime configuration interface
interface RealtimeConfig {
  supported?: boolean;
  connectionType?: 'WEBSOCKET' | 'SSE' | 'POLLING';
}

// Auth configuration interface
interface ConnectorAuthConfig {
  type:
    | 'OAUTH'
    | 'OAUTH_ADMIN_CONSENT'
    | 'API_TOKEN'
    | 'USERNAME_PASSWORD'
    | 'BEARER_TOKEN'
    | 'CUSTOM';
  supportedAuthTypes?: string[];
  displayRedirectUri?: boolean;
  redirectUri?: string;
  conditionalDisplay?: ConditionalDisplayConfig;
  schema: {
    fields: AuthSchemaField[];
    redirectUri?: string;
    displayRedirectUri?: boolean;
  };
  schemas?: {
    [authType: string]: {
      fields: AuthSchemaField[];
      redirectUri?: string;
      displayRedirectUri?: boolean;
    };
  };
  values: Record<string, any>;
  customFields: AuthCustomField[];
  customValues: Record<string, any>;
}

// Sync configuration interface
interface ConnectorSyncConfig {
  supportedStrategies: ('WEBHOOK' | 'SCHEDULED' | 'MANUAL' | 'REALTIME')[];
  selectedStrategy?: 'WEBHOOK' | 'SCHEDULED' | 'MANUAL' | 'REALTIME';
  webhookConfig?: WebhookConfig;
  scheduledConfig?: ScheduledConfig;
  realtimeConfig?: RealtimeConfig;
  customFields: SyncCustomField[];
  customValues: Record<string, any>;
  values?: Record<string, any>;
}

// Filter category configuration (sync/indexing)
interface FilterCategoryConfig {
  schema?: {
    fields: FilterSchemaField[];
  };
  values?: Record<string, any>;
  customFields?: FilterCustomField[];
  customValues?: Record<string, any>;
}

// Filters configuration interface
interface ConnectorFiltersConfig {
  sync?: FilterCategoryConfig;
  indexing?: FilterCategoryConfig;
  schema?: {
    fields: FilterSchemaField[];
  };
  values?: Record<string, any>;
  customFields?: FilterCustomField[];
  customValues?: Record<string, any>;
}

// Main connector configuration interface
interface ConnectorConfig {
  name: string;
  type: string;
  appGroup: string;
  appGroupId: string;
  authType: string;
  isActive: boolean;
  isConfigured: boolean;
  supportsRealtime: boolean;
  appDescription: string;
  appCategories: string[];
  iconPath: string;
  config: {
    auth: ConnectorAuthConfig;
    sync: ConnectorSyncConfig;
    filters: ConnectorFiltersConfig;
    documentationLinks?: DocumentationLink[];
  };
}

// Main connector interface matching the app schema
interface Connector {
  _key: string;
  name: string;
  type: string;
  appGroup: string;
  appGroupId?: string;
  authType: string;
  appDescription: string;
  appCategories: string[];
  iconPath: string;
  isActive: boolean;
  isConfigured: boolean;
  isAgentActive: boolean;
  isAuthenticated?: boolean;
  supportsRealtime: boolean;
  supportsSync: boolean;
  supportsAgent: boolean;
  scope: 'personal' | 'team';
  createdBy?: string;
  updatedBy?: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
}

/**
 * Connector registry entry (available connector types)
 */
interface ConnectorRegistry {
  name: string;
  type: string;
  appGroup: string;
  supportedAuthTypes: string[];  // Supported auth types (user selects one during creation)
  authType?: string;  // Optional: only exists in instance data (from database), not in registry
  appDescription: string;
  appCategories: string[];
  iconPath: string;
  supportsRealtime: boolean;
  supportsSync: boolean;
  supportsAgent: boolean;
  connectorScopes?: ('personal' | 'team')[];
  config: {
    auth: any;
    sync: any;
    filters: any;
    documentationLinks?: DocumentationLink[];
  };
}

interface IndexingStatusStats {
  NOT_STARTED: number;
  PAUSED: number;
  IN_PROGRESS: number;
  COMPLETED: number;
  FAILED: number;
  FILE_TYPE_NOT_SUPPORTED: number;
  AUTO_INDEX_OFF: number;
  EMPTY: number;
  ENABLE_MULTIMODAL_MODELS: number;
  QUEUED: number;
}

interface BasicStats {
  total: number;
  indexingStatus: IndexingStatusStats;
}

interface RecordTypeStats {
  recordType: string;
  total: number;
  indexingStatus: IndexingStatusStats;
}

// For individual Knowledge Base details
interface KnowledgeBaseStats {
  kbId: string;
  kbName: string;
  total: number;
  indexingStatus: IndexingStatusStats;
  byRecordType: RecordTypeStats[];
}

// Main connector stats data structure
interface ConnectorStatsData {
  orgId: string;
  origin: 'CONNECTOR';
  stats: BasicStats;
  byRecordType: RecordTypeStats[];
  connectorId: string;
}

interface ConnectorStatsResponse {
  success: boolean;
  message?: string; // Present when success is false
  data: ConnectorStatsData | null;
}

type ConnectorToggleType = 'sync' | 'agent';


// Export all types
export type { 
  Connector,
  ConnectorConfig,
  ConnectorRegistry,
  ConnectorAuthConfig,
  ConnectorSyncConfig,
  ConnectorFiltersConfig,
  FilterCategoryConfig,
  ScheduledConfig,
  WebhookConfig,
  RealtimeConfig,
  DocumentationLink,
  AuthSchemaField,
  AuthCustomField,
  SyncCustomField,
  FilterSchemaField,
  FilterCustomField,
  FieldValidation,
  BaseField,
  ConditionalDisplayRule,
  ConditionalDisplayConfig,
  ConnectorStatsData,
  ConnectorStatsResponse,
  ConnectorToggleType,
  FilterOption,
  FilterOptionsResponse,
  OptionSourceType
};
