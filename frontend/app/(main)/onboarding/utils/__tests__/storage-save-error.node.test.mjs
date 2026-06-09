/**
 * Runnable with:
 *   node --test frontend/app/\(main\)/onboarding/utils/__tests__/storage-save-error.node.test.mjs
 */
import assert from 'node:assert/strict';
import test from 'node:test';

function extractStorageSaveErrorMessage(data) {
  if (data == null || typeof data !== 'object') {
    return null;
  }

  const body = data;
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

test('extractStorageSaveErrorMessage returns nested error message', () => {
  const message = extractStorageSaveErrorMessage({
    error: { message: 'S3 health check failed. Verify credentials.' },
  });
  assert.equal(message, 'S3 health check failed. Verify credentials.');
});

test('extractStorageSaveErrorMessage appends failed capability details from metadata', () => {
  const message = extractStorageSaveErrorMessage({
    error: { message: 'S3 health check failed.' },
    metadata: {
      s3HealthCheck: [
        { capability: 'bucketAccess', passed: true },
        { capability: 'upload', passed: false, error: 'AccessDenied' },
      ],
    },
  });
  assert.match(message ?? '', /upload: AccessDenied/);
});

test('extractStorageSaveErrorMessage returns capability summary when message is missing', () => {
  const message = extractStorageSaveErrorMessage({
    metadata: {
      s3HealthCheck: [
        { capability: 'signedUrlPut', passed: false, error: 'AccessDenied' },
      ],
    },
  });
  assert.equal(message, 'S3 health check failed (signedUrlPut: AccessDenied)');
});
