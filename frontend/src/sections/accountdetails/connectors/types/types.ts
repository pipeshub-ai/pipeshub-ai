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

// Filter schema field
interface FilterSchemaField extends BaseField {
  fieldType:
    | 'TEXT'
    | 'SELECT'
    | 'MULTISELECT'
    | 'DATE'
    | 'DATERANGE'
    | 'NUMBER'
    | 'BOOLEAN'
    | 'TAGS';
  operators?: (
    | 'EQUALS'
    | 'NOT_EQUALS'
    | 'CONTAINS'
    | 'NOT_CONTAINS'
    | 'STARTS_WITH'
    | 'ENDS_WITH'
    | 'GREATER_THAN'
    | 'LESS_THAN'
    | 'IN'
    | 'NOT_IN'
  )[];
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
  type: 'setup' | 'api' | 'connector';
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
  displayRedirectUri?: boolean;
  redirectUri?: string;
  conditionalDisplay?: ConditionalDisplayConfig;
  schema: {
    fields: AuthSchemaField[];
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

// Filters configuration interface
interface ConnectorFiltersConfig {
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
  _key?: string;
  name: string;
  type: string;
  appGroup: string;
  appGroupId: string;
  authType: string;
  appDescription: string;
  appCategories: string[];
  iconPath: string;
  isActive: boolean;
  isConfigured: boolean;
  supportsRealtime: boolean;
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
  authType: string;
  appDescription: string;
  appCategories: string[];
  iconPath: string;
  supportsRealtime: boolean;
  supportsSync: boolean;
  config: {
    auth: any;
    sync: any;
    filters: any;
    documentationLinks?: DocumentationLink[];
  };
}

interface IndexingStatusStats {
  NOT_STARTED: number;
  IN_PROGRESS: number;
  COMPLETED: number;
  FAILED: number;
  FILE_TYPE_NOT_SUPPORTED: number;
  AUTO_INDEX_OFF: number;
}

interface BasicStats {
  total: number;
  indexing_status: IndexingStatusStats;
}

interface RecordTypeStats {
  record_type: string;
  total: number;
  indexing_status: IndexingStatusStats;
}

// For individual Knowledge Base details
interface KnowledgeBaseStats {
  kb_id: string;
  kb_name: string;
  total: number;
  indexing_status: IndexingStatusStats;
  by_record_type: RecordTypeStats[];
}

// Main connector stats data structure
interface ConnectorStatsData {
  org_id: string;
  connector: Connector; // "KNOWLEDGE_BASE" or specific connector name like "GOOGLE_DRIVE"
  origin: 'CONNECTOR';
  stats: BasicStats;
  by_record_type: RecordTypeStats[];
  // knowledge_bases?: KnowledgeBaseStats[]; // Only present for Knowledge Base queries
  connectorId?: string;
}

interface ConnectorStatsResponse {
  success: boolean;
  message?: string; // Present when success is false
  data: ConnectorStatsData | null;
}


// Export all types
export type { 
  Connector,
  ConnectorConfig,
  ConnectorRegistry,
  ConnectorAuthConfig,
  ConnectorSyncConfig,
  ConnectorFiltersConfig,
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
  ConnectorStatsResponse
};
