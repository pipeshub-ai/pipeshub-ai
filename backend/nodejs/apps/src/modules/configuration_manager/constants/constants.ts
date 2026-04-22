export const storageTypes = {
  LOCAL: 'local',
  S3: 's3',
  GCP: 'gcp',
  AZURE_BLOB: 'azureBlob',
};

export const authTypes = {
  AZURE_AD: 'azureAd',
  SSO: 'sso',
  GOOGLE: 'google',
  MICROSOFT: 'microsoft',
};

export const keyValueStoreTypes = {
  REDIS: 'redis',
};

export const googleWorkspaceTypes = {
  INDIVIDUAL: 'individual',
  BUSINESS: 'business',
};

export const googleWorkspaceServiceTypes = {
  GOOGLE_DRIVE: 'googleDrive',
  GOOGLE_DOCS: 'googleDocs',
  GOOGLE_SHEETS: 'googleSheets',
  GOOGLE_SLIDES: 'googleSlides',
  GOOGLE_CALENDAR: 'googleCalendar',
  GOOGLE_CONTACTS: 'googleContacts',
  GOOGLE_MAIL: 'gmail',
};

export const aiModelsTypes = {
  OCR: 'ocr',
  EMBEDDING: 'embedding',
  SLM: 'slm',
  REASONING: 'reasoning',
  LLM: 'llm',
  MULTI_MODAL: 'multiModal',
  IMAGE_GENERATION: 'imageGeneration',
};

export const dbTypes = {
  MONGO_DB: 'mongodb',
  ARANGO_DB: 'arangodb',
};

export const aiModelRoute = `api/v1/configurationManager/internal/aiModelsConfig`;

export interface AIServiceResponse {
  statusCode: number;
  data?: any;
  msg?: string;
}

// Platform feature flags (maintainable list)
export interface PlatformFeatureFlagDef {
  key: string;
  label: string;
  description?: string;
  defaultEnabled?: boolean;
  /**
   * When true, the flag is still honoured by backend code and seeded with its
   * default in the key-value store, but it is NOT returned by
   * GET /platform/feature-flags/available and therefore never appears in the
   * Labs UI. Use for legacy / internal-only flags we don't want users to toggle.
   */
  hidden?: boolean;
}

export const PLATFORM_FEATURE_FLAGS: PlatformFeatureFlagDef[] = [
  {
    key: 'ENABLE_BETA_CONNECTORS',
    label: 'Enable Beta Connectors',
    description: 'Allow usage of beta connector integrations that may be unstable.',
    defaultEnabled: false,
    hidden: true,
  },
  {
    key: 'ENABLE_CODE_EXECUTION',
    label: 'Enable Code Execution',
    description:
      'Let agents run Python/TypeScript and SQL queries in a sandboxed environment. Disable to hide coding_sandbox and database_sandbox tools from all agents.',
    defaultEnabled: true,
  },
];