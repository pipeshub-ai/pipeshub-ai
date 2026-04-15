import { apiClient } from '@/lib/api';
import type {
  OrgProfileFormData,
  AiModelFormData,
  EmbeddingModelFormData,
  StorageFormData,
  SmtpFormData,
  OnboardingStatus,
  OnboardingStatusResponse,
  LlmConfigResponse,
  EmbeddingConfigResponse,
  StorageConfigResponse,
  SmtpConfigResponse,
  OrgDetailsResponse,
  UserBackgroundSurveyResponse,
} from './types';

// ===============================
// Onboarding Status Gate
// ===============================

/**
 * GET /api/v1/org/onboarding-status
 * Returns { status: 'notConfigured' | 'configured' | 'skipped' }
 */
export async function getOnboardingStatus(): Promise<OnboardingStatusResponse> {
  const { data } = await apiClient.get<OnboardingStatusResponse>(
    '/api/v1/org/onboarding-status'
  );
  return data;
}

/**
 * PUT /api/v1/org/onboarding-status
 * Call with { status: 'configured' } on finish or { status: 'skipped' } on dismiss.
 */
export async function updateOnboardingStatus(
  status: Exclude<OnboardingStatus, 'notConfigured'>
): Promise<void> {
  await apiClient.put('/api/v1/org/onboarding-status', { status });
}

// ===============================
// Step 0 — Org Profile
// ===============================

/**
 * GET /api/v1/org
 * Fetch existing org details to pre-populate the org-profile form.
 */
export async function getOrgDetails(): Promise<OrgDetailsResponse> {
  const { data } = await apiClient.get<OrgDetailsResponse>('/api/v1/org');
  return data;
}

/**
 * PUT /api/v1/org
 * Update org name / address for an existing org (created at sign-up).
 */
export async function updateOrgProfile(data: OrgProfileFormData): Promise<void> {
  await apiClient.put('/api/v1/org', {
    registeredName: data.organizationName,
    shortName: data.displayName,
    permanentAddress: {
      addressLine1: data.streetAddress,
      city: data.city,
      state: data.state,
      postCode: data.zipCode,
      country: data.country,
    },
  });
}

// ===============================
// Step 1 — LLM (AI Model) — REQUIRED
// ===============================

/**
 * GET /api/v1/configurationManager/ai-models/llm
 */
export async function getLlmConfig(): Promise<LlmConfigResponse> {
  const { data } = await apiClient.get<LlmConfigResponse>(
    '/api/v1/configurationManager/ai-models/llm'
  );
  return data;
}

/**
 * POST /api/v1/configurationManager/ai-models/providers
 * modelType: 'llm'
 */
export async function saveLlmConfig(form: AiModelFormData): Promise<void> {
  const configuration: Record<string, unknown> = {
    apiKey: form.apiKey,
    model: form.model,
  };
  if (form.endpoint) configuration.endpoint = form.endpoint;
  if (form.deploymentName) configuration.deploymentName = form.deploymentName;
  if (form.apiVersion) configuration.apiVersion = form.apiVersion;
  if (form.modelFriendlyName) configuration.modelFriendlyName = form.modelFriendlyName;

  await apiClient.post('/api/v1/configurationManager/ai-models/providers', {
    modelType: 'llm',
    provider: form.provider,
    configuration,
    isMultimodal: form.isMultimodal,
    isReasoning: form.isReasoning,
    ...(form.contextLength ? { contextLength: form.contextLength } : {}),
    isDefault: true,
  });
}

// ===============================
// Step 2 — Embedding Model — OPTIONAL
// ===============================

/**
 * GET /api/v1/configurationManager/ai-models/embedding
 */
export async function getEmbeddingConfig(): Promise<EmbeddingConfigResponse> {
  const { data } = await apiClient.get<EmbeddingConfigResponse>(
    '/api/v1/configurationManager/ai-models/embedding'
  );
  return data;
}

/**
 * POST /api/v1/configurationManager/ai-models/providers
 * modelType: 'embedding'
 * If providerType === 'default', skip this call (system embeddings are used).
 */
export async function saveEmbeddingConfig(form: EmbeddingModelFormData): Promise<void> {
  if (form.providerType === 'default') return;

  const configuration: Record<string, unknown> = {
    apiKey: form.apiKey,
    model: form.model,
  };
  if (form.endpoint) configuration.endpoint = form.endpoint;

  await apiClient.post('/api/v1/configurationManager/ai-models/providers', {
    modelType: 'embedding',
    provider: form.providerType,
    configuration,
    isMultimodal: form.isMultimodal,
    isDefault: true,
  });
}

// ===============================
// Step 3 — Storage — OPTIONAL
// ===============================

/**
 * GET /api/v1/configurationManager/storageConfig
 */
export async function getStorageConfig(): Promise<StorageConfigResponse> {
  const { data } = await apiClient.get<StorageConfigResponse>(
    '/api/v1/configurationManager/storageConfig'
  );
  return data;
}

/**
 * POST /api/v1/configurationManager/storageConfig
 */
export async function saveStorageConfig(form: StorageFormData): Promise<void> {
  const body: Record<string, unknown> = { storageType: form.providerType };

  if (form.providerType === 's3') {
    body.s3AccessKeyId = form.s3AccessKeyId;
    body.s3SecretAccessKey = form.s3SecretAccessKey;
    body.s3Region = form.s3Region;
    body.s3BucketName = form.s3BucketName;
  } else if (form.providerType === 'azureBlob') {
    body.accountName = form.accountName;
    body.accountKey = form.accountKey;
    body.containerName = form.containerName;
    if (form.endpointProtocol) body.endpointProtocol = form.endpointProtocol;
    if (form.endpointSuffix) body.endpointSuffix = form.endpointSuffix;
  } else {
    // local
    if (form.mountName) body.mountName = form.mountName;
    if (form.baseUrl) body.baseUrl = form.baseUrl;
  }

  await apiClient.post('/api/v1/configurationManager/storageConfig', body);
}

// ===============================
// Step 4 — SMTP — OPTIONAL
// ===============================

/**
 * GET /api/v1/configurationManager/smtpConfig
 */
export async function getSmtpConfig(): Promise<SmtpConfigResponse> {
  const { data } = await apiClient.get<SmtpConfigResponse>(
    '/api/v1/configurationManager/smtpConfig'
  );
  return data;
}

/**
 * POST /api/v1/configurationManager/smtpConfig
 */
export async function saveSmtpConfig(form: SmtpFormData): Promise<void> {
  await apiClient.post('/api/v1/configurationManager/smtpConfig', {
    host: form.host,
    port: form.port,
    fromEmail: form.fromEmail,
    ...(form.username ? { username: form.username } : {}),
    ...(form.password ? { password: form.password } : {}),
  });
}

// ===============================
// User Background Survey
// ===============================

/**
 * Submit the user background survey role selection.
 */
export async function submitUserBackgroundSurvey(
  role: string
): Promise<UserBackgroundSurveyResponse> {
  try {
    const { data } = await apiClient.post<UserBackgroundSurveyResponse>(
      '/api/v1/onboarding/user-background',
      { role }
    );
    return data;
  } catch {
    // Non-critical — dismiss silently, but report failure accurately
    return { success: false };
  }
}
