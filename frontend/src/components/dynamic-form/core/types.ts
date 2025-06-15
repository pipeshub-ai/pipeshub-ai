// ===================================================================
// 📁 src/entities/dynamic-forms/core/types.ts
// ===================================================================

// BASE TYPES
export interface BaseFormValues {
  modelType?: string;
  providerType: string;
  _provider?: string;
}

export interface LlmFormValues extends BaseFormValues {
  apiKey?: string;
  model?: string;
  endpoint?: string;
  deploymentName?: string;
}

export interface EmbeddingFormValues extends BaseFormValues {
  apiKey?: string;
  model?: string;
  endpoint?: string;
}

export interface StorageFormValues extends BaseFormValues {
  // S3 fields
  s3AccessKeyId?: string;
  s3SecretAccessKey?: string;
  s3Region?: string;
  s3BucketName?: string;
  // Azure Blob fields
  accountName?: string;
  accountKey?: string;
  containerName?: string;
  endpointProtocol?: 'http' | 'https';
  endpointSuffix?: string;
  // Local storage fields
  mountName?: string;
  baseUrl?: string;
}

export interface UrlFormValues extends BaseFormValues {
  frontendUrl?: string;
  connectorUrl?: string;
}

export interface SmtpFormValues extends BaseFormValues {
  host?: string;
  port?: number;
  username?: string;
  password?: string;
  fromEmail?: string;
}


export interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}


export type AnyFormValues = LlmFormValues | EmbeddingFormValues | StorageFormValues | UrlFormValues | SmtpFormValues;
