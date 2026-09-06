/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import {
  baseStorageSchema,
  s3ConfigSchema,
  s3ConfigSchemaRefined,
  azureBlobConfigSchema,
  azureBlobConfigSchemaRefined,
  localConfigSchema,
  storageValidationSchema,
  smtpConfigSchema,
  azureAdConfigSchema,
  ssoConfigSchema,
  googleAuthConfigSchema,
  githubAuthConfigSchema,
  oauthConfigSchema,
  microsoftConfigSchema,
  platformSettingsSchema,
  createSlackBotConfigSchema,
  updateSlackBotConfigSchema,
  deleteSlackBotConfigSchema,
  modelType,
  providerType,
  configurationSchema,
  modelConfigurationSchema,
  addProviderRequestSchema,
  updateProviderRequestSchema,
  updateDefaultModelSchema,
  deleteProviderSchema,
  webSearchProviderType,
  addWebSearchProviderSchema,
  updateWebSearchProviderSchema,
  deleteWebSearchProviderSchema,
  updateDefaultWebSearchProviderSchema,
  webSearchSettingsSchema,
  updateWebSearchSettingsSchema,
  urlSchema,
  publicUrlSchema,
  metricsCollectionToggleSchema,
  metricsCollectionPushIntervalSchema,
  metricsCollectionRemoteServerSchema,
  modelTypeSchema,
} from '../../../../src/modules/configuration_manager/validator/validators'

