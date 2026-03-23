import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  baseStorageSchema,
  s3ConfigSchema,
  azureBlobConfigSchema,
} from '../../../../src/modules/configuration_manager/validator/validators'

describe('configuration_manager/validator/validators', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('baseStorageSchema', () => {
    it('should accept local storage type', () => {
      const result = baseStorageSchema.safeParse({ storageType: 'local' })
      expect(result.success).to.be.true
    })

    it('should accept s3 storage type', () => {
      const result = baseStorageSchema.safeParse({ storageType: 's3' })
      expect(result.success).to.be.true
    })

    it('should accept azureBlob storage type', () => {
      const result = baseStorageSchema.safeParse({ storageType: 'azureBlob' })
      expect(result.success).to.be.true
    })

    it('should reject invalid storage type', () => {
      const result = baseStorageSchema.safeParse({ storageType: 'gcs' })
      expect(result.success).to.be.false
    })
  })

  describe('s3ConfigSchema', () => {
    it('should accept valid S3 config', () => {
      const data = {
        storageType: 's3',
        s3AccessKeyId: 'AKIAIOSFODNN7EXAMPLE',
        s3SecretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
        s3Region: 'us-east-1',
        s3BucketName: 'my-bucket',
      }
      const result = s3ConfigSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing accessKeyId', () => {
      const data = {
        storageType: 's3',
        s3SecretAccessKey: 'secret',
        s3Region: 'us-east-1',
        s3BucketName: 'my-bucket',
      }
      const result = s3ConfigSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject missing bucket name', () => {
      const data = {
        storageType: 's3',
        s3AccessKeyId: 'key',
        s3SecretAccessKey: 'secret',
        s3Region: 'us-east-1',
      }
      const result = s3ConfigSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('azureBlobConfigSchema', () => {
    it('should accept valid Azure config with individual params', () => {
      const data = {
        storageType: 'azureBlob',
        accountName: 'myaccount',
        accountKey: 'mykey',
        containerName: 'mycontainer',
      }
      const result = azureBlobConfigSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept Azure config with connection string', () => {
      const data = {
        storageType: 'azureBlob',
        azureBlobConnectionString: 'DefaultEndpointsProtocol=https;AccountName=test;...',
        containerName: 'mycontainer',
      }
      const result = azureBlobConfigSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing container name', () => {
      const data = {
        storageType: 'azureBlob',
        accountName: 'myaccount',
        accountKey: 'mykey',
      }
      const result = azureBlobConfigSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })
})
