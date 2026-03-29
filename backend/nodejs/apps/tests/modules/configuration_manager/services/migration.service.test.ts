import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import { MigrationService } from '../../../../src/modules/configuration_manager/services/migration.service'

describe('MigrationService', () => {
  let mockLogger: any
  let mockKeyValueStore: any
  let mockEncService: any
  let loadConfigStub: sinon.SinonStub

  const fakeConfig = {
    algorithm: 'aes-256-gcm',
    secretKey: 'a'.repeat(64),
    storeType: 'etcd',
    storeConfig: { host: 'localhost', port: 2379, dialTimeout: 2000 },
    redisConfig: { host: 'localhost', port: 6379 },
  }

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }

    mockKeyValueStore = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
    }

    mockEncService = {
      encrypt: sinon.stub().callsFake((val: string) => `encrypted:${val}`),
      decrypt: sinon.stub().callsFake((val: string) => val.replace('encrypted:', '')),
    }

    loadConfigStub = sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns(fakeConfig as any)
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns(mockEncService)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('constructor', () => {
    it('should create an instance', () => {
      const service = new MigrationService(mockLogger, mockKeyValueStore)
      expect(service).to.exist
    })
  })

  describe('runMigration', () => {
    it('should call aiModelsMigration', async () => {
      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.runMigration()

      expect(mockLogger.info.calledWith('Running migration...')).to.be.true
    })
  })

  describe('aiModelsMigration', () => {
    it('should return early when no AI config exists', async () => {
      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.aiModelsMigration()

      expect(mockKeyValueStore.set.called).to.be.false
      expect(mockLogger.info.calledWith('No ai models configurations found')).to.be.true
    })

    it('should add modelKey to LLM configs that lack one', async () => {
      const aiModels = {
        llm: [
          { provider: 'openai', configuration: { model: 'gpt-4' } },
          { provider: 'anthropic', configuration: { model: 'claude' } },
        ],
        embedding: [
          { provider: 'openai', configuration: { model: 'ada' } },
        ],
      }
      mockEncService.decrypt.returns(JSON.stringify(aiModels))
      mockKeyValueStore.get.resolves('encrypted:data')

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.aiModelsMigration()

      expect(mockKeyValueStore.set.calledOnce).to.be.true
      // Verify the encrypted data that was set contains modelKeys
      const setArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(setArg)
      expect(parsed.llm[0]).to.have.property('modelKey')
      expect(parsed.llm[0].isDefault).to.be.true
      expect(parsed.llm[1].isDefault).to.be.false
      expect(parsed.embedding[0]).to.have.property('modelKey')
      expect(parsed.embedding[0].isDefault).to.be.true
    })

    it('should skip configs that already have modelKey', async () => {
      const aiModels = {
        llm: [
          { provider: 'openai', configuration: { model: 'gpt-4' }, modelKey: 'existing-key' },
        ],
        embedding: [
          { provider: 'openai', configuration: { model: 'ada' }, modelKey: 'existing-key-2' },
        ],
      }
      mockEncService.decrypt.returns(JSON.stringify(aiModels))
      mockKeyValueStore.get.resolves('encrypted:data')

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.aiModelsMigration()

      expect(mockKeyValueStore.set.calledOnce).to.be.true
      const setArg = mockEncService.encrypt.firstCall.args[0]
      const parsed = JSON.parse(setArg)
      expect(parsed.llm[0].modelKey).to.equal('existing-key')
    })
  })
})
