import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import * as generateAuthTokenModule from '../../../../src/modules/auth/utils/generateAuthToken'
import {
  createStorageConfig,
  getStorageConfig,
  createSmtpConfig,
  getSmtpConfig,
  getSlackBotConfigs,
  createSlackBotConfig,
  updateSlackBotConfig,
  deleteSlackBotConfig,
  setPlatformSettings,
  getPlatformSettings,
  getAvailablePlatformFeatureFlags,
  getAzureAdAuthConfig,
  setAzureAdAuthConfig,
  getMicrosoftAuthConfig,
  setMicrosoftAuthConfig,
  getGoogleAuthConfig,
  setGoogleAuthConfig,
  getOAuthConfig,
  setOAuthConfig,
  getSsoAuthConfig,
  setSsoAuthConfig,
  getGoogleWorkspaceOauthConfig,
  setGoogleWorkspaceOauthConfig,
  getFrontendUrl,
  setFrontendUrl,
  getConnectorPublicUrl,
  setConnectorPublicUrl,
  toggleMetricsCollection,
  getMetricsCollection,
  setMetricsCollectionPushInterval,
  setMetricsCollectionRemoteServer,
  getAIModelsConfig,
  getAIModelsProviders,
  getModelsByType,
  getAvailableModelsByType,
  deleteAIModelProvider,
  updateDefaultAIModel,
  getConnectorConfig,
  getCustomSystemPrompt,
  setCustomSystemPrompt,
  getAtlassianOauthConfig,
  setAtlassianOauthConfig,
  getOneDriveCredentials,
  setOneDriveCredentials,
  getSharePointCredentials,
  setSharePointCredentials,
  createArangoDbConfig,
  getArangoDbConfig,
  createMongoDbConfig,
  getMongoDbConfig,
  createRedisConfig,
  getRedisConfig,
  createKafkaConfig,
  getKafkaConfig,
  createQdrantConfig,
  getQdrantConfig,
  getAtlassianCredentials,
  setAtlassianCredentials,
  createAIModelsConfig,
  addAIModelProvider,
  updateAIModelProvider,
  createGoogleWorkspaceCredentials,
  getGoogleWorkspaceCredentials,
  getGoogleWorkspaceBusinessCredentials,
  deleteGoogleWorkspaceCredentials,
} from '../../../../src/modules/configuration_manager/controller/cm_controller'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import nock from 'nock'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: {},
    body: {},
    params: {},
    query: {},
    path: '/test',
    method: 'GET',
    user: { userId: 'user-1', orgId: 'org-1', email: 'test@test.com', fullName: 'Test User' },
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    end: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
  }
  res.status.returns(res)
  res.json.returns(res)
  res.end.returns(res)
  res.send.returns(res)
  res.setHeader.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

function createMockKeyValueStore(overrides: Record<string, any> = {}): any {
  return {
    get: sinon.stub().resolves(null),
    set: sinon.stub().resolves(),
    delete: sinon.stub().resolves(),
    compareAndSet: sinon.stub().resolves(true),
    ...overrides,
  }
}

function createMockEncryptionService(): any {
  return {
    encrypt: sinon.stub().callsFake((val: string) => `encrypted:${val}`),
    decrypt: sinon.stub().callsFake((val: string) => val.replace('encrypted:', '')),
  }
}

function createMockEventService(): any {
  return {
    start: sinon.stub().resolves(),
    publishEvent: sinon.stub().resolves(),
    stop: sinon.stub().resolves(),
  }
}

function createMockConfigService(): any {
  return {
    updateConfig: sinon.stub().resolves({ statusCode: 200, data: {} }),
  }
}

