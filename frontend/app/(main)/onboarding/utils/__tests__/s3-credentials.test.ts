import { describe, it, expect } from 'vitest';
import { resolveS3Credentials } from '../s3-credentials';

describe('resolveS3Credentials', () => {
  it('treats both keys blank as the instance IAM role', () => {
    expect(resolveS3Credentials({ accessKeyId: '', secretAccessKey: '   ' })).toEqual({
      kind: 'iamRole',
    });
    expect(resolveS3Credentials({})).toEqual({ kind: 'iamRole' });
  });

  it('reports which half of the pair is missing', () => {
    expect(resolveS3Credentials({ accessKeyId: 'AKIA123' })).toEqual({
      kind: 'partial',
      missingField: 'secretAccessKey',
    });
    expect(resolveS3Credentials({ secretAccessKey: 'secret' })).toEqual({
      kind: 'partial',
      missingField: 'accessKeyId',
    });
  });

  it('returns trimmed explicit credentials when both are present', () => {
    expect(
      resolveS3Credentials({ accessKeyId: ' AKIA123 ', secretAccessKey: ' secret ' })
    ).toEqual({ kind: 'explicit', accessKeyId: 'AKIA123', secretAccessKey: 'secret' });
  });
});
