// OpenAI specific fields
export interface OpenAILlmFormValues {
  modelType: 'openai';
  clientId: string;
  apiKey: string;
  model: string;
}

export interface GeminiLlmFormValues {
  modelType: 'gemini';
  // clientId: string;
  apiKey: string;
  model: string;
}

export interface AnthropicLlmFormValues {
  modelType: 'anthropic';
  // clientId: string;
  apiKey: string;
  model: string;
}

// Azure OpenAI specific fields
export interface AzureLlmFormValues {
  modelType: 'azure';
  endpoint: string;
  apiKey: string;
  deploymentName: string;
  model: string;
}

// Open AI Compatible specific fields
export interface OpenAICompatibleLlmFormValues {
  modelType: 'openAICompatible';
  endpoint: string;
  apiKey: string;
  model: string;
}

// Embedding configuration
export interface EmbeddingFormValues {
  modelType: 'openai' | 'azureOpenAI' | 'sentenceTransformers' | 'default' | 'gemini' | 'cohere';
  apiKey?: string;
  model: string;
  endpoint?: string; // Only required for Azure
}

// Union type for LLM form values
export type LlmFormValues =
  | OpenAILlmFormValues
  | AzureLlmFormValues
  | GeminiLlmFormValues
  | AnthropicLlmFormValues
  | OpenAICompatibleLlmFormValues;

export const storageTypes = {
  LOCAL: 'local',
  S3: 's3',
  AZURE_BLOB: 'azureBlob',
} as const;

export type StorageType = (typeof storageTypes)[keyof typeof storageTypes];

// Base storage configuration
export interface BaseStorageFormValues {
  storageType: StorageType;
}

// S3 storage configuration
export interface S3StorageFormValues extends BaseStorageFormValues {
  storageType: typeof storageTypes.S3;
  s3AccessKeyId: string;
  s3SecretAccessKey: string;
  s3Region: string;
  s3BucketName: string;
}

// Azure Blob storage configuration - Make endpointProtocol and endpointSuffix non-optional
export interface AzureBlobStorageFormValues extends BaseStorageFormValues {
  storageType: typeof storageTypes.AZURE_BLOB;
  endpointProtocol: 'http' | 'https'; // Remove optional
  accountName: string;
  accountKey: string;
  endpointSuffix: string; // Remove optional
  containerName: string;
}

// Local storage configuration
export interface LocalStorageFormValues extends BaseStorageFormValues {
  storageType: typeof storageTypes.LOCAL;
  mountName?: string;
  baseUrl?: string;
}

// Combined storage form values type
export type StorageFormValues =
  | S3StorageFormValues
  | AzureBlobStorageFormValues
  | LocalStorageFormValues;

// Connector form values
export interface ConnectorFormValues {
  googleWorkspace: {
    serviceCredentials: string;
    clientId?: string;
    clientEmail?: string;
    privateKey?: string;
    projectId?: string;
    clientSecret?: string;
    redirectUri?: string;
    adminEmail?: string;
    enableRealTimeUpdates?: boolean;
    topicName?: string;
  };
}

// SMTP form values
export interface SmtpFormValues {
  host: string;
  port: number;
  username?: string;
  password?: string;
  fromEmail: string;
}