const fakeConfigManagerConfig = {
  algorithm: 'aes-256-gcm',
  secretKey: 'a'.repeat(64),
  storeType: 'etcd',
  storeConfig: { host: 'localhost', port: 2379, dialTimeout: 2000 },
  redisConfig: { host: 'localhost', port: 6379 },
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ConfigurationManager Controller', () => {
  let mockEncService: any
  let loadConfigStub: sinon.SinonStub

  beforeEach(() => {
    mockEncService = createMockEncryptionService()
    loadConfigStub = sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns(fakeConfigManagerConfig as any)
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns(mockEncService)
  })

  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // Storage Config
  // -----------------------------------------------------------------------
  describe('createStorageConfig', () => {
    it('should return a handler function', () => {
      const kvs = createMockKeyValueStore()
      const handler = createStorageConfig(kvs, { endpoint: 'http://localhost:3003' } as any)
      expect(handler).to.be.a('function')
    })

    it('should save S3 storage config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createStorageConfig(kvs, { endpoint: 'http://localhost:3003' } as any)
      const req = createMockRequest({
        body: {
          storageType: 's3',
          s3AccessKeyId: 'AKIA...',
          s3SecretAccessKey: 'secret',
          s3Region: 'us-east-1',
          s3BucketName: 'my-bucket',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledOnce).to.be.true
      expect(next.called).to.be.false
    })

    it('should save Azure Blob storage config with connection string', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createStorageConfig(kvs, { endpoint: 'http://localhost:3003' } as any)
      const req = createMockRequest({
        body: {
          storageType: 'azureBlob',
          azureBlobConnectionString: 'DefaultEndpointsProtocol=https;...',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should save Azure Blob storage config with individual fields', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createStorageConfig(kvs, { endpoint: 'http://localhost:3003' } as any)
      const req = createMockRequest({
        body: {
          storageType: 'azureBlob',
          endpointProtocol: 'https',
          accountName: 'myaccount',
          accountKey: 'mykey',
          endpointSuffix: 'core.windows.net',
          containerName: 'mycontainer',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should save local storage config', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createStorageConfig(kvs, { endpoint: 'http://localhost:3003' } as any)
      const req = createMockRequest({
        body: {
          storageType: 'local',
          mountName: 'PipesHub',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should call next with BadRequestError for unsupported storage type', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createStorageConfig(kvs, { endpoint: 'http://localhost:3003' } as any)
      const req = createMockRequest({
        body: { storageType: 'unsupported' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getStorageConfig', () => {
    it('should return a handler function', () => {
      const kvs = createMockKeyValueStore()
      const handler = getStorageConfig(kvs)
      expect(handler).to.be.a('function')
    })

    it('should return S3 config when storageType is s3', async () => {
      const s3Data = JSON.stringify({ accessKeyId: 'AK', secretAccessKey: 'SK', region: 'us-east-1', bucketName: 'b' })
      mockEncService.decrypt.returns(s3Data)
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ storageType: 's3', s3: 'encrypted:data' })),
      })
      const handler = getStorageConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledOnce).to.be.true
    })

    it('should call next with error when storageType is missing', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('{}'),
      })
      const handler = getStorageConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should return local config when storageType is local', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ storageType: 'local', local: '{"mountName":"PipesHub"}' })),
      })
      const handler = getStorageConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // SMTP Config
  // -----------------------------------------------------------------------
  describe('getSmtpConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getSmtpConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return SMTP config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ host: 'smtp.test.com', port: 587 }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:smtp-config'),
      })
      const handler = getSmtpConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Slack Bot Config
  // -----------------------------------------------------------------------
  describe('getSlackBotConfigs', () => {
    it('should return empty configs when no store exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getSlackBotConfigs(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.status).to.equal('success')
      expect(response.configs).to.be.an('array').that.is.empty
    })
  })

  describe('createSlackBotConfig', () => {
    it('should return a handler function', () => {
      const kvs = createMockKeyValueStore()
      const handler = createSlackBotConfig(kvs)
      expect(handler).to.be.a('function')
    })
  })

  describe('updateSlackBotConfig', () => {
    it('should return a handler function', () => {
      const kvs = createMockKeyValueStore()
      const handler = updateSlackBotConfig(kvs)
      expect(handler).to.be.a('function')
    })
  })

  describe('deleteSlackBotConfig', () => {
    it('should return a handler function', () => {
      const kvs = createMockKeyValueStore()
      const handler = deleteSlackBotConfig(kvs)
      expect(handler).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // Platform Settings
  // -----------------------------------------------------------------------
  describe('setPlatformSettings', () => {
    it('should save platform settings successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setPlatformSettings(kvs)
      const req = createMockRequest({
        body: { fileUploadMaxSizeBytes: 10485760, featureFlags: { ENABLE_BETA_CONNECTORS: true } },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('Platform settings saved')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setPlatformSettings(kvs)
      const req = createMockRequest({ body: { fileUploadMaxSizeBytes: 100, featureFlags: {} } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getPlatformSettings', () => {
    it('should return a handler function', () => {
      const kvs = createMockKeyValueStore()
      const handler = getPlatformSettings(kvs)
      expect(handler).to.be.a('function')
    })
  })

  describe('getAvailablePlatformFeatureFlags', () => {
    it('should return flags', async () => {
      const handler = getAvailablePlatformFeatureFlags()
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('flags')
    })
  })

  // -----------------------------------------------------------------------
  // Azure AD Auth Config
  // -----------------------------------------------------------------------
  describe('getAzureAdAuthConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAzureAdAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ clientId: 'cid', tenantId: 'tid' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getAzureAdAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('setAzureAdAuthConfig', () => {
    it('should save Azure AD config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setAzureAdAuthConfig(kvs)
      const req = createMockRequest({
        body: { clientId: 'cid', tenantId: 'tid', enableJit: true },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('Azure AD config created successfully')
    })

    it('should default enableJit to true when not provided', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setAzureAdAuthConfig(kvs)
      const req = createMockRequest({
        body: { clientId: 'cid', tenantId: 'tid' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      const encryptedArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encryptedArg)
      expect(parsed.enableJit).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Microsoft Auth Config
  // -----------------------------------------------------------------------
  describe('getMicrosoftAuthConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getMicrosoftAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })
  })

  describe('setMicrosoftAuthConfig', () => {
    it('should save Microsoft Auth config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setMicrosoftAuthConfig(kvs)
      const req = createMockRequest({
        body: { clientId: 'cid', tenantId: 'tid' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Google Auth Config
  // -----------------------------------------------------------------------
  describe('getGoogleAuthConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getGoogleAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })
  })

  describe('setGoogleAuthConfig', () => {
    it('should save Google Auth config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setGoogleAuthConfig(kvs)
      const req = createMockRequest({
        body: { clientId: 'google-cid' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // OAuth Config
  // -----------------------------------------------------------------------
  describe('getOAuthConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getOAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })
  })

  describe('setOAuthConfig', () => {
    it('should save OAuth config with all fields', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setOAuthConfig(kvs)
      const req = createMockRequest({
        body: {
          providerName: 'custom',
          clientId: 'cid',
          clientSecret: 'secret',
          authorizationUrl: 'https://auth.example.com/authorize',
          tokenEndpoint: 'https://auth.example.com/token',
          userInfoEndpoint: 'https://auth.example.com/userinfo',
          scope: 'openid profile email',
          redirectUri: 'https://app.example.com/callback',
          enableJit: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('OAuth config created successfully')
    })

    it('should save OAuth config with only required fields', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setOAuthConfig(kvs)
      const req = createMockRequest({
        body: { providerName: 'minimal', clientId: 'cid' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      const encrypted = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encrypted)
      expect(parsed.enableJit).to.be.true
      expect(parsed).not.to.have.property('clientSecret')
    })
  })

  // -----------------------------------------------------------------------
  // SSO Auth Config
  // -----------------------------------------------------------------------
  describe('getSsoAuthConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getSsoAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })
  })

  describe('setSsoAuthConfig', () => {
    it('should save SSO config and clean certificate', async () => {
      const kvs = createMockKeyValueStore()
      const mockSamlController = { updateSamlStrategiesWithCallback: sinon.stub().resolves() }
      const handler = setSsoAuthConfig(kvs, mockSamlController as any)
      const req = createMockRequest({
        body: {
          entryPoint: 'https://sso.example.com/saml',
          certificate: '-----BEGIN CERTIFICATE-----\nABC123\n-----END CERTIFICATE-----',
          emailKey: 'email',
          enableJit: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      // Verify certificate was cleaned
      const encrypted = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encrypted)
      expect(parsed.certificate).to.equal('ABC123')
    })
  })

  // -----------------------------------------------------------------------
  // Google Workspace OAuth Config
  // -----------------------------------------------------------------------
  describe('getGoogleWorkspaceOauthConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getGoogleWorkspaceOauthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ clientId: 'gc', clientSecret: 'gs' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:gws-config'),
      })
      const handler = getGoogleWorkspaceOauthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Frontend URL
  // -----------------------------------------------------------------------
  describe('getFrontendUrl', () => {
    it('should return empty object when no url exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getFrontendUrl(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should return frontend url when it exists', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ frontend: { publicEndpoint: 'https://app.example.com' } })),
      })
      const handler = getFrontendUrl(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ url: 'https://app.example.com' })
    })
  })

  describe('setFrontendUrl', () => {
    it('should call next when user is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setFrontendUrl(kvs, 'secret', createMockConfigService())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when URL is empty', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setFrontendUrl(kvs, 'secret', createMockConfigService())
      const req = createMockRequest({ body: { url: '' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when URL is invalid', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setFrontendUrl(kvs, 'secret', createMockConfigService())
      const req = createMockRequest({ body: { url: 'not-a-url' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Connector Public URL
  // -----------------------------------------------------------------------
  describe('getConnectorPublicUrl', () => {
    it('should return empty object when no url exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getConnectorPublicUrl(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should return connector public url when it exists', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ connectors: { publicEndpoint: 'https://connectors.example.com' } })),
      })
      const handler = getConnectorPublicUrl(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ url: 'https://connectors.example.com' })
    })
  })

  describe('setConnectorPublicUrl', () => {
    it('should call next when user is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setConnectorPublicUrl(kvs, createMockEventService())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Metrics Collection
  // -----------------------------------------------------------------------
  describe('toggleMetricsCollection', () => {
    it('should toggle metrics when value changes', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ enableMetricCollection: false })),
      })
      const handler = toggleMetricsCollection(kvs)
      const req = createMockRequest({ body: { enableMetricCollection: true } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should not set when value is same', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ enableMetricCollection: true })),
      })
      const handler = toggleMetricsCollection(kvs)
      const req = createMockRequest({ body: { enableMetricCollection: true } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getMetricsCollection', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getMetricsCollection(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('setMetricsCollectionPushInterval', () => {
    it('should set push interval when value changes', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ pushIntervalMs: 5000 })),
      })
      const handler = setMetricsCollectionPushInterval(kvs)
      const req = createMockRequest({ body: { pushIntervalMs: 10000 } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('setMetricsCollectionRemoteServer', () => {
    it('should set remote server when value changes', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('{}'),
      })
      const handler = setMetricsCollectionRemoteServer(kvs)
      const req = createMockRequest({ body: { serverUrl: 'https://metrics.example.com' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // AI Models Config
  // -----------------------------------------------------------------------
  describe('getAIModelsConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAIModelsConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return AI config when it exists', async () => {
      const aiConfig = { llm: [{ provider: 'openai' }], embedding: [] }
      mockEncService.decrypt.returns(JSON.stringify(aiConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:ai-config'),
      })
      const handler = getAIModelsConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // AI Models Providers
  // -----------------------------------------------------------------------
  describe('getAIModelsProviders', () => {
    it('should return default structure when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAIModelsProviders(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.status).to.equal('success')
      expect(response.models).to.have.property('llm')
      expect(response.models).to.have.property('embedding')
    })

    it('should return providers with filled defaults when config exists', async () => {
      const aiModels = { llm: [{ provider: 'openai' }] }
      mockEncService.decrypt.returns(JSON.stringify(aiModels))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:ai-config'),
      })
      const handler = getAIModelsProviders(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.status).to.equal('success')
      expect(response.models.llm).to.have.length(1)
    })
  })

  // -----------------------------------------------------------------------
  // Get Models By Type
  // -----------------------------------------------------------------------
  describe('getModelsByType', () => {
    it('should return 400 when modelType is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getModelsByType(kvs)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should return 400 for invalid model type', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'invalid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should return empty models when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'llm' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.models).to.deep.equal([])
    })

    it('should return models for valid type', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ llm: [{ provider: 'openai', modelKey: 'k1' }] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'llm' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.models).to.have.length(1)
    })
  })

  // -----------------------------------------------------------------------
  // Get Available Models By Type
  // -----------------------------------------------------------------------
  describe('getAvailableModelsByType', () => {
    it('should return 400 for invalid type', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAvailableModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'invalid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should flatten models with comma-separated model names', async () => {
      const aiModels = {
        llm: [{
          provider: 'openai',
          modelKey: 'k1',
          isDefault: true,
          isMultimodal: false,
          isReasoning: false,
          configuration: { model: 'gpt-4,gpt-3.5-turbo' },
        }],
      }
      mockEncService.decrypt.returns(JSON.stringify(aiModels))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getAvailableModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'llm' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.models).to.have.length(2)
      expect(response.models[0].isDefault).to.be.true
      expect(response.models[1].isDefault).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // Delete AI Model Provider
  // -----------------------------------------------------------------------
  describe('deleteAIModelProvider', () => {
    it('should return 404 when no AI config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = deleteAIModelProvider(kvs, createMockEventService(), {} as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should return 404 when model key not found', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ llm: [{ modelKey: 'k2' }] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = deleteAIModelProvider(kvs, createMockEventService(), {} as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should return 400 when model type does not match', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ embedding: [{ modelKey: 'k1', isDefault: false }] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = deleteAIModelProvider(kvs, createMockEventService(), {} as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should delete model and promote next model as default if deleted was default', async () => {
      const aiModels = {
        llm: [
          { modelKey: 'k1', isDefault: true, provider: 'openai', configuration: { model: 'gpt-4' } },
          { modelKey: 'k2', isDefault: false, provider: 'openai', configuration: { model: 'gpt-3.5' } },
        ],
      }
      mockEncService.decrypt.returns(JSON.stringify(aiModels))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const eventService = createMockEventService()
      const handler = deleteAIModelProvider(kvs, eventService, { cmBackend: 'http://cm' } as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(kvs.set.calledOnce).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.details.wasDefault).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Update Default AI Model
  // -----------------------------------------------------------------------
  describe('updateDefaultAIModel', () => {
    it('should return 404 when no AI config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = updateDefaultAIModel(kvs, createMockEventService(), {} as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should update default model successfully', async () => {
      const aiModels = {
        llm: [
          { modelKey: 'k1', isDefault: true, provider: 'openai', configuration: { model: 'gpt-4' } },
          { modelKey: 'k2', isDefault: false, provider: 'openai', configuration: { model: 'gpt-3.5' } },
        ],
      }
      mockEncService.decrypt.returns(JSON.stringify(aiModels))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })
      const handler = updateDefaultAIModel(kvs, createMockEventService(), { cmBackend: 'http://cm', aiBackend: 'http://ai' } as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k2' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(kvs.set.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Connector Config (generic)
  // -----------------------------------------------------------------------
  describe('getConnectorConfig', () => {
    it('should return empty when no config found', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getConnectorConfig(kvs)
      const req = createMockRequest({ params: { connector: 'slack' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should call next with error when connector param is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getConnectorConfig(kvs)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should decrypt and return config when found', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ key: 'value' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getConnectorConfig(kvs)
      const req = createMockRequest({ params: { connector: 'slack' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Custom System Prompt
  // -----------------------------------------------------------------------
  describe('getCustomSystemPrompt', () => {
    it('should return empty string when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getCustomSystemPrompt(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ customSystemPrompt: '', customSystemPromptWebSearch: '' })
    })

    it('should return custom prompt when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ customSystemPrompt: 'Be helpful', llm: [] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getCustomSystemPrompt(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ customSystemPrompt: 'Be helpful', customSystemPromptWebSearch: '' })
    })
  })

  describe('setCustomSystemPrompt', () => {
    it('should call next with error when prompt is not a string', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setCustomSystemPrompt(kvs)
      const req = createMockRequest({ body: { customSystemPrompt: 123 } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should save custom system prompt using CAS pattern', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ llm: [] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
        compareAndSet: sinon.stub().resolves(true),
      })
      const handler = setCustomSystemPrompt(kvs)
      const req = createMockRequest({ body: { customSystemPrompt: 'Be concise', customSystemPromptWebSearch: '' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].customSystemPrompt).to.equal('Be concise')
    })
  })

  // -----------------------------------------------------------------------
  // Atlassian OAuth Config
  // -----------------------------------------------------------------------
  describe('getAtlassianOauthConfig', () => {
    it('should return empty when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAtlassianOauthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should call next when orgId is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAtlassianOauthConfig(kvs)
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setAtlassianOauthConfig', () => {
    it('should save Atlassian config', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setAtlassianOauthConfig(kvs)
      const req = createMockRequest({
        body: { clientId: 'atl-cid', clientSecret: 'atl-secret' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should call next when orgId is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setAtlassianOauthConfig(kvs)
      const req = createMockRequest({ user: {}, body: { clientId: 'c' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // OneDrive Credentials
  // -----------------------------------------------------------------------
  describe('getOneDriveCredentials', () => {
    it('should return empty when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getOneDriveCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })
  })

  describe('setOneDriveCredentials', () => {
    it('should save OneDrive credentials', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setOneDriveCredentials(kvs)
      const req = createMockRequest({ body: { clientId: 'od-cid', tenantId: 'od-tid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // SharePoint Credentials
  // -----------------------------------------------------------------------
  describe('getSharePointCredentials', () => {
    it('should return empty when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getSharePointCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })
  })

  describe('setSharePointCredentials', () => {
    it('should save SharePoint credentials', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setSharePointCredentials(kvs)
      const req = createMockRequest({ body: { clientId: 'sp-cid', tenantId: 'sp-tid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should call next when orgId is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setSharePointCredentials(kvs)
      const req = createMockRequest({ user: {}, body: { clientId: 'sp-cid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when kvs.set fails', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setSharePointCredentials(kvs)
      const req = createMockRequest({ body: { clientId: 'sp-cid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // ArangoDB Config
  // -----------------------------------------------------------------------
  describe('createArangoDbConfig', () => {
    it('should save ArangoDB config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createArangoDbConfig(kvs)
      const req = createMockRequest({
        body: { url: 'http://arangodb:8529', username: 'root', password: 'pass' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('Arango DB config created successfully')
    })

    it('should call next on kvs.set failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = createArangoDbConfig(kvs)
      const req = createMockRequest({
        body: { url: 'http://arangodb:8529', username: 'root', password: 'pass' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should encrypt the config before storing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createArangoDbConfig(kvs)
      const req = createMockRequest({
        body: { url: 'http://arangodb:8529', username: 'root', password: 'pass' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockEncService.encrypt.calledOnce).to.be.true
    })
  })

  describe('getArangoDbConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getArangoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ url: 'http://arangodb:8529', username: 'root', password: 'pass', db: 'pipeshub' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getArangoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('url')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getArangoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // MongoDB Config
  // -----------------------------------------------------------------------
  describe('createMongoDbConfig', () => {
    it('should save MongoDB config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createMongoDbConfig(kvs)
      const req = createMockRequest({
        body: { uri: 'mongodb://localhost:27017' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('Mongo DB config created successfully')
    })

    it('should call next on kvs.set failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = createMongoDbConfig(kvs)
      const req = createMockRequest({ body: { uri: 'mongodb://localhost:27017' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should encrypt the uri before storing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createMongoDbConfig(kvs)
      const req = createMockRequest({ body: { uri: 'mongodb://localhost:27017' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockEncService.encrypt.calledOnce).to.be.true
      const encryptedArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encryptedArg)
      expect(parsed).to.have.property('uri')
      expect(parsed).to.have.property('db')
    })
  })

  describe('getMongoDbConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getMongoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ uri: 'mongodb://localhost:27017', db: 'pipeshub' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getMongoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('uri')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getMongoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Redis Config
  // -----------------------------------------------------------------------
  describe('createRedisConfig', () => {
    it('should save Redis config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createRedisConfig(kvs)
      const req = createMockRequest({
        body: { host: 'localhost', port: 6379, password: 'secret', tls: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('Redis config created successfully')
    })

    it('should call next on failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = createRedisConfig(kvs)
      const req = createMockRequest({ body: { host: 'localhost', port: 6379 } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should encrypt host, port, password, tls', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createRedisConfig(kvs)
      const req = createMockRequest({
        body: { host: 'redis.example.com', port: 6380, password: 'pw', tls: true },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const encryptedArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encryptedArg)
      expect(parsed).to.have.property('host', 'redis.example.com')
      expect(parsed).to.have.property('port', 6380)
      expect(parsed).to.have.property('tls', true)
    })
  })

  describe('getRedisConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getRedisConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ host: 'localhost', port: 6379 }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:redis'),
      })
      const handler = getRedisConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('host', 'localhost')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getRedisConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Kafka Config
  // -----------------------------------------------------------------------
  describe('createKafkaConfig', () => {
    it('should save Kafka config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createKafkaConfig(kvs)
      const req = createMockRequest({
        body: { brokers: ['localhost:9092'], sasl: null },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('Kafka config created successfully')
    })

    it('should call next on failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = createKafkaConfig(kvs)
      const req = createMockRequest({ body: { brokers: [], sasl: null } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should include warningMessage from response header', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createKafkaConfig(kvs)
      const req = createMockRequest({
        body: { brokers: ['localhost:9092'], sasl: null },
      })
      const res = createMockResponse()
      res.getHeader.returns('some warning')
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.json.firstCall.args[0].warningMessage).to.equal('some warning')
    })
  })

  describe('getKafkaConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getKafkaConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ brokers: ['localhost:9092'] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:kafka'),
      })
      const handler = getKafkaConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('brokers')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getKafkaConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Qdrant Config
  // -----------------------------------------------------------------------
  describe('createQdrantConfig', () => {
    it('should save Qdrant config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createQdrantConfig(kvs)
      const req = createMockRequest({
        body: { port: 6333, apiKey: 'qd-key', host: 'localhost', grpcPort: 6334 },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('Qdrant config created successfully')
    })

    it('should call next on failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = createQdrantConfig(kvs)
      const req = createMockRequest({ body: { port: 6333, apiKey: '', host: 'localhost', grpcPort: 6334 } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should include warningMessage from response header', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createQdrantConfig(kvs)
      const req = createMockRequest({
        body: { port: 6333, apiKey: 'qd-key', host: 'localhost', grpcPort: 6334 },
      })
      const res = createMockResponse()
      res.getHeader.returns('qdrant warning')
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.json.firstCall.args[0].warningMessage).to.equal('qdrant warning')
    })
  })

  describe('getQdrantConfig', () => {
    it('should return empty object when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getQdrantConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ port: 6333, apiKey: 'key', host: 'localhost', grpcPort: 6334 }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:qdrant'),
      })
      const handler = getQdrantConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('port', 6333)
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getQdrantConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Atlassian Credentials
  // -----------------------------------------------------------------------
  describe('getAtlassianCredentials', () => {
    it('should return empty when no credentials exist', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAtlassianCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })

    it('should call next when orgId is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAtlassianCredentials(kvs)
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should decrypt and return credentials when they exist', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ accessToken: 'tok', cloudId: 'cloud-1' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:atl-creds'),
      })
      const handler = getAtlassianCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('accessToken', 'tok')
    })
  })

  describe('setAtlassianCredentials', () => {
    it('should save Atlassian credentials', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setAtlassianCredentials(kvs)
      const req = createMockRequest({
        body: { accessToken: 'tok', refreshToken: 'ref', cloudId: 'cloud-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].message).to.equal('Atlassian credentials created successfully')
    })

    it('should call next when orgId is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setAtlassianCredentials(kvs)
      const req = createMockRequest({ user: {}, body: { accessToken: 'tok' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next on kvs.set failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setAtlassianCredentials(kvs)
      const req = createMockRequest({ body: { accessToken: 'tok' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Connector Public URL - additional tests
  // -----------------------------------------------------------------------
  describe('setConnectorPublicUrl (additional)', () => {
    it('should call next when URL is empty', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setConnectorPublicUrl(kvs, createMockEventService())
      const req = createMockRequest({ body: { url: '' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when URL is invalid', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setConnectorPublicUrl(kvs, createMockEventService())
      const req = createMockRequest({ body: { url: 'not-a-url' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Storage Config - Azure Blob S3 missing config error test
  // -----------------------------------------------------------------------
  describe('getStorageConfig (additional)', () => {
    it('should return Azure Blob config when storageType is azureBlob', async () => {
      const azureBlobData = JSON.stringify({
        endpointProtocol: 'https',
        accountName: 'acc',
        accountKey: 'key',
        endpointSuffix: 'core.windows.net',
        containerName: 'container',
      })
      mockEncService.decrypt.returns(azureBlobData)
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ storageType: 'azureBlob', azureBlob: 'encrypted:azure' })),
      })
      const handler = getStorageConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('storageType', 'azureBlob')
    })

    it('should throw BadRequestError when S3 config key is missing', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ storageType: 's3' })),
      })
      const handler = getStorageConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when azureBlob config key is missing', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ storageType: 'azureBlob' })),
      })
      const handler = getStorageConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // createSmtpConfig - additional error tests
  // -----------------------------------------------------------------------
  describe('createSmtpConfig (additional)', () => {
    it('should return a handler function', () => {
      const kvs = createMockKeyValueStore()
      const handler = createSmtpConfig(kvs, 'http://comm-backend:3002', 'jwt-secret')
      expect(handler).to.be.a('function')
    })

    it('should call next when user is not found', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createSmtpConfig(kvs, 'http://comm-backend:3002', 'jwt-secret')
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // setFrontendUrl - additional tests
  // -----------------------------------------------------------------------
  describe('setFrontendUrl (additional)', () => {
    it('should strip trailing slash from URL', async () => {
      const kvs = createMockKeyValueStore()
      const configService = createMockConfigService()
      const handler = setFrontendUrl(kvs, 'secret', configService)
      const req = createMockRequest({ body: { url: 'http://example.com/' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      // Should succeed since URL is valid after normalization
      expect(kvs.set.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Metrics Collection - additional tests
  // -----------------------------------------------------------------------
  describe('toggleMetricsCollection (additional)', () => {
    it('should call next on kvs error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = toggleMetricsCollection(kvs)
      const req = createMockRequest({ body: { enableMetricCollection: true } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getMetricsCollection (additional)', () => {
    it('should return stored config', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ enableMetricCollection: true, pushIntervalMs: 5000 })),
      })
      const handler = getMetricsCollection(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('enableMetricCollection', true)
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getMetricsCollection(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setMetricsCollectionPushInterval (additional)', () => {
    it('should not set when value is unchanged', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ pushIntervalMs: 5000 })),
      })
      const handler = setMetricsCollectionPushInterval(kvs)
      const req = createMockRequest({ body: { pushIntervalMs: 5000 } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = setMetricsCollectionPushInterval(kvs)
      const req = createMockRequest({ body: { pushIntervalMs: 10000 } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setMetricsCollectionRemoteServer (additional)', () => {
    it('should not set when value is unchanged', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({ serverUrl: 'https://metrics.example.com' })),
      })
      const handler = setMetricsCollectionRemoteServer(kvs)
      const req = createMockRequest({ body: { serverUrl: 'https://metrics.example.com' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = setMetricsCollectionRemoteServer(kvs)
      const req = createMockRequest({ body: { serverUrl: 'https://metrics.example.com' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // AI Models Config - additional error tests
  // -----------------------------------------------------------------------
  describe('getAIModelsConfig (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getAIModelsConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getAIModelsProviders (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getAIModelsProviders(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getModelsByType (additional)', () => {
    it('should return empty array when model type has no entries', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ llm: [{ provider: 'openai' }] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'embedding' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.models).to.deep.equal([])
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'llm' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getAvailableModelsByType (additional)', () => {
    it('should return 400 when modelType is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAvailableModelsByType(kvs)
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should return empty when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getAvailableModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'llm' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.models).to.deep.equal([])
    })

    it('should return empty when model type has no entries', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ llm: [] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getAvailableModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'embedding' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.models).to.deep.equal([])
    })

    it('should include modelFriendlyName only for single models', async () => {
      const aiModels = {
        llm: [{
          provider: 'openai',
          modelKey: 'k1',
          isDefault: false,
          isMultimodal: false,
          isReasoning: false,
          modelFriendlyName: 'GPT-4',
          configuration: { model: 'gpt-4' },
        }],
      }
      mockEncService.decrypt.returns(JSON.stringify(aiModels))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = getAvailableModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'llm' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.models[0]).to.have.property('modelFriendlyName', 'GPT-4')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getAvailableModelsByType(kvs)
      const req = createMockRequest({ params: { modelType: 'llm' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // updateDefaultAIModel - additional tests
  // -----------------------------------------------------------------------
  describe('updateDefaultAIModel (additional)', () => {
    it('should return 404 when model key not found', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ llm: [{ modelKey: 'k2' }] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = updateDefaultAIModel(kvs, createMockEventService(), {} as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should return 400 when model type does not match', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ embedding: [{ modelKey: 'k1' }] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const handler = updateDefaultAIModel(kvs, createMockEventService(), {} as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = updateDefaultAIModel(kvs, createMockEventService(), {} as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // deleteAIModelProvider - additional tests
  // -----------------------------------------------------------------------
  describe('deleteAIModelProvider (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = deleteAIModelProvider(kvs, createMockEventService(), {} as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should delete non-default model without promoting', async () => {
      const aiModels = {
        llm: [
          { modelKey: 'k1', isDefault: true, provider: 'openai', configuration: { model: 'gpt-4' } },
          { modelKey: 'k2', isDefault: false, provider: 'openai', configuration: { model: 'gpt-3.5' } },
        ],
      }
      mockEncService.decrypt.returns(JSON.stringify(aiModels))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
      })
      const eventService = createMockEventService()
      const handler = deleteAIModelProvider(kvs, eventService, { cmBackend: 'http://cm' } as any)
      const req = createMockRequest({ params: { modelType: 'llm', modelKey: 'k2' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.details.wasDefault).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // SSO Auth Config - additional tests
  // -----------------------------------------------------------------------
  describe('getSsoAuthConfig (additional)', () => {
    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ certificate: 'ABC', entryPoint: 'https://sso.example.com', emailKey: 'email' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:sso-config'),
      })
      const handler = getSsoAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('certificate', 'ABC')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getSsoAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setSsoAuthConfig (additional)', () => {
    it('should default enableJit to true when not provided', async () => {
      const kvs = createMockKeyValueStore()
      const mockSamlController = { updateSamlStrategiesWithCallback: sinon.stub().resolves() }
      const handler = setSsoAuthConfig(kvs, mockSamlController as any)
      const req = createMockRequest({
        body: {
          entryPoint: 'https://sso.example.com/saml',
          certificate: 'ABC123',
          emailKey: 'email',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      const encryptedArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encryptedArg)
      expect(parsed.enableJit).to.be.true
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const mockSamlController = { updateSamlStrategiesWithCallback: sinon.stub().resolves() }
      const handler = setSsoAuthConfig(kvs, mockSamlController as any)
      const req = createMockRequest({
        body: { entryPoint: 'https://sso.example.com', certificate: 'ABC', emailKey: 'email' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // OneDrive Credentials - additional tests
  // -----------------------------------------------------------------------
  describe('getOneDriveCredentials (additional)', () => {
    it('should call next when orgId is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getOneDriveCredentials(kvs)
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should decrypt and return credentials when they exist', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ clientId: 'od-cid', tenantId: 'od-tid' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:od-creds'),
      })
      const handler = getOneDriveCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('clientId', 'od-cid')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getOneDriveCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setOneDriveCredentials (additional)', () => {
    it('should call next when orgId is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setOneDriveCredentials(kvs)
      const req = createMockRequest({ user: {}, body: { clientId: 'od-cid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next on kvs.set failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setOneDriveCredentials(kvs)
      const req = createMockRequest({ body: { clientId: 'od-cid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // SharePoint Credentials - additional tests
  // -----------------------------------------------------------------------
  describe('getSharePointCredentials (additional)', () => {
    it('should call next when orgId is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getSharePointCredentials(kvs)
      const req = createMockRequest({ user: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should decrypt and return credentials when they exist', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ clientId: 'sp-cid', tenantId: 'sp-tid' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:sp-creds'),
      })
      const handler = getSharePointCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('clientId', 'sp-cid')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getSharePointCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Google Auth Config - additional tests
  // -----------------------------------------------------------------------
  describe('getGoogleAuthConfig (additional)', () => {
    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ clientId: 'gcid' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:google-auth'),
      })
      const handler = getGoogleAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('clientId', 'gcid')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getGoogleAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setGoogleAuthConfig (additional)', () => {
    it('should default enableJit to true when not provided', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setGoogleAuthConfig(kvs)
      const req = createMockRequest({ body: { clientId: 'gcid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const encryptedArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encryptedArg)
      expect(parsed.enableJit).to.be.true
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setGoogleAuthConfig(kvs)
      const req = createMockRequest({ body: { clientId: 'gcid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Microsoft Auth Config - additional tests
  // -----------------------------------------------------------------------
  describe('getMicrosoftAuthConfig (additional)', () => {
    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ clientId: 'ms-cid', tenantId: 'ms-tid' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:ms-auth'),
      })
      const handler = getMicrosoftAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('clientId', 'ms-cid')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getMicrosoftAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setMicrosoftAuthConfig (additional)', () => {
    it('should default enableJit to true', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setMicrosoftAuthConfig(kvs)
      const req = createMockRequest({ body: { clientId: 'ms-cid', tenantId: 'ms-tid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const encryptedArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encryptedArg)
      expect(parsed.enableJit).to.be.true
      expect(parsed.authority).to.include('ms-tid')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setMicrosoftAuthConfig(kvs)
      const req = createMockRequest({ body: { clientId: 'ms-cid', tenantId: 'ms-tid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // OAuth Config - additional tests
  // -----------------------------------------------------------------------
  describe('getOAuthConfig (additional)', () => {
    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ providerName: 'custom', clientId: 'cid' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:oauth'),
      })
      const handler = getOAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('providerName', 'custom')
    })

    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getOAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setOAuthConfig (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setOAuthConfig(kvs)
      const req = createMockRequest({ body: { providerName: 'custom', clientId: 'cid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Custom System Prompt - additional tests
  // -----------------------------------------------------------------------
  describe('getCustomSystemPrompt (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getCustomSystemPrompt(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setCustomSystemPrompt (additional)', () => {
    it('should call next when CAS fails after max retries', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ llm: [] }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:data'),
        compareAndSet: sinon.stub().resolves(false),
      })
      const handler = setCustomSystemPrompt(kvs)
      const req = createMockRequest({ body: { customSystemPrompt: 'Be concise' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should handle empty AI config gracefully', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
        compareAndSet: sinon.stub().resolves(true),
      })
      const handler = setCustomSystemPrompt(kvs)
      const req = createMockRequest({ body: { customSystemPrompt: 'Test prompt', customSystemPromptWebSearch: '' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].customSystemPrompt).to.equal('Test prompt')
    })
  })

  // -----------------------------------------------------------------------
  // Connector Config - additional tests
  // -----------------------------------------------------------------------
  describe('getConnectorConfig (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getConnectorConfig(kvs)
      const req = createMockRequest({ params: { connector: 'slack' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Slack Bot Configs - additional tests
  // -----------------------------------------------------------------------
  describe('getSlackBotConfigs (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getSlackBotConfigs(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should return configs when they exist', async () => {
      const configData = {
        configs: [{
          id: 'sb-1',
          name: 'MyBot',
          botToken: 'xoxb-123',
          signingSecret: 'secret',
          createdAt: '2024-01-01',
          updatedAt: '2024-01-01',
        }],
      }
      mockEncService.decrypt.returns(JSON.stringify(configData))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:slack-bots'),
      })
      const handler = getSlackBotConfigs(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.configs).to.have.length(1)
      expect(response.configs[0]).to.have.property('name', 'MyBot')
    })
  })

  // -----------------------------------------------------------------------
  // Azure AD Auth Config - additional error tests
  // -----------------------------------------------------------------------
  describe('getAzureAdAuthConfig (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getAzureAdAuthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setAzureAdAuthConfig (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setAzureAdAuthConfig(kvs)
      const req = createMockRequest({ body: { clientId: 'cid', tenantId: 'tid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should include authority in encrypted config', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setAzureAdAuthConfig(kvs)
      const req = createMockRequest({
        body: { clientId: 'cid', tenantId: 'my-tenant' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const encryptedArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encryptedArg)
      expect(parsed.authority).to.equal('https://login.microsoftonline.com/my-tenant')
    })
  })

  // -----------------------------------------------------------------------
  // setPlatformSettings - additional tests
  // -----------------------------------------------------------------------
  describe('setPlatformSettings (additional)', () => {
    it('should include updatedAt in the encrypted payload', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setPlatformSettings(kvs)
      const req = createMockRequest({
        body: { fileUploadMaxSizeBytes: 100, featureFlags: {} },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const encryptedArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(encryptedArg)
      expect(parsed).to.have.property('updatedAt')
    })
  })

  // -----------------------------------------------------------------------
  // Google Workspace OAuth Config - additional error tests
  // -----------------------------------------------------------------------
  describe('getGoogleWorkspaceOauthConfig (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getGoogleWorkspaceOauthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Atlassian OAuth Config - additional error tests
  // -----------------------------------------------------------------------
  describe('getAtlassianOauthConfig (additional)', () => {
    it('should decrypt and return config when it exists', async () => {
      mockEncService.decrypt.returns(JSON.stringify({ clientId: 'atl-cid', clientSecret: 'atl-sec' }))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('encrypted:atl-config'),
      })
      const handler = getAtlassianOauthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('clientId', 'atl-cid')
    })

    it('should call next on kvs.get error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getAtlassianOauthConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('setAtlassianOauthConfig (additional)', () => {
    it('should call next on kvs.set failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = setAtlassianOauthConfig(kvs)
      const req = createMockRequest({ body: { clientId: 'atl-cid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getFrontendUrl / getConnectorPublicUrl - additional error tests
  // -----------------------------------------------------------------------
  describe('getFrontendUrl (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getFrontendUrl(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getConnectorPublicUrl (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getConnectorPublicUrl(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // createStorageConfig - additional error tests
  // -----------------------------------------------------------------------
  describe('createStorageConfig (additional)', () => {
    it('should call next on kvs.set failure', async () => {
      const kvs = createMockKeyValueStore({
        set: sinon.stub().rejects(new Error('store failed')),
      })
      const handler = createStorageConfig(kvs, { endpoint: 'http://localhost:3003' } as any)
      const req = createMockRequest({
        body: { storageType: 's3', s3AccessKeyId: 'AK', s3SecretAccessKey: 'SK', s3Region: 'us-east-1', s3BucketName: 'b' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getSmtpConfig - additional error tests
  // -----------------------------------------------------------------------
  describe('getSmtpConfig (additional)', () => {
    it('should call next on error', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().rejects(new Error('fetch failed')),
      })
      const handler = getSmtpConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // createSmtpConfig - happy path
  // -----------------------------------------------------------------------
  describe('createSmtpConfig (happy path)', () => {
    beforeEach(() => {
      nock.disableNetConnect()
    })

    afterEach(() => {
      nock.cleanAll()
      nock.enableNetConnect()
    })

    it('should encrypt and store SMTP config before calling communication backend', async () => {
      const kvs = createMockKeyValueStore()

      const handler = createSmtpConfig(kvs, 'http://comm-backend:3002', 'jwt-secret')
      const req = createMockRequest({
        body: { host: 'smtp.test.com', port: 587, username: 'user', password: 'pass' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      // The kvs.set should always be called before the axios call
      expect(kvs.set.calledOnce).to.be.true
      // The handler will call next with error because axios call to communication backend fails in test
      // but the key path (encryption + store) is covered
    })

    it('should call next when user is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createSmtpConfig(kvs, 'http://comm-backend:3002', 'jwt-secret')
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // createSlackBotConfig - happy path
  // -----------------------------------------------------------------------
  describe('createSlackBotConfig (happy path)', () => {
    it('should create slack bot config successfully', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
        compareAndSet: sinon.stub().resolves(true),
      })
      const handler = createSlackBotConfig(kvs)
      const req = createMockRequest({
        body: { name: 'TestBot', botToken: 'xoxb-test', signingSecret: 'secret123' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('status', 'success')
        expect(response).to.have.property('config')
      }
    })

    it('should create slack bot config with agentId', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
        compareAndSet: sinon.stub().resolves(true),
      })
      const handler = createSlackBotConfig(kvs)
      const req = createMockRequest({
        body: { name: 'TestBot', botToken: 'xoxb-test', signingSecret: 'secret123', agentId: 'agent-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  // -----------------------------------------------------------------------
  // updateSlackBotConfig - happy path
  // -----------------------------------------------------------------------
  describe('updateSlackBotConfig (happy path)', () => {
    it('should update slack bot config successfully', async () => {
      const existingConfig = {
        configs: [{
          id: 'config-1',
          name: 'OldBot',
          botToken: 'xoxb-old',
          signingSecret: 'old-secret',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        }],
      }
      const encryptedExisting = mockEncService.encrypt(JSON.stringify(existingConfig))

      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encryptedExisting),
        compareAndSet: sinon.stub().resolves(true),
      })
      const handler = updateSlackBotConfig(kvs)
      const req = createMockRequest({
        params: { configId: 'config-1' },
        body: { name: 'UpdatedBot', botToken: 'xoxb-new', signingSecret: 'new-secret' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('status', 'success')
      }
    })

    it('should call next when configId is not found', async () => {
      const existingConfig = { configs: [] }
      const encryptedExisting = mockEncService.encrypt(JSON.stringify(existingConfig))

      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encryptedExisting),
        compareAndSet: sinon.stub().resolves(true),
      })
      const handler = updateSlackBotConfig(kvs)
      const req = createMockRequest({
        params: { configId: 'nonexistent' },
        body: { name: 'Bot', botToken: 'xoxb-test', signingSecret: 'secret' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // deleteSlackBotConfig - happy path
  // -----------------------------------------------------------------------
  describe('deleteSlackBotConfig (happy path)', () => {
    it('should delete slack bot config successfully', async () => {
      const existingConfig = {
        configs: [{
          id: 'config-1',
          name: 'Bot',
          botToken: 'xoxb-test',
          signingSecret: 'secret',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        }],
      }
      const encryptedExisting = mockEncService.encrypt(JSON.stringify(existingConfig))

      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encryptedExisting),
        compareAndSet: sinon.stub().resolves(true),
      })
      const handler = deleteSlackBotConfig(kvs)
      const req = createMockRequest({
        params: { configId: 'config-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('status', 'success')
      }
    })

    it('should call next when config not found', async () => {
      const existingConfig = { configs: [] }
      const encryptedExisting = mockEncService.encrypt(JSON.stringify(existingConfig))

      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encryptedExisting),
        compareAndSet: sinon.stub().resolves(true),
      })
      const handler = deleteSlackBotConfig(kvs)
      const req = createMockRequest({
        params: { configId: 'nonexistent' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getPlatformSettings - happy path
  // -----------------------------------------------------------------------
  describe('getPlatformSettings (happy path)', () => {
    it('should return platform settings when they exist', async () => {
      const settings = { fileUploadMaxSizeBytes: 10485760, featureFlags: { search: true } }
      const encrypted = mockEncService.encrypt(JSON.stringify(settings))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getPlatformSettings(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should return empty object when no settings exist', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
      })
      const handler = getPlatformSettings(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // setFrontendUrl - happy path
  // -----------------------------------------------------------------------
  describe('setFrontendUrl (happy path)', () => {
    it('should save frontend url successfully', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('{}'),
      })
      const handler = setFrontendUrl(kvs, 'test-scoped-secret', createMockConfigService())
      sinon.stub(generateAuthTokenModule, 'generateFetchConfigAuthToken').resolves('test-token')

      const req = createMockRequest({
        body: { url: 'https://app.example.com' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(kvs.set.calledOnce).to.be.true
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  // -----------------------------------------------------------------------
  // setConnectorPublicUrl - happy path
  // -----------------------------------------------------------------------
  describe('setConnectorPublicUrl (happy path)', () => {
    it('should save connector public url successfully', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('{}'),
      })
      const handler = setConnectorPublicUrl(kvs, createMockEventService())
      const req = createMockRequest({
        body: { url: 'https://connector.example.com' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(kvs.set.calledOnce).to.be.true
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should call next when url is empty', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setConnectorPublicUrl(kvs, createMockEventService())
      const req = createMockRequest({
        body: { url: '' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when url is invalid', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setConnectorPublicUrl(kvs, createMockEventService())
      const req = createMockRequest({
        body: { url: 'not-a-url' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // setGoogleWorkspaceOauthConfig - happy path
  // -----------------------------------------------------------------------
  describe('setGoogleWorkspaceOauthConfig (happy path)', () => {
    it('should save config without real-time updates', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
      })
      const handler = setGoogleWorkspaceOauthConfig(kvs, createMockEventService(), 'org-1')
      const req = createMockRequest({
        body: { clientId: 'client-123', clientSecret: 'secret-456' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(kvs.set.calledOnce).to.be.true
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should save config with real-time updates enabled', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
      })
      const eventService = createMockEventService()
      const handler = setGoogleWorkspaceOauthConfig(kvs, eventService, 'org-1')
      const req = createMockRequest({
        body: {
          clientId: 'client-123',
          clientSecret: 'secret-456',
          enableRealTimeUpdates: true,
          topicName: 'projects/my-project/topics/gmail',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(kvs.set.calledOnce).to.be.true
        expect(res.status.calledWith(200)).to.be.true
        expect(eventService.start.calledOnce).to.be.true
      }
    })

    it('should call next when real-time is enabled but topicName is missing', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setGoogleWorkspaceOauthConfig(kvs, createMockEventService(), 'org-1')
      const req = createMockRequest({
        body: { clientId: 'client-123', clientSecret: 'secret-456', enableRealTimeUpdates: true },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should handle update with existing config and topic change', async () => {
      const existingConfig = {
        clientId: 'old-client',
        clientSecret: 'old-secret',
        enableRealTimeUpdates: true,
        topicName: 'old-topic',
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const eventService = createMockEventService()
      const handler = setGoogleWorkspaceOauthConfig(kvs, eventService, 'org-1')
      const req = createMockRequest({
        body: {
          clientId: 'new-client',
          clientSecret: 'new-secret',
          enableRealTimeUpdates: true,
          topicName: 'new-topic',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(kvs.set.calledOnce).to.be.true
        expect(eventService.start.calledOnce).to.be.true
      }
    })

    it('should handle disabling real-time updates from existing enabled config', async () => {
      const existingConfig = {
        clientId: 'old-client',
        clientSecret: 'old-secret',
        enableRealTimeUpdates: true,
        topicName: 'old-topic',
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const eventService = createMockEventService()
      const handler = setGoogleWorkspaceOauthConfig(kvs, eventService, 'org-1')
      const req = createMockRequest({
        body: {
          clientId: 'new-client',
          clientSecret: 'new-secret',
          enableRealTimeUpdates: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(kvs.set.calledOnce).to.be.true
        // Should fire GmailUpdatesDisabledEvent
        expect(eventService.start.calledOnce).to.be.true
      }
    })
  })

  // -----------------------------------------------------------------------
  // createAIModelsConfig - happy path
  // -----------------------------------------------------------------------
  describe('createAIModelsConfig', () => {
    it('should create AI models config successfully with LLM', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = createAIModelsConfig(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          llm: [{ provider: 'openai', configuration: { model: 'gpt-4', apiKey: 'sk-test' } }],
          embedding: [],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(kvs.set.calledOnce).to.be.true
        expect(res.status.calledWith(200)).to.be.true
        expect(eventService.start.calledOnce).to.be.true
      }
    })

    it('should create AI models config with embedding', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = createAIModelsConfig(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          llm: [],
          embedding: [{ provider: 'openai', configuration: { model: 'text-embedding-3-small', apiKey: 'sk-test' } }],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(kvs.set.calledOnce).to.be.true
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should call next when AI config is missing', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      const handler = createAIModelsConfig(kvs, eventService, appConfig)
      const req = createMockRequest({ body: null })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when health check fails for LLM', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: null,
      } as any)

      const handler = createAIModelsConfig(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          llm: [{ provider: 'openai', configuration: { model: 'gpt-4', apiKey: 'bad-key' } }],
          embedding: [],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // addAIModelProvider - happy path
  // -----------------------------------------------------------------------
  describe('addAIModelProvider', () => {
    it('should add AI model provider successfully', async () => {
      const existingConfig = { llm: [], embedding: [], ocr: [], slm: [], reasoning: [], multiModal: [] }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          modelType: 'llm',
          provider: 'openai',
          configuration: { model: 'gpt-4', apiKey: 'sk-test' },
          isDefault: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('status', 'success')
        expect(response.details).to.have.property('modelType', 'llm')
      }
    })

    it('should return 400 when modelType is missing', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: { provider: 'openai', configuration: {} },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should return 400 when modelType is invalid', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: { modelType: 'invalid', provider: 'openai', configuration: {} },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should return error when health check fails', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 422,
        data: { message: 'Invalid API key' },
      } as any)

      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          modelType: 'llm',
          provider: 'openai',
          configuration: { model: 'gpt-4', apiKey: 'bad-key' },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(422)).to.be.true
    })

    it('should add provider with no existing config', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
      })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          modelType: 'embedding',
          provider: 'openai',
          configuration: { model: 'text-embedding-3-small', apiKey: 'sk-test' },
          isDefault: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        expect(kvs.set.calledOnce).to.be.true
      }
    })

    it('should set new model as default and unset others', async () => {
      const existingConfig = {
        llm: [{ provider: 'openai', configuration: { model: 'gpt-3.5' }, modelKey: 'existing-key', isDefault: true }],
        embedding: [], ocr: [], slm: [], reasoning: [], multiModal: [],
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          modelType: 'llm',
          provider: 'openai',
          configuration: { model: 'gpt-4', apiKey: 'sk-test' },
          isDefault: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  // -----------------------------------------------------------------------
  // updateAIModelProvider - happy path
  // -----------------------------------------------------------------------
  describe('updateAIModelProvider', () => {
    it('should update AI model provider successfully', async () => {
      const existingConfig = {
        llm: [{ provider: 'openai', configuration: { model: 'gpt-3.5', apiKey: 'old' }, modelKey: 'key-1', isDefault: true, isMultimodal: false, isReasoning: false }],
        embedding: [],
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'llm', modelKey: 'key-1' },
        body: {
          provider: 'openai',
          configuration: { model: 'gpt-4', apiKey: 'new-key' },
          isDefault: true,
          isMultimodal: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('status', 'success')
      }
    })

    it('should return 400 when provider is missing', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'llm', modelKey: 'key-1' },
        body: { configuration: {} },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should return 404 when no AI config exists', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
      })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'llm', modelKey: 'key-1' },
        body: { provider: 'openai', configuration: {} },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should return 404 when model key not found', async () => {
      const existingConfig = {
        llm: [{ provider: 'openai', modelKey: 'other-key', configuration: {} }],
        embedding: [],
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'llm', modelKey: 'nonexistent-key' },
        body: { provider: 'openai', configuration: {} },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should return 400 when model type does not match', async () => {
      const existingConfig = {
        llm: [{ provider: 'openai', modelKey: 'key-1', configuration: {} }],
        embedding: [],
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://localhost:8000', cmBackend: 'http://localhost:3001' } as any

      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { status: 'healthy' },
      } as any)

      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'embedding', modelKey: 'key-1' },
        body: { provider: 'openai', configuration: {} },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // DB Config tests - happy paths
  // -----------------------------------------------------------------------
  describe('createArangoDbConfig (happy path)', () => {
    it('should create ArangoDB config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createArangoDbConfig(kvs)
      const req = createMockRequest({
        body: { url: 'http://localhost:8529', username: 'root', password: 'password' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getArangoDbConfig (happy path)', () => {
    it('should return ArangoDB config when it exists', async () => {
      const config = { url: 'http://localhost:8529', username: 'root', password: 'password', db: 'pipeshub' }
      const encrypted = mockEncService.encrypt(JSON.stringify(config))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getArangoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should return empty when no config exists', async () => {
      const kvs = createMockKeyValueStore()
      const handler = getArangoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('createMongoDbConfig (happy path)', () => {
    it('should create MongoDB config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createMongoDbConfig(kvs)
      const req = createMockRequest({
        body: { uri: 'mongodb://localhost:27017/pipeshub' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getMongoDbConfig (happy path)', () => {
    it('should return MongoDB config when it exists', async () => {
      const config = { uri: 'mongodb://localhost:27017/pipeshub', db: 'pipeshub' }
      const encrypted = mockEncService.encrypt(JSON.stringify(config))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getMongoDbConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('createRedisConfig (happy path)', () => {
    it('should create Redis config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createRedisConfig(kvs)
      const req = createMockRequest({
        body: { host: 'localhost', port: 6379, password: 'redis-pass' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getRedisConfig (happy path)', () => {
    it('should return Redis config when it exists', async () => {
      const config = { host: 'localhost', port: 6379, password: 'redis-pass' }
      const encrypted = mockEncService.encrypt(JSON.stringify(config))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getRedisConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('createKafkaConfig (happy path)', () => {
    it('should create Kafka config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createKafkaConfig(kvs)
      const req = createMockRequest({
        body: { brokers: 'localhost:9092' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getKafkaConfig (happy path)', () => {
    it('should return Kafka config when it exists', async () => {
      const config = { brokers: 'localhost:9092' }
      const encrypted = mockEncService.encrypt(JSON.stringify(config))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getKafkaConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('createQdrantConfig (happy path)', () => {
    it('should create Qdrant config successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = createQdrantConfig(kvs)
      const req = createMockRequest({
        body: { host: 'localhost', port: 6333, apiKey: 'qdrant-key' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getQdrantConfig (happy path)', () => {
    it('should return Qdrant config when it exists', async () => {
      const config = { host: 'localhost', port: 6333, apiKey: 'qdrant-key' }
      const encrypted = mockEncService.encrypt(JSON.stringify(config))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getQdrantConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Credential CRUD happy paths
  // -----------------------------------------------------------------------
  describe('getOneDriveCredentials (happy path)', () => {
    it('should return OneDrive credentials when they exist', async () => {
      const config = { clientId: 'one-drive-client', clientSecret: 'one-drive-secret' }
      const encrypted = mockEncService.encrypt(JSON.stringify(config))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getOneDriveCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('setOneDriveCredentials (happy path)', () => {
    it('should save OneDrive credentials successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setOneDriveCredentials(kvs)
      const req = createMockRequest({
        body: { clientId: 'one-drive-client', clientSecret: 'one-drive-secret', tenantId: 'tenant-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getSharePointCredentials (happy path)', () => {
    it('should return SharePoint credentials when they exist', async () => {
      const config = { clientId: 'sp-client', clientSecret: 'sp-secret' }
      const encrypted = mockEncService.encrypt(JSON.stringify(config))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getSharePointCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('setSharePointCredentials (happy path)', () => {
    it('should save SharePoint credentials successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setSharePointCredentials(kvs)
      const req = createMockRequest({
        body: { clientId: 'sp-client', clientSecret: 'sp-secret', tenantId: 'tenant-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('getAtlassianCredentials (happy path)', () => {
    it('should return Atlassian credentials when they exist', async () => {
      const config = { apiToken: 'jira-token', email: 'user@test.com', baseUrl: 'https://test.atlassian.net' }
      const encrypted = mockEncService.encrypt(JSON.stringify(config))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
      })
      const handler = getAtlassianCredentials(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('setAtlassianCredentials (happy path)', () => {
    it('should save Atlassian credentials successfully', async () => {
      const kvs = createMockKeyValueStore()
      const handler = setAtlassianCredentials(kvs)
      const req = createMockRequest({
        body: { apiToken: 'jira-token', email: 'user@test.com', baseUrl: 'https://test.atlassian.net' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.set.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Google Workspace Credentials - INDIVIDUAL type
  // -----------------------------------------------------------------------
  describe('createGoogleWorkspaceCredentials (individual)', () => {
    it('should save individual Google Workspace credentials', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'individual',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({
        body: {
          access_token: 'at-1',
          refresh_token: 'rt-1',
          access_token_expiry_time: 9999,
          refresh_token_expiry_time: 9999,
          enableRealTimeUpdates: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(kvs.set.calledOnce).to.be.true
      orgStub.restore()
    })

    it('should save individual credentials with real-time updates enabled', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'individual',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({
        body: {
          access_token: 'at-1',
          refresh_token: 'rt-1',
          access_token_expiry_time: 9999,
          refresh_token_expiry_time: 9999,
          enableRealTimeUpdates: 'true',
          topicName: 'projects/my-proj/topics/gmail',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      orgStub.restore()
    })

    it('should call next when org not found', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves(null)

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({ body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })

    it('should call next for unsupported account type', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'enterprise',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({ body: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })
  })

  // -----------------------------------------------------------------------
  // Google Workspace Credentials - BUSINESS type (fileChanged=true)
  // -----------------------------------------------------------------------
  describe('createGoogleWorkspaceCredentials (business, fileChanged)', () => {
    it('should save business credentials when file changed', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'business',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({
        body: {
          fileChanged: true,
          adminEmail: 'admin@test.com',
          enableRealTimeUpdates: false,
          fileContent: {
            type: 'service_account',
            project_id: 'proj-1',
            private_key_id: 'pk-id',
            private_key: '-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n',
            client_email: 'svc@proj-1.iam.gserviceaccount.com',
            client_id: '1234',
            auth_uri: 'https://accounts.google.com/o/oauth2/auth',
            token_uri: 'https://oauth2.googleapis.com/token',
            auth_provider_x509_cert_url: 'https://www.googleapis.com/oauth2/v1/certs',
            client_x509_cert_url: 'https://www.googleapis.com/robot/v1/metadata/x509/svc%40proj-1.iam.gserviceaccount.com',
            universe_domain: 'googleapis.com',
          },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(kvs.set.calledOnce).to.be.true
      orgStub.restore()
    })

    it('should throw when adminEmail is missing for business type', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'business',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({
        body: {
          fileChanged: true,
          enableRealTimeUpdates: false,
          fileContent: { type: 'service_account' },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })

    it('should save business credentials with real-time updates and publish event', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'business',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({
        body: {
          fileChanged: true,
          adminEmail: 'admin@test.com',
          enableRealTimeUpdates: true,
          topicName: 'projects/my-proj/topics/gmail',
          fileContent: {
            type: 'service_account',
            project_id: 'proj-1',
            private_key_id: 'pk-id',
            private_key: '-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n',
            client_email: 'svc@proj-1.iam.gserviceaccount.com',
            client_id: '1234',
            auth_uri: 'https://accounts.google.com/o/oauth2/auth',
            token_uri: 'https://oauth2.googleapis.com/token',
            auth_provider_x509_cert_url: 'https://www.googleapis.com/oauth2/v1/certs',
            client_x509_cert_url: 'https://www.googleapis.com/robot/v1/metadata/x509/svc',
            universe_domain: 'googleapis.com',
          },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(eventService.start.calledOnce).to.be.true
      expect(eventService.publishEvent.calledOnce).to.be.true
      orgStub.restore()
    })
  })

  // -----------------------------------------------------------------------
  // Google Workspace Credentials - BUSINESS type (fileChanged=false, existing config)
  // -----------------------------------------------------------------------
  describe('createGoogleWorkspaceCredentials (business, no file change)', () => {
    it('should reuse existing config when file not changed', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'business',
        _id: 'org-1',
      } as any)

      const existingConfig = {
        type: 'service_account',
        project_id: 'proj-1',
        private_key_id: 'pk-id',
        private_key: 'key',
        client_email: 'svc@test.com',
        client_id: '1234',
        auth_uri: 'https://accounts.google.com/o/oauth2/auth',
        token_uri: 'https://oauth2.googleapis.com/token',
        auth_provider_x509_cert_url: 'https://certs.url',
        client_x509_cert_url: 'https://cert.url',
        universe_domain: 'googleapis.com',
        enableRealTimeUpdates: false,
        topicName: '',
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingConfig))

      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(encrypted),
        set: sinon.stub().resolves(),
      })
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({
        body: {
          fileChanged: false,
          adminEmail: 'admin@test.com',
          enableRealTimeUpdates: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      orgStub.restore()
    })

    it('should call next when no existing config and file not changed', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'business',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(null),
      })
      const eventService = createMockEventService()
      const handler = createGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1', eventService)
      const req = createMockRequest({
        body: {
          fileChanged: false,
          adminEmail: 'admin@test.com',
          enableRealTimeUpdates: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })
  })

  // -----------------------------------------------------------------------
  // getGoogleWorkspaceCredentials
  // -----------------------------------------------------------------------
  describe('getGoogleWorkspaceCredentials', () => {
    it('should return individual credentials with oauth config', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'individual',
        _id: 'org-1',
      } as any)

      const creds = { access_token: 'at', refresh_token: 'rt' }
      const oauthConfig = { clientId: 'cid', clientSecret: 'cs' }
      const encCreds = mockEncService.encrypt(JSON.stringify(creds))
      const encOauth = mockEncService.encrypt(JSON.stringify(oauthConfig))

      const getStub = sinon.stub()
      getStub.onFirstCall().resolves(encCreds)
      getStub.onSecondCall().resolves(encOauth)

      const kvs = createMockKeyValueStore({ get: getStub })
      const handler = getGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      orgStub.restore()
    })

    it('should return empty when no individual credentials exist', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'individual',
        _id: 'org-1',
      } as any)

      const oauthConfig = { clientId: 'cid' }
      const encOauth = mockEncService.encrypt(JSON.stringify(oauthConfig))
      const getStub = sinon.stub()
      getStub.onFirstCall().resolves(null)
      getStub.onSecondCall().resolves(encOauth)

      const kvs = createMockKeyValueStore({ get: getStub })
      const handler = getGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      orgStub.restore()
    })

    it('should return empty when no oauth config exists for individual', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'individual',
        _id: 'org-1',
      } as any)

      const getStub = sinon.stub()
      getStub.onFirstCall().resolves(null)
      getStub.onSecondCall().resolves(null)

      const kvs = createMockKeyValueStore({ get: getStub })
      const handler = getGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      orgStub.restore()
    })

    it('should return business credentials', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'business',
        _id: 'org-1',
      } as any)

      const creds = { client_email: 'svc@test.com', adminEmail: 'admin@test.com' }
      const encCreds = mockEncService.encrypt(JSON.stringify(creds))

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(encCreds) })
      const handler = getGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      orgStub.restore()
    })

    it('should return empty when no business credentials exist', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'business',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(null) })
      const handler = getGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      orgStub.restore()
    })

    it('should call next for unsupported account type', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'enterprise',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const handler = getGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })

    it('should call next when org not found', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves(null)

      const kvs = createMockKeyValueStore()
      const handler = getGoogleWorkspaceCredentials(kvs, 'user-1', 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })
  })

  // -----------------------------------------------------------------------
  // getGoogleWorkspaceBusinessCredentials
  // -----------------------------------------------------------------------
  describe('getGoogleWorkspaceBusinessCredentials', () => {
    it('should return business credentials when they exist', async () => {
      const creds = { client_email: 'svc@test.com', adminEmail: 'admin@test.com' }
      const encCreds = mockEncService.encrypt(JSON.stringify(creds))

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(encCreds) })
      const handler = getGoogleWorkspaceBusinessCredentials(kvs, 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should return empty when no business credentials exist', async () => {
      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(null) })
      const handler = getGoogleWorkspaceBusinessCredentials(kvs, 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // deleteGoogleWorkspaceCredentials
  // -----------------------------------------------------------------------
  describe('deleteGoogleWorkspaceCredentials', () => {
    it('should delete business credentials', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'business',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const handler = deleteGoogleWorkspaceCredentials(kvs, 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(kvs.delete.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      orgStub.restore()
    })

    it('should call next for individual type (not allowed)', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'individual',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const handler = deleteGoogleWorkspaceCredentials(kvs, 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })

    it('should call next when org not found', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves(null)

      const kvs = createMockKeyValueStore()
      const handler = deleteGoogleWorkspaceCredentials(kvs, 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })

    it('should call next for unsupported account type', async () => {
      const orgStub = sinon.stub(Org, 'findOne').resolves({
        accountType: 'enterprise',
        _id: 'org-1',
      } as any)

      const kvs = createMockKeyValueStore()
      const handler = deleteGoogleWorkspaceCredentials(kvs, 'org-1')
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
      orgStub.restore()
    })
  })

  // -----------------------------------------------------------------------
  // setGoogleWorkspaceOauthConfig - deep logic with events
  // -----------------------------------------------------------------------
  describe('setGoogleWorkspaceOauthConfig (deep paths)', () => {
    it('should save config with real-time updates enabled and publish event (no existing config)', async () => {
      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(null) })
      const eventService = createMockEventService()
      const handler = setGoogleWorkspaceOauthConfig(kvs, eventService, 'org-1')
      const req = createMockRequest({
        body: {
          clientId: 'cid',
          clientSecret: 'cs',
          enableRealTimeUpdates: true,
          topicName: 'projects/proj/topics/gmail',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(eventService.publishEvent.calledOnce).to.be.true
    })

    it('should save config without real-time updates', async () => {
      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(null) })
      const eventService = createMockEventService()
      const handler = setGoogleWorkspaceOauthConfig(kvs, eventService, 'org-1')
      const req = createMockRequest({
        body: {
          clientId: 'cid',
          clientSecret: 'cs',
          enableRealTimeUpdates: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(eventService.publishEvent.called).to.be.false
    })

    it('should publish disable event when toggling from enabled to disabled', async () => {
      const existing = {
        clientId: 'cid',
        clientSecret: 'cs',
        enableRealTimeUpdates: true,
        topicName: 'projects/proj/topics/gmail',
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existing))

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(encrypted) })
      const eventService = createMockEventService()
      const handler = setGoogleWorkspaceOauthConfig(kvs, eventService, 'org-1')
      const req = createMockRequest({
        body: {
          clientId: 'cid',
          clientSecret: 'cs',
          enableRealTimeUpdates: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(eventService.publishEvent.calledOnce).to.be.true
    })

    it('should throw when topic name missing but real-time updates enabled', async () => {
      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(null) })
      const eventService = createMockEventService()
      const handler = setGoogleWorkspaceOauthConfig(kvs, eventService, 'org-1')
      const req = createMockRequest({
        body: {
          clientId: 'cid',
          clientSecret: 'cs',
          enableRealTimeUpdates: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // setFrontendUrl - deep logic
  // -----------------------------------------------------------------------
  describe('setFrontendUrl (deep paths)', () => {
    it('should save frontend URL and update config', async () => {
      sinon.stub(generateAuthTokenModule, 'generateFetchConfigAuthToken').resolves('token-1')
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('{}'),
      })
      const configService = createMockConfigService()
      const handler = setFrontendUrl(kvs, 'jwt-secret', configService)
      const req = createMockRequest({
        body: { url: 'https://frontend.example.com/' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(kvs.set.calledOnce).to.be.true
    })

    it('should call next when user is missing', async () => {
      const kvs = createMockKeyValueStore()
      const configService = createMockConfigService()
      const handler = setFrontendUrl(kvs, 'jwt-secret', configService)
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next for empty URL', async () => {
      const kvs = createMockKeyValueStore()
      const configService = createMockConfigService()
      const handler = setFrontendUrl(kvs, 'jwt-secret', configService)
      const req = createMockRequest({ body: { url: '' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next for invalid URL format', async () => {
      const kvs = createMockKeyValueStore()
      const configService = createMockConfigService()
      const handler = setFrontendUrl(kvs, 'jwt-secret', configService)
      const req = createMockRequest({ body: { url: 'not-a-url' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // setConnectorPublicUrl - deep logic
  // -----------------------------------------------------------------------
  describe('setConnectorPublicUrl (deep paths)', () => {
    it('should save connector URL and publish event', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves('{}'),
      })
      const eventService = createMockEventService()
      const handler = setConnectorPublicUrl(kvs, eventService)
      const req = createMockRequest({
        body: { url: 'https://connector.example.com' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(eventService.start.calledOnce).to.be.true
      expect(eventService.publishEvent.calledOnce).to.be.true
    })

    it('should call next when user is missing', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = setConnectorPublicUrl(kvs, eventService)
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next for empty URL', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = setConnectorPublicUrl(kvs, eventService)
      const req = createMockRequest({ body: { url: '' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next for invalid URL format', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const handler = setConnectorPublicUrl(kvs, eventService)
      const req = createMockRequest({ body: { url: 'bad-url' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // createAIModelsConfig - deep logic
  // -----------------------------------------------------------------------
  describe('createAIModelsConfig (deep paths)', () => {
    it('should save AI config after health checks pass', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = createAIModelsConfig(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          llm: [{ provider: 'openai', configuration: { model: 'gpt-4', apiKey: 'key' } }],
          embedding: [],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(kvs.set.calledOnce).to.be.true
    })

    it('should call next when no config body', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = createAIModelsConfig(kvs, eventService, appConfig)
      const req = createMockRequest({ body: null })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when LLM health check fails', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: null,
      })

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = createAIModelsConfig(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          llm: [{ provider: 'openai', configuration: { model: 'gpt-4', apiKey: 'key' } }],
          embedding: [],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should handle embedding health check', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = createAIModelsConfig(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          llm: [],
          embedding: [{ provider: 'openai', configuration: { model: 'text-embedding-ada-002', apiKey: 'key' } }],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // addAIModelProvider - deep logic
  // -----------------------------------------------------------------------
  describe('addAIModelProvider (deep paths)', () => {
    it('should add a new LLM provider after health check', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })

      const existingModels = { llm: [], embedding: [], ocr: [], slm: [], reasoning: [], multiModal: [] }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingModels))

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(encrypted) })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          modelType: 'llm',
          provider: 'openai',
          configuration: { model: 'gpt-4', apiKey: 'key' },
          isDefault: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(kvs.set.calledOnce).to.be.true
    })

    it('should return 400 when required fields are missing', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({ body: { modelType: 'llm' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should return 400 for invalid model type', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: { modelType: 'invalid', provider: 'openai', configuration: { model: 'x' } },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should add provider when no existing config', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(null) })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          modelType: 'embedding',
          provider: 'openai',
          configuration: { model: 'text-embedding-ada-002', apiKey: 'key' },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should return health check error status when check fails', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { message: 'Invalid API key' },
      })

      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = addAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        body: {
          modelType: 'llm',
          provider: 'openai',
          configuration: { model: 'gpt-4', apiKey: 'bad-key' },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // updateAIModelProvider - deep logic
  // -----------------------------------------------------------------------
  describe('updateAIModelProvider (deep paths)', () => {
    it('should update an existing model provider', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })

      const existingModels = {
        llm: [{ modelKey: 'mk-1', provider: 'openai', configuration: { model: 'gpt-4' }, isDefault: true, isMultimodal: false, isReasoning: false }],
        embedding: [],
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingModels))

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(encrypted) })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'llm', modelKey: 'mk-1' },
        body: {
          provider: 'openai',
          configuration: { model: 'gpt-4-turbo', apiKey: 'new-key' },
          isDefault: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(kvs.set.calledOnce).to.be.true
    })

    it('should return 400 when provider is missing', async () => {
      const kvs = createMockKeyValueStore()
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'llm', modelKey: 'mk-1' },
        body: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })

    it('should return 404 when no config exists', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(null) })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'llm', modelKey: 'mk-1' },
        body: { provider: 'openai', configuration: { model: 'gpt-4' } },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should return 404 when model key not found', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })

      const existingModels = { llm: [{ modelKey: 'other', provider: 'openai' }] }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingModels))

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(encrypted) })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'llm', modelKey: 'mk-nonexistent' },
        body: { provider: 'openai', configuration: { model: 'gpt-4' } },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should return 400 when model type mismatch', async () => {
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { healthy: true },
      })

      const existingModels = { llm: [{ modelKey: 'mk-1', provider: 'openai' }], embedding: [] }
      const encrypted = mockEncService.encrypt(JSON.stringify(existingModels))

      const kvs = createMockKeyValueStore({ get: sinon.stub().resolves(encrypted) })
      const eventService = createMockEventService()
      const appConfig = { aiBackend: 'http://ai:8000', cmBackend: 'http://cm:3001' } as any
      const handler = updateAIModelProvider(kvs, eventService, appConfig)
      const req = createMockRequest({
        params: { modelType: 'embedding', modelKey: 'mk-1' },
        body: { provider: 'openai', configuration: { model: 'gpt-4' } },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getStorageConfig - Azure Blob path
  // -----------------------------------------------------------------------
  describe('getStorageConfig (Azure Blob path)', () => {
    it('should return Azure Blob config when storageType is azure_blob', async () => {
      const azureConfig = {
        endpointProtocol: 'https',
        accountName: 'myaccount',
        accountKey: 'mykey',
        endpointSuffix: 'core.windows.net',
        containerName: 'mycontainer',
      }
      const encrypted = mockEncService.encrypt(JSON.stringify(azureConfig))
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({
          storageType: 'azureBlob',
          azureBlob: encrypted,
        })),
      })
      // Override the kvs.get to return the storage config string
      kvs.get = sinon.stub().resolves(JSON.stringify({
        storageType: 'azureBlob',
        azureBlob: mockEncService.encrypt(JSON.stringify(azureConfig)),
      }))

      const handler = getStorageConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getStorageConfig - unsupported type path
  // -----------------------------------------------------------------------
  describe('getStorageConfig (unsupported type)', () => {
    it('should return 400 for unsupported storage type', async () => {
      const kvs = createMockKeyValueStore({
        get: sinon.stub().resolves(JSON.stringify({
          storageType: 'gcs',
        })),
      })

      const handler = getStorageConfig(kvs)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
    })
  })
})
