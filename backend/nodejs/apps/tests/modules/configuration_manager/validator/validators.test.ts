import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  baseStorageSchema,
  s3ConfigSchema,
  azureBlobConfigSchema,
  providerType,
  addProviderRequestSchema,
  configurationSchema,
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

  describe('providerType (dynamic string validation)', () => {
    it('should accept known providers like openAI', () => {
      const result = providerType.safeParse('openAI')
      expect(result.success).to.be.true
    })

    it('should accept known providers like minimax', () => {
      const result = providerType.safeParse('minimax')
      expect(result.success).to.be.true
    })

    it('should accept any non-empty provider string (dynamic registry)', () => {
      const result = providerType.safeParse('brandNewProvider')
      expect(result.success).to.be.true
    })

    it('should reject empty string', () => {
      const result = providerType.safeParse('')
      expect(result.success).to.be.false
    })
  })

  describe('addProviderRequestSchema - minimax', () => {
    it('should accept valid MiniMax LLM provider config', () => {
      const data = {
        body: {
          modelType: 'llm',
          provider: 'minimax',
          configuration: {
            model: 'MiniMax-M2.7',
            apiKey: 'test-minimax-key',
          },
          isMultimodal: true,
          isReasoning: false,
          isDefault: false,
        },
      }
      const result = addProviderRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept MiniMax with multiple models', () => {
      const data = {
        body: {
          modelType: 'llm',
          provider: 'minimax',
          configuration: {
            model: 'MiniMax-M2.7, MiniMax-M2.7-highspeed',
            apiKey: 'test-minimax-key',
          },
          isMultimodal: true,
          isReasoning: false,
          isDefault: false,
        },
      }
      const result = addProviderRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject MiniMax with invalid modelType', () => {
      const data = {
        body: {
          modelType: 'invalid',
          provider: 'minimax',
          configuration: {
            model: 'MiniMax-M2.7',
            apiKey: 'test-key',
          },
          isMultimodal: false,
          isReasoning: false,
          isDefault: false,
        },
      }
      const result = addProviderRequestSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept modelType "tts"', () => {
      const data = {
        body: {
          modelType: 'tts',
          provider: 'openAI',
          configuration: {
            model: 'tts-1',
            apiKey: 'test-key',
            voice: 'nova',
          },
          isMultimodal: false,
          isReasoning: false,
          isDefault: true,
        },
      }
      const result = addProviderRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept modelType "stt" with openAI', () => {
      const data = {
        body: {
          modelType: 'stt',
          provider: 'openAI',
          configuration: {
            model: 'whisper-1',
            apiKey: 'test-key',
          },
          isMultimodal: false,
          isReasoning: false,
          isDefault: true,
        },
      }
      const result = addProviderRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept modelType "stt" with self-hosted whisper (no apiKey)', () => {
      const data = {
        body: {
          modelType: 'stt',
          provider: 'whisper',
          configuration: {
            model: 'base',
          },
          isMultimodal: false,
          isReasoning: false,
          isDefault: true,
        },
      }
      const result = addProviderRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })
  })

  describe('configurationSchema passthrough', () => {
    it('should preserve provider-specific keys not declared on the base object shape', () => {
      const result = configurationSchema.safeParse({
        model: 'gemini-pro',
        apiKey: 'optional',
        project: 'gcp-project-123',
        location: 'us-central1',
        serviceAccountJson: '{"type":"service_account"}',
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.project).to.equal('gcp-project-123')
        expect(result.data.location).to.equal('us-central1')
        expect(result.data.serviceAccountJson).to.equal('{"type":"service_account"}')
      }
    })
  })
})
