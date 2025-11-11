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

export const messageBrokerTypes = {
  KAFKA: 'kafka',
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
}

export const PLATFORM_FEATURE_FLAGS: PlatformFeatureFlagDef[] = [
  {
    key: 'ENABLE_BETA_CONNECTORS',
    label: 'Enable Beta Connectors',
    description: 'Allow usage of beta connector integrations that may be unstable.',
    defaultEnabled: false,
  },
];