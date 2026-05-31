import { v4 as uuidv4 } from 'uuid';
import { S3 } from 'aws-sdk';
import AmazonS3Adapter from '../providers/s3.provider';
import { StorageError } from '../../../libs/errors/storage.errors';
import { Document } from '../types/storage.service.types';

export type S3CapabilityName =
  | 'bucketAccess'
  | 'upload'
  | 'read'
  | 'signedUrlGet'
  | 'signedUrlPut';

export interface S3CapabilityCheckResult {
  capability: S3CapabilityName;
  passed: boolean;
  error?: string;
}

export interface S3HealthCheckResult {
  success: boolean;
  checks: S3CapabilityCheckResult[];
}

export interface S3HealthCheckCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  region: string;
  bucketName: string;
}

const HEALTH_CHECK_PREFIX = '.pipeshub-health-check';

function formatError(error: unknown): string {
  if (error instanceof StorageError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function buildFailureMessage(checks: S3CapabilityCheckResult[]): string {
  const failedChecks = checks.filter((check) => !check.passed);
  const summary = failedChecks
    .map((check) => `${check.capability}: ${check.error ?? 'failed'}`)
    .join('; ');
  return `S3 health check failed. Verify credentials, bucket name, region, and IAM permissions (s3:ListBucket, s3:PutObject, s3:GetObject, s3:DeleteObject). ${summary}`;
}

export function buildS3HealthCheckErrorMessage(
  checks: S3CapabilityCheckResult[],
): string {
  return buildFailureMessage(checks);
}

export async function validateS3Capabilities(
  credentials: S3HealthCheckCredentials,
): Promise<S3HealthCheckResult> {
  const { accessKeyId, secretAccessKey, bucketName } = credentials;
  const region = (credentials.region ?? '').trim().toLowerCase();
  const checks: S3CapabilityCheckResult[] = [];
  const probeId = uuidv4();
  const testKey = `${HEALTH_CHECK_PREFIX}/${probeId}`;
  const directUploadKey = `${HEALTH_CHECK_PREFIX}/${probeId}-direct`;
  const keysToCleanup = new Set<string>();

  let adapter: AmazonS3Adapter;
  try {
    adapter = new AmazonS3Adapter({
      accessKeyId,
      secretAccessKey,
      region,
      bucket: bucketName,
    });
  } catch (error) {
    return {
      success: false,
      checks: [
        {
          capability: 'bucketAccess',
          passed: false,
          error: formatError(error),
        },
      ],
    };
  }

  const s3 = new S3({
    accessKeyId,
    secretAccessKey,
    region: region.trim().toLowerCase(),
  });

  try {
    await s3.headBucket({ Bucket: bucketName }).promise();
    checks.push({ capability: 'bucketAccess', passed: true });
  } catch (error) {
    checks.push({
      capability: 'bucketAccess',
      passed: false,
      error: formatError(error),
    });
    return { success: false, checks };
  }

  let uploadedUrl: string | undefined;

  try {
    const uploadResult = await adapter.uploadDocumentToStorageService({
      documentPath: testKey,
      buffer: Buffer.from('pipeshub-s3-health-check'),
      mimeType: 'text/plain',
      isVersioned: false,
    });
    uploadedUrl = uploadResult.data;
    keysToCleanup.add(testKey);
    checks.push({ capability: 'upload', passed: true });
  } catch (error) {
    checks.push({
      capability: 'upload',
      passed: false,
      error: formatError(error),
    });
  }

  if (uploadedUrl) {
    const probeDocument = {
      s3: { url: uploadedUrl },
      mimeType: 'text/plain',
    } as Document;

    try {
      await adapter.getBufferFromStorageService(probeDocument);
      checks.push({ capability: 'read', passed: true });
    } catch (error) {
      checks.push({
        capability: 'read',
        passed: false,
        error: formatError(error),
      });
    }

    try {
      await adapter.getSignedUrl(probeDocument);
      checks.push({ capability: 'signedUrlGet', passed: true });
    } catch (error) {
      checks.push({
        capability: 'signedUrlGet',
        passed: false,
        error: formatError(error),
      });
    }
  } else {
    checks.push(
      {
        capability: 'read',
        passed: false,
        error: 'Skipped because upload check failed',
      },
      {
        capability: 'signedUrlGet',
        passed: false,
        error: 'Skipped because upload check failed',
      },
    );
  }

  try {
    await adapter.generatePresignedUrlForDirectUpload(directUploadKey);
    checks.push({ capability: 'signedUrlPut', passed: true });
  } catch (error) {
    checks.push({
      capability: 'signedUrlPut',
      passed: false,
      error: formatError(error),
    });
  }

  for (const key of keysToCleanup) {
    try {
      await s3.deleteObject({ Bucket: bucketName, Key: key }).promise();
    } catch {
      // Best-effort cleanup; do not fail health check on delete errors.
    }
  }

  const success = checks.every((check) => check.passed);
  return { success, checks };
}
