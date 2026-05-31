import { v4 as uuidv4 } from 'uuid';
import { S3 } from 'aws-sdk';
import AmazonS3Adapter from '../providers/s3.provider';
import { StorageError } from '../../../libs/errors/storage.errors';
import { Document } from '../types/storage.service.types';
import { Logger } from '../../../libs/services/logger.service';

const logger = Logger.getInstance({ service: 'S3HealthCheck' });

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
    const underlying = error.metadata?.originalError;
    if (underlying) {
      return `${error.message}: ${underlying}`;
    }
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
  return `S3 health check failed. Verify credentials, bucket name, region, and IAM permissions (s3:PutObject, s3:GetObject, s3:DeleteObject). ${summary}`;
}

export function buildS3HealthCheckErrorMessage(
  checks: S3CapabilityCheckResult[],
): string {
  return buildFailureMessage(checks);
}

/**
 * Classifies an upload/bucket error to distinguish a missing bucket from a
 * permissions problem so the failure is reported under the right capability.
 * A NoSuchBucket error is a bucket-access problem; everything else is an
 * upload/write-permission problem.
 */
function classifyUploadError(error: unknown): S3CapabilityName {
  const msg = formatError(error).toLowerCase();
  if (
    msg.includes('nosuchbucket') ||
    msg.includes('no such bucket') ||
    msg.includes('bucket is not valid') ||
    msg.includes('bucket does not exist')
  ) {
    return 'bucketAccess';
  }
  return 'upload';
}

export async function validateS3Capabilities(
  credentials: S3HealthCheckCredentials,
): Promise<S3HealthCheckResult> {
  const { accessKeyId, secretAccessKey } = credentials;
  const region = (credentials.region ?? '').trim().toLowerCase();
  const bucketName = (credentials.bucketName ?? '').trim();
  const checks: S3CapabilityCheckResult[] = [];
  const probeId = uuidv4();
  const testKey = `${HEALTH_CHECK_PREFIX}/${probeId}`;
  const directUploadKey = `${HEALTH_CHECK_PREFIX}/${probeId}-direct`;
  const keysToCleanup = new Set<string>();

  logger.info('Starting S3 health check', {
    bucketName,
    bucketNameLength: bucketName?.length,
    region,
  });

  // Single S3 client used for cleanup only (best-effort, no IAM check needed).
  const s3 = new S3({ accessKeyId, secretAccessKey, region, s3ForcePathStyle: true });

  let adapter: AmazonS3Adapter;
  try {
    adapter = new AmazonS3Adapter({
      accessKeyId,
      secretAccessKey,
      region,
      bucket: bucketName,
    });
  } catch (error) {
    logger.error('S3 adapter initialization failed', { error: formatError(error) });
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

  // The upload check validates bucket existence and write access — no
  // separate HeadBucket call is needed, avoiding the s3:ListBucket IAM action.
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
    checks.push({ capability: 'bucketAccess', passed: true });
    checks.push({ capability: 'upload', passed: true });
    logger.info('S3 upload check passed', { uploadedUrl });
  } catch (error) {
    const failedCapability = classifyUploadError(error);
    logger.error('S3 upload check failed', { capability: failedCapability, error: formatError(error) });
    checks.push({
      capability: 'bucketAccess',
      passed: failedCapability !== 'bucketAccess',
      error: failedCapability === 'bucketAccess' ? formatError(error) : undefined,
    });
    checks.push({
      capability: 'upload',
      passed: false,
      error: failedCapability === 'upload' ? formatError(error) : 'Skipped because bucket was not found',
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
      logger.info('S3 read check passed');
    } catch (error) {
      logger.error('S3 read check failed', { error: formatError(error) });
      checks.push({
        capability: 'read',
        passed: false,
        error: formatError(error),
      });
    }

    try {
      await adapter.getSignedUrl(probeDocument);
      checks.push({ capability: 'signedUrlGet', passed: true });
      logger.info('S3 signedUrlGet check passed');
    } catch (error) {
      logger.error('S3 signedUrlGet check failed', { error: formatError(error) });
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
    logger.info('S3 signedUrlPut check passed');
  } catch (error) {
    logger.error('S3 signedUrlPut check failed', { error: formatError(error) });
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
  logger.info('S3 health check completed', { success, checks });
  return { success, checks };
}
