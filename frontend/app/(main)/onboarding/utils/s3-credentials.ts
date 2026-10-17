'use client';

export type S3CredentialResolution =
  | { kind: 'explicit'; accessKeyId: string; secretAccessKey: string }
  | { kind: 'iamRole' }
  | { kind: 'partial'; missingField: 'accessKeyId' | 'secretAccessKey' };

/**
 * Mirrors resolveS3Credentials in the Node API: the pair is atomic, and
 * omitting both is what selects the EC2/ECS instance IAM role. Half a pair is
 * surfaced here so the user sees it before the save round-trip.
 */
export function resolveS3Credentials(credentials: {
  accessKeyId?: string | null;
  secretAccessKey?: string | null;
}): S3CredentialResolution {
  const accessKeyId = credentials.accessKeyId?.trim() ?? '';
  const secretAccessKey = credentials.secretAccessKey?.trim() ?? '';

  if (accessKeyId && secretAccessKey) {
    return { kind: 'explicit', accessKeyId, secretAccessKey };
  }
  if (!accessKeyId && !secretAccessKey) {
    return { kind: 'iamRole' };
  }
  return {
    kind: 'partial',
    missingField: accessKeyId ? 'secretAccessKey' : 'accessKeyId',
  };
}
