import { apiClient } from '@/lib/api';
import type {
  OrgProfileFormData,
  StorageFormData,
  SmtpFormData,
  OnboardingStatus,
  OnboardingStatusResponse,
  StorageConfigResponse,
  SmtpConfigResponse,
  OrgDetailsResponse,
  UserBackgroundSurveyResponse,
} from './types';
import { resolveS3Credentials } from './utils/s3-credentials';

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
// Step 3 — Storage — OPTIONAL
// ===============================

/**
 * GET /api/v1/configurationManager/storageConfig
 */
export async function getStorageConfig(): Promise<StorageConfigResponse> {
  const { data } = await apiClient.get<StorageConfigResponse>(
    '/api/v1/configurationManager/storageConfig'
  );

  // Backward compatibility for older API responses that used accessKeyId/region/bucketName.
  const legacy = data as StorageConfigResponse & {
    accessKeyId?: string;
    secretAccessKey?: string;
    region?: string;
    bucketName?: string;
  };

  return {
    ...data,
    s3AccessKeyId: data.s3AccessKeyId ?? legacy.accessKeyId,
    s3SecretAccessKey: data.s3SecretAccessKey ?? legacy.secretAccessKey,
    s3Region: data.s3Region ?? legacy.region,
    s3BucketName: data.s3BucketName ?? legacy.bucketName,
  };
}

/**
 * POST /api/v1/configurationManager/storageConfig
 */
export async function saveStorageConfig(
  form: StorageFormData
): Promise<{ message?: string }> {
  const body: Record<string, unknown> = { storageType: form.providerType };

  if (form.providerType === 's3') {
    // Both keys omitted tells the API to use the instance IAM role; a half-filled
    // pair is still sent so the API rejects it instead of silently switching mode.
    const credentials = resolveS3Credentials({
      accessKeyId: form.s3AccessKeyId,
      secretAccessKey: form.s3SecretAccessKey,
    });
    if (credentials.kind !== 'iamRole') {
      body.s3AccessKeyId = form.s3AccessKeyId?.trim() ?? '';
      body.s3SecretAccessKey = form.s3SecretAccessKey?.trim() ?? '';
    }
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

  const { data } = await apiClient.post<{ message?: string }>(
    '/api/v1/configurationManager/storageConfig',
    body
  );
  return data ?? {};
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
