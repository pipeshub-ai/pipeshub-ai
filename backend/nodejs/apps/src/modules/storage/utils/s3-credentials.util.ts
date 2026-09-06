export const S3_PARTIAL_CREDENTIALS_MESSAGE =
  'S3 access key ID and secret access key must be provided together. Omit both to authenticate with the EC2/ECS IAM role.';

/**
 * Explicit IAM user credentials are an all-or-nothing pair. Accepting just one
 * of them would silently fall back to the AWS default credential chain, so the
 * instance would run as a different principal than the operator configured.
 */
export type S3CredentialResolution =
  | { kind: 'explicit'; accessKeyId: string; secretAccessKey: string }
  | { kind: 'iamRole' }
  | { kind: 'partial'; missingField: 'accessKeyId' | 'secretAccessKey' };

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
