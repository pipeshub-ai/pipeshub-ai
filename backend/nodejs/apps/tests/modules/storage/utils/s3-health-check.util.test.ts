import 'reflect-metadata'
import { expect } from 'chai'
import {
  buildS3HealthCheckErrorMessage,
} from '../../../../src/modules/storage/utils/s3-health-check.util'

describe('s3-health-check.util', () => {
  describe('buildS3HealthCheckErrorMessage', () => {
    it('should summarize failed capability checks', () => {
      const message = buildS3HealthCheckErrorMessage([
        { capability: 'bucketAccess', passed: true },
        { capability: 'upload', passed: false, error: 'AccessDenied' },
        { capability: 'signedUrlPut', passed: false, error: 'AccessDenied' },
      ])

      expect(message).to.include('S3 health check failed')
      expect(message).to.include('upload: AccessDenied')
      expect(message).to.include('signedUrlPut: AccessDenied')
      expect(message).to.include('s3:ListBucket')
    })
  })
})