describe('CM Validators', () => {
  describe('baseStorageSchema', () => {
    it('should accept valid storage types', () => {
      expect(baseStorageSchema.safeParse({ storageType: 's3' }).success).to.be.true
      expect(baseStorageSchema.safeParse({ storageType: 'azureBlob' }).success).to.be.true
      expect(baseStorageSchema.safeParse({ storageType: 'local' }).success).to.be.true
    })

    it('should reject invalid storage type', () => {
      expect(baseStorageSchema.safeParse({ storageType: 'gcs' }).success).to.be.false
    })
  })

  describe('s3ConfigSchema', () => {
    it('should accept valid S3 config', () => {
      const result = s3ConfigSchema.safeParse({
        storageType: 's3',
        s3AccessKeyId: 'AKID',
        s3SecretAccessKey: 'secret',
        s3Region: 'us-east-1',
        s3BucketName: 'my-bucket',
      })
      expect(result.success).to.be.true
    })

    it('should reject missing required fields', () => {
      const result = s3ConfigSchema.safeParse({ storageType: 's3' })
      expect(result.success).to.be.false
    })

    it('should accept S3 config without credentials (IAM role mode)', () => {
      const result = s3ConfigSchema.safeParse({
        storageType: 's3',
        s3Region: 'us-east-1',
        s3BucketName: 'my-bucket',
      })
      expect(result.success).to.be.true
    })

    it('should reject a half-filled credential pair', () => {
      const result = s3ConfigSchemaRefined.safeParse({
        storageType: 's3',
        s3AccessKeyId: 'AKID',
        s3Region: 'us-east-1',
        s3BucketName: 'my-bucket',
      })
      expect(result.success).to.be.false
    })
  })

  describe('azureBlobConfigSchemaRefined', () => {
    it('should accept connection string + container', () => {
      const result = azureBlobConfigSchemaRefined.safeParse({
        storageType: 'azureBlob',
        azureBlobConnectionString: 'DefaultEndpointsProtocol=https;',
        containerName: 'mycontainer',
      })
      expect(result.success).to.be.true
    })

    it('should accept account params + container', () => {
      const result = azureBlobConfigSchemaRefined.safeParse({
        storageType: 'azureBlob',
        accountName: 'myaccount',
        accountKey: 'mykey',
        containerName: 'mycontainer',
      })
      expect(result.success).to.be.true
    })

    it('should reject when neither approach is complete', () => {
      const result = azureBlobConfigSchemaRefined.safeParse({
        storageType: 'azureBlob',
        containerName: 'mycontainer',
      })
      expect(result.success).to.be.false
    })
  })

  describe('localConfigSchema', () => {
    it('should accept local config', () => {
      const result = localConfigSchema.safeParse({
        storageType: 'local',
        mountName: 'data',
      })
      expect(result.success).to.be.true
    })
  })

  describe('storageValidationSchema', () => {
    it('should accept valid S3 body', () => {
      const result = storageValidationSchema.safeParse({
        body: {
          storageType: 's3',
          s3AccessKeyId: 'AKID',
          s3SecretAccessKey: 'secret',
          s3Region: 'us-east-1',
          s3BucketName: 'bucket',
        },
      })
      expect(result.success).to.be.true
    })

    it('should accept S3 body without credentials (IAM role mode)', () => {
      const result = storageValidationSchema.safeParse({
        body: {
          storageType: 's3',
          s3Region: 'us-east-1',
          s3BucketName: 'bucket',
        },
      })
      expect(result.success).to.be.true
    })
  })

  describe('smtpConfigSchema', () => {
    it('should accept valid SMTP config', () => {
      const result = smtpConfigSchema.safeParse({
        body: { host: 'smtp.example.com', port: 587, fromEmail: 'noreply@example.com' },
      })
      expect(result.success).to.be.true
    })

    it('should reject missing host', () => {
      const result = smtpConfigSchema.safeParse({
        body: { port: 587, fromEmail: 'x@y.com' },
      })
      expect(result.success).to.be.false
    })
  })

  describe('auth config schemas', () => {
    it('azureAdConfigSchema should accept valid config', () => {
      const result = azureAdConfigSchema.safeParse({ body: { clientId: 'abc' } })
      expect(result.success).to.be.true
    })

    it('ssoConfigSchema should accept valid config', () => {
      const result = ssoConfigSchema.safeParse({
        body: { entryPoint: 'https://idp.example.com', certificate: 'CERT', emailKey: 'email' },
      })
      expect(result.success).to.be.true
    })

    it('googleAuthConfigSchema should accept valid config', () => {
      const result = googleAuthConfigSchema.safeParse({ body: { clientId: 'gid' } })
      expect(result.success).to.be.true
    })

    it('githubAuthConfigSchema should require clientId and clientSecret', () => {
      expect(githubAuthConfigSchema.safeParse({ body: { clientId: 'gid', clientSecret: 'gs' } }).success).to.be.true
      expect(githubAuthConfigSchema.safeParse({ body: { clientId: 'gid' } }).success).to.be.false
    })

    it('oauthConfigSchema should accept valid config', () => {
      const result = oauthConfigSchema.safeParse({
        body: { providerName: 'custom', clientId: 'cid' },
      })
      expect(result.success).to.be.true
    })

    it('microsoftConfigSchema should accept valid config', () => {
      const result = microsoftConfigSchema.safeParse({ clientId: 'msid' })
      expect(result.success).to.be.true
    })
  })

  describe('platformSettingsSchema', () => {
    it('should accept valid settings', () => {
      const result = platformSettingsSchema.safeParse({
        body: { fileUploadMaxSizeBytes: 10485760, featureFlags: { ENABLE_X: true } },
      })
      expect(result.success).to.be.true
    })

    it('should reject file size exceeding 1GB', () => {
      const result = platformSettingsSchema.safeParse({
        body: { fileUploadMaxSizeBytes: 2 * 1024 * 1024 * 1024, featureFlags: {} },
      })
      expect(result.success).to.be.false
    })
  })

  describe('slackBot config schemas', () => {
    it('createSlackBotConfigSchema should accept valid config', () => {
      const result = createSlackBotConfigSchema.safeParse({
        body: { name: 'MyBot', botToken: 'xoxb-abc', signingSecret: 'secret123' },
      })
      expect(result.success).to.be.true
    })

    it('updateSlackBotConfigSchema should require configId param', () => {
      const result = updateSlackBotConfigSchema.safeParse({
        params: { configId: 'cfg1' },
        body: { name: 'MyBot', botToken: 'xoxb-abc', signingSecret: 'secret123' },
      })
      expect(result.success).to.be.true
    })

    it('deleteSlackBotConfigSchema should require configId', () => {
      const result = deleteSlackBotConfigSchema.safeParse({ params: { configId: 'cfg1' } })
      expect(result.success).to.be.true
    })
  })

  describe('AI model schemas', () => {
    it('modelType should accept valid types', () => {
      for (const t of ['llm', 'embedding', 'ocr', 'slm', 'reasoning', 'multiModal', 'imageGeneration', 'tts', 'stt']) {
        expect(modelType.safeParse(t).success).to.be.true
      }
    })

    it('providerType should require non-empty string', () => {
      expect(providerType.safeParse('openai').success).to.be.true
      expect(providerType.safeParse('').success).to.be.false
    })

    it('configurationSchema should passthrough unknown keys', () => {
      const result = configurationSchema.safeParse({ model: 'gpt-4', customKey: 'val' })
      expect(result.success).to.be.true
      if (result.success) {
        expect((result.data as any).customKey).to.equal('val')
      }
    })

    it('configurationSchema should reject modelFriendlyName with comma-separated model', () => {
      const result = configurationSchema.safeParse({
        model: 'gpt-4, gpt-3.5',
        modelFriendlyName: 'MyModel',
      })
      expect(result.success).to.be.false
    })

    it('configurationSchema should allow modelFriendlyName with single model', () => {
      const result = configurationSchema.safeParse({
        model: 'gpt-4',
        modelFriendlyName: 'GPT Four',
      })
      expect(result.success).to.be.true
    })

    it('addProviderRequestSchema should accept valid input', () => {
      const result = addProviderRequestSchema.safeParse({
        body: {
          modelType: 'llm',
          provider: 'openai',
          configuration: { model: 'gpt-4' },
        },
      })
      expect(result.success).to.be.true
    })

    it('updateProviderRequestSchema should accept valid input', () => {
      const result = updateProviderRequestSchema.safeParse({
        params: { modelType: 'llm', modelKey: 'key1' },
        body: {
          provider: 'openai',
          configuration: { model: 'gpt-4' },
        },
      })
      expect(result.success).to.be.true
    })

    it('modelTypeSchema should validate params.modelType', () => {
      expect(modelTypeSchema.safeParse({ params: { modelType: 'llm' } }).success).to.be.true
      expect(modelTypeSchema.safeParse({ params: { modelType: 'invalid' } }).success).to.be.false
    })

    it('updateDefaultModelSchema should require modelType and modelKey', () => {
      const result = updateDefaultModelSchema.safeParse({
        params: { modelType: 'embedding', modelKey: 'mk1' },
      })
      expect(result.success).to.be.true
    })

    it('deleteProviderSchema should require modelType and modelKey', () => {
      const result = deleteProviderSchema.safeParse({
        params: { modelType: 'ocr', modelKey: 'mk2' },
      })
      expect(result.success).to.be.true
    })
  })

  describe('web search schemas', () => {
    it('webSearchProviderType should accept valid providers', () => {
      for (const p of ['duckduckgo', 'serper', 'tavily', 'exa']) {
        expect(webSearchProviderType.safeParse(p).success).to.be.true
      }
      expect(webSearchProviderType.safeParse('google').success).to.be.false
    })

    it('addWebSearchProviderSchema should accept valid config', () => {
      const result = addWebSearchProviderSchema.safeParse({
        body: { provider: 'serper', configuration: { apiKey: 'key' } },
      })
      expect(result.success).to.be.true
    })

    it('updateWebSearchProviderSchema should require providerKey', () => {
      const result = updateWebSearchProviderSchema.safeParse({
        params: { providerKey: 'pk1' },
        body: { provider: 'tavily', configuration: {} },
      })
      expect(result.success).to.be.true
    })

    it('deleteWebSearchProviderSchema should require providerKey', () => {
      const result = deleteWebSearchProviderSchema.safeParse({ params: { providerKey: 'pk1' } })
      expect(result.success).to.be.true
    })

    it('updateDefaultWebSearchProviderSchema should require providerKey', () => {
      const result = updateDefaultWebSearchProviderSchema.safeParse({ params: { providerKey: 'pk1' } })
      expect(result.success).to.be.true
    })

    it('webSearchSettingsSchema should require maxImages when includeImages is true', () => {
      expect(webSearchSettingsSchema.safeParse({ includeImages: true, maxImages: 10 }).success).to.be.true
      expect(webSearchSettingsSchema.safeParse({ includeImages: true }).success).to.be.false
      expect(webSearchSettingsSchema.safeParse({ includeImages: false }).success).to.be.true
    })

    it('updateWebSearchSettingsSchema should wrap in body', () => {
      const result = updateWebSearchSettingsSchema.safeParse({
        body: { includeImages: false },
      })
      expect(result.success).to.be.true
    })
  })

  describe('url schemas', () => {
    it('urlSchema should accept valid url', () => {
      expect(urlSchema.safeParse({ body: { url: 'https://example.com' } }).success).to.be.true
      expect(urlSchema.safeParse({ body: { url: 'not-a-url' } }).success).to.be.false
    })

    it('publicUrlSchema should accept optional urls', () => {
      expect(publicUrlSchema.safeParse({ body: {} }).success).to.be.true
      expect(publicUrlSchema.safeParse({ body: { frontendUrl: 'https://fe.com' } }).success).to.be.true
    })
  })

  describe('metrics schemas', () => {
    it('metricsCollectionToggleSchema should transform to string', () => {
      const result = metricsCollectionToggleSchema.safeParse({
        body: { enableMetricCollection: true },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.enableMetricCollection).to.equal('true')
      }
    })

    it('metricsCollectionPushIntervalSchema should transform to string', () => {
      const result = metricsCollectionPushIntervalSchema.safeParse({
        body: { pushIntervalMs: 5000 },
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(result.data.body.pushIntervalMs).to.equal('5000')
      }
    })

    it('metricsCollectionRemoteServerSchema should accept valid url', () => {
      const result = metricsCollectionRemoteServerSchema.safeParse({
        body: { serverUrl: 'https://metrics.example.com' },
      })
      expect(result.success).to.be.true
    })
  })
})
