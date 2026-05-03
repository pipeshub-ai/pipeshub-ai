// ===============================
// Onboarding Step Configuration
// ===============================

export interface OnboardingStep {
  id: OnboardingStepId;
  title: string;
  description: string;
  required: boolean;
}

export type OnboardingStepId =
  // | 'org-profile'
  | 'ai-model'
  | 'embedding-model'
  | 'storage'
  | 'smtp'
  | 'loading';

// ===============================
// Onboarding Status
// ===============================

export type OnboardingStatus = 'notConfigured' | 'configured' | 'skipped';

export interface OnboardingStatusResponse {
  status: OnboardingStatus;
}

// ===============================
// Form Data Types
// ===============================

export interface OrgProfileFormData {
  organizationName: string;
  displayName: string;
  streetAddress: string;
  country: string;
  state: string;
  city: string;
  zipCode: string;
}

// Matches backend llmProvider enum exactly
export type AiProvider =
  | 'openAI'
  | 'anthropic'
  | 'gemini'
  | 'groq'
  | 'mistral'
  | 'cohere'
  | 'xai'
  | 'fireworks'
  | 'together'
  | 'bedrock'
  | 'vertexAI'
  | 'azureOpenAI'
  | 'azureAI'
  | 'ollama'
  | 'openAICompatible'
  | '';

export interface AiModelFormData {
  provider: AiProvider;
  apiKey: string;
  model: string;
  endpoint?: string;         // azureOpenAI, azureAI, ollama, openAICompatible
  deploymentName?: string;   // azureOpenAI
  apiVersion?: string;       // azureOpenAI (optional)
  modelFriendlyName?: string;
  isReasoning: boolean;
  isMultimodal: boolean;
  contextLength?: number;
}

// Matches backend embeddingProvider enum exactly
export type EmbeddingProviderType =
  | 'default'
  | 'openAI'
  | 'azureOpenAI'
  | 'cohere'
  | 'gemini'
  | 'mistral'
  | 'voyage'
  | 'jinaAI'
  | 'together'
  | 'bedrock'
  | 'vertexAI'
  | 'ollama'
  | 'openAICompatible'
  | 'sentenceTransformers'
  | 'fastembed'
  | '';

export interface EmbeddingModelFormData {
  providerType: EmbeddingProviderType;
  apiKey: string;
  model: string;
  endpoint?: string;   // ollama, openAICompatible
  isMultimodal: boolean;
}

// gcs is not supported by the backend — only local, s3, azureBlob
export type StorageProviderType = 'local' | 's3' | 'azureBlob' | '';

export interface StorageFormData {
  providerType: StorageProviderType;
  // Amazon S3
  s3AccessKeyId?: string;
  s3SecretAccessKey?: string;
  s3Region?: string;
  s3BucketName?: string;
  // Azure Blob
  accountName?: string;
  accountKey?: string;
  containerName?: string;
  endpointProtocol?: 'https' | 'http';
  endpointSuffix?: string;
  // Local
  mountName?: string;
  baseUrl?: string;
}

export interface SmtpFormData {
  host: string;
  port: number;
  fromEmail: string;
  username: string;
  password: string;
}

// ===============================
// API Response Types
// ===============================

export interface OnboardingConfigResponse {
  steps: OnboardingStep[];
  isOnboardingActive: boolean;
}

export interface OnboardingStepSubmitResponse {
  success: boolean;
  nextStep: OnboardingStepId | null;
  message?: string;
}

export interface UserBackgroundSurveyResponse {
  success: boolean;
}

// LLM config from GET /api/v1/configurationManager/ai-models/llm
export interface LlmModelConfig {
  provider: AiProvider;
  configuration: {
    apiKey?: string;
    model?: string;
    endpoint?: string;
    deploymentName?: string;
    apiVersion?: string;
    modelFriendlyName?: string;
  };
  isDefault: boolean;
  isMultimodal: boolean;
  isReasoning: boolean;
  contextLength?: number;
}

export interface LlmConfigResponse {
  status: string;
  models: LlmModelConfig[];
}

// Embedding config from GET /api/v1/configurationManager/ai-models/embedding
export interface EmbeddingModelConfig {
  provider: EmbeddingProviderType;
  configuration: {
    apiKey?: string;
    model?: string;
    endpoint?: string;
  };
  isDefault: boolean;
  isMultimodal: boolean;
}

export interface EmbeddingConfigResponse {
  status: string;
  models: EmbeddingModelConfig[];
}

// Storage config from GET /api/v1/configurationManager/storageConfig
export interface StorageConfigResponse {
  storageType: StorageProviderType;
  s3AccessKeyId?: string;
  s3SecretAccessKey?: string;
  s3Region?: string;
  s3BucketName?: string;
  accountName?: string;
  accountKey?: string;
  containerName?: string;
  endpointProtocol?: 'https' | 'http';
  endpointSuffix?: string;
  mountName?: string;
  baseUrl?: string;
}

// SMTP config from GET /api/v1/configurationManager/smtpConfig
export interface SmtpConfigResponse {
  host: string;
  port: number;
  fromEmail: string;
  username?: string;
  password?: string;
}

// Org details from GET /api/v1/org
export interface OrgDetailsResponse {
  registeredName?: string;
  shortName?: string;
  permanentAddress?: {
    addressLine1?: string;
    city?: string;
    state?: string;
    postCode?: string;
    country?: string;
  };
}
