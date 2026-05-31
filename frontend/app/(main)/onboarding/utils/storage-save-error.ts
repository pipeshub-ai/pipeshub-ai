interface ApiErrorBody {
  message?: string;
  error?: string | { message?: string };
  metadata?: {
    s3HealthCheck?: Array<{
      capability: string;
      passed: boolean;
      error?: string;
    }>;
  };
}

/**
 * Extracts a user-facing message from storage config save failures,
 * including per-capability S3 health-check details when present.
 */
export function extractStorageSaveErrorMessage(data: unknown): string | null {
  if (data == null || typeof data !== 'object') {
    return null;
  }

  const body = data as ApiErrorBody;
  const nestedError =
    typeof body.error === 'string'
      ? body.error
      : body.error && typeof body.error === 'object'
        ? body.error.message
        : undefined;

  const baseMessage =
    (typeof body.message === 'string' && body.message.trim()) ||
    (typeof nestedError === 'string' && nestedError.trim()) ||
    null;

  const failedChecks =
    body.metadata?.s3HealthCheck?.filter((check) => !check.passed) ?? [];

  if (failedChecks.length === 0) {
    return baseMessage;
  }

  const details = failedChecks
    .map((check) => `${check.capability}: ${check.error ?? 'failed'}`)
    .join('; ');

  if (baseMessage) {
    return `${baseMessage} (${details})`;
  }

  return `S3 health check failed (${details})`;
}
