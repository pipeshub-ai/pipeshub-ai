/// <reference types="mocha" />
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
    it('should call connectorSyncScheduleMigration, chatKbFiltersMigration, adminRoleMigration and documentOrgIdMigration', async () => {
      const service = new MigrationService(mockLogger, mockKeyValueStore)
      const mockScheduler = {
        scheduleJob: sinon.stub().resolves(),
        removeJob: sinon.stub().resolves(),
        getJobStatus: sinon.stub().resolves(null),
      }
      const mockAppConfig = {
        connectorBackend: 'http://localhost:8088',
      }
      // Stub migrations so they don't make real HTTP calls or DB queries
      const connectorStub = sinon.stub(service, 'connectorSyncScheduleMigration' as any).resolves()
      const chatStub = sinon.stub(service, 'chatKbFiltersMigration' as any).resolves()
      const adminStub = sinon.stub(service, 'adminRoleMigration' as any).resolves()
      const documentOrgIdStub = sinon.stub(service, 'documentOrgIdMigration' as any).resolves()

      await service.runMigration({ scheduler: mockScheduler as any, appConfig: mockAppConfig as any })

      expect(mockLogger.info.calledWith('Running migration...')).to.be.true
      expect(connectorStub.calledOnce).to.be.true
      expect(chatStub.calledOnce).to.be.true
      expect(adminStub.calledOnce).to.be.true
      expect(documentOrgIdStub.calledOnce).to.be.true
      expect(mockLogger.info.calledWith('✅ Migration completed')).to.be.true
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

  describe('adminRoleMigration', () => {
    let adminRoleMigrationStub: sinon.SinonStub

    beforeEach(() => {
      const AdminRoleMigration = require('../../../../src/modules/configuration_manager/services/migrations/admin_role.migration').AdminRoleMigration
      adminRoleMigrationStub = sinon.stub(AdminRoleMigration.prototype, 'run')
    })

    afterEach(() => {
      adminRoleMigrationStub.restore()
    })

    it('should run admin role migration successfully', async () => {
      adminRoleMigrationStub.resolves({
        adminGroupsProcessed: 2,
        usersPromoted: 3,
        usersDefaultedToMember: 5,
        adminGroupsSoftDeleted: 2,
        errored: 0,
      })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.adminRoleMigration()

      expect(adminRoleMigrationStub.calledOnce).to.be.true
      expect(mockLogger.info.calledWith('Migrating admin group membership to user.role')).to.be.true
      expect(mockLogger.info.calledWith('✅ Admin role migrated', sinon.match.object)).to.be.true
    })

    it('should warn when migration finishes with errors', async () => {
      adminRoleMigrationStub.resolves({
        adminGroupsProcessed: 2,
        usersPromoted: 1,
        usersDefaultedToMember: 0,
        adminGroupsSoftDeleted: 0,
        errored: 1,
      })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.adminRoleMigration()

      expect(mockLogger.warn.calledWith(
        '⚠️  Admin-role migration finished with errors — will retry on next boot',
        sinon.match.object,
      )).to.be.true
    })

    it('should catch and log migration errors', async () => {
      adminRoleMigrationStub.rejects(new Error('DB error'))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.adminRoleMigration()

      expect(mockLogger.error.calledWith('Admin-role migration failed', sinon.match.object)).to.be.true
    })

    it('should handle non-Error exceptions', async () => {
      adminRoleMigrationStub.callsFake(() => Promise.reject(42))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.adminRoleMigration()

      expect(mockLogger.error.calledOnce).to.be.true
      expect(mockLogger.error.firstCall.args[1].error).to.equal('Unknown error')
    })
  })

  describe('documentOrgIdMigration', () => {
    let documentOrgIdMigrationStub: sinon.SinonStub

    beforeEach(() => {
      const DocumentOrgIdBackfillMigration = require('../../../../src/modules/configuration_manager/services/migrations/document_orgid_backfill.migration').DocumentOrgIdBackfillMigration
      documentOrgIdMigrationStub = sinon.stub(DocumentOrgIdBackfillMigration.prototype, 'run')
    })

    afterEach(() => {
      documentOrgIdMigrationStub.restore()
    })

    it('should run document orgId migration successfully', async () => {
      documentOrgIdMigrationStub.resolves({
        updated: 10,
        errored: 0,
      })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdMigration()

      expect(documentOrgIdMigrationStub.calledOnce).to.be.true
      expect(mockLogger.info.calledWith('Migrating document orgId backfill')).to.be.true
      expect(mockLogger.info.calledWith('✅ Document orgId migrated', sinon.match.object)).to.be.true
    })

    it('should warn when migration finishes with errors', async () => {
      documentOrgIdMigrationStub.resolves({
        updated: 5,
        errored: 2,
      })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdMigration()

      expect(mockLogger.warn.calledWith(
        '⚠️  Document orgId migration finished with errors — will retry on next boot',
        sinon.match.object,
      )).to.be.true
    })

    it('should catch and log migration errors', async () => {
      documentOrgIdMigrationStub.rejects(new Error('Mongo down'))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdMigration()

      expect(mockLogger.error.calledWith('Document orgId migration failed', sinon.match.object)).to.be.true
    })

    it('should handle non-Error exceptions', async () => {
      documentOrgIdMigrationStub.rejects(42)

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdMigration()

      expect(mockLogger.error.calledOnce).to.be.true
      expect(mockLogger.error.firstCall.args[1].error).to.equal('Unknown error')
    })
  })

  describe('connectorSyncScheduleMigration', () => {
    let scheduledJobsMigrationStub: sinon.SinonStub
    let orgCountStub: sinon.SinonStub
    let mockScheduler: any
    let mockAppConfig: any

    beforeEach(() => {
      const ScheduledJobsBackfillMigration = require('../../../../src/modules/configuration_manager/services/migrations/scheduled_jobs_backfill.migration').ScheduledJobsBackfillMigration
      scheduledJobsMigrationStub = sinon.stub(ScheduledJobsBackfillMigration.prototype, 'run')
      const Org = require('../../../../src/modules/user_management/schema/org.schema').Org
      orgCountStub = sinon.stub(Org, 'countDocuments')
      mockScheduler = { scheduleJob: sinon.stub(), removeJob: sinon.stub() }
      mockAppConfig = { connectorBackend: 'http://localhost:8088' }
    })

    afterEach(() => {
      scheduledJobsMigrationStub.restore()
      orgCountStub.restore()
    })

    it('should skip migration on fresh install (no orgs)', async () => {
      orgCountStub.resolves(0)

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await (service as any).connectorSyncScheduleMigration(mockScheduler, mockAppConfig)

      expect(orgCountStub.calledOnce).to.be.true
      expect(mockKeyValueStore.set.calledOnce).to.be.true
      expect(scheduledJobsMigrationStub.called).to.be.false
      expect(mockLogger.info.calledWith(sinon.match(/fresh setup detected/))).to.be.true
    })

    it('should run migration when orgs exist', async () => {
      orgCountStub.resolves(3)
      scheduledJobsMigrationStub.resolves({ errored: 0 })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await (service as any).connectorSyncScheduleMigration(mockScheduler, mockAppConfig)

      expect(scheduledJobsMigrationStub.calledOnce).to.be.true
      expect(mockLogger.info.calledWith('✅ Connector sync schedules migrated', sinon.match.object)).to.be.true
    })

    it('should warn when migration finishes with errors', async () => {
      orgCountStub.resolves(1)
      scheduledJobsMigrationStub.resolves({ errored: 2 })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await (service as any).connectorSyncScheduleMigration(mockScheduler, mockAppConfig)

      expect(mockLogger.warn.calledWith(
        '⚠️  Connector sync schedule migration finished with errors — will retry on next boot',
        sinon.match.object,
      )).to.be.true
    })

    it('should catch and log migration errors', async () => {
      orgCountStub.resolves(1)
      scheduledJobsMigrationStub.rejects(new Error('Connection refused'))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await (service as any).connectorSyncScheduleMigration(mockScheduler, mockAppConfig)

      expect(mockLogger.error.calledWith('Connector sync schedule migration failed', sinon.match.object)).to.be.true
    })

    it('should handle non-Error exceptions', async () => {
      orgCountStub.resolves(1)
      scheduledJobsMigrationStub.callsFake(() => Promise.reject(42))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await (service as any).connectorSyncScheduleMigration(mockScheduler, mockAppConfig)

      expect(mockLogger.error.calledOnce).to.be.true
      expect(mockLogger.error.firstCall.args[1].error).to.equal('Unknown error')
    })
  })

  describe('chatKbFiltersMigration', () => {
    let chatKbFiltersMigrationStub: sinon.SinonStub

    beforeEach(() => {
      // Mock the ChatKbFiltersMigration class
      const ChatKbFiltersMigration = require('../../../../src/modules/configuration_manager/services/migrations/chat_kb_filters.migration').ChatKbFiltersMigration
      chatKbFiltersMigrationStub = sinon.stub(ChatKbFiltersMigration.prototype, 'run')
    })

    afterEach(() => {
      chatKbFiltersMigrationStub.restore()
    })

    it('should run chat KB-filters migration successfully', async () => {
      chatKbFiltersMigrationStub.resolves({
        conversationsUpdated: 5,
        messagesUpdated: 12,
        errored: 0,
      })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.chatKbFiltersMigration()

      expect(chatKbFiltersMigrationStub.calledOnce).to.be.true
      expect(mockLogger.info.calledWith('Migrating chat KB filters')).to.be.true
      expect(mockLogger.info.calledWith('✅ Chat KB filters migrated', sinon.match.object)).to.be.true
    })

    it('should warn when migration finishes with errors', async () => {
      chatKbFiltersMigrationStub.resolves({
        conversationsUpdated: 3,
        messagesUpdated: 8,
        errored: 2,
      })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.chatKbFiltersMigration()

      expect(mockLogger.warn.calledWith(
        '⚠️  Chat KB-filters migration finished with errors — will retry on next boot',
        sinon.match.object,
      )).to.be.true
    })

    it('should catch and log migration errors', async () => {
      chatKbFiltersMigrationStub.rejects(new Error('DB connection failed'))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.chatKbFiltersMigration()

      expect(mockLogger.error.calledWith('Chat KB-filters migration failed', sinon.match.object)).to.be.true
    })

    it('should handle non-Error exceptions', async () => {
      chatKbFiltersMigrationStub.rejects(42)

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.chatKbFiltersMigration()

      expect(mockLogger.error.calledOnce).to.be.true
      expect(mockLogger.error.firstCall.args[0]).to.equal('Chat KB-filters migration failed')
      expect(mockLogger.error.firstCall.args[1].error).to.equal('Unknown error')
    })
  })

  describe('documentOrgIdMigration', () => {
    let documentOrgIdMigrationStub: sinon.SinonStub

    beforeEach(() => {
      const DocumentOrgIdBackfillMigration = require('../../../../src/modules/configuration_manager/services/migrations/document_orgid_backfill.migration').DocumentOrgIdBackfillMigration
      documentOrgIdMigrationStub = sinon.stub(DocumentOrgIdBackfillMigration.prototype, 'run')
    })

    afterEach(() => {
      documentOrgIdMigrationStub.restore()
    })

    it('should run document orgId backfill successfully', async () => {
      documentOrgIdMigrationStub.resolves({
        updated: 5,
        errored: 0,
      })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdMigration()

      expect(documentOrgIdMigrationStub.calledOnce).to.be.true
      expect(mockLogger.info.calledWith('Migrating document orgId backfill')).to.be.true
      expect(mockLogger.info.calledWith('✅ Document orgId migrated', sinon.match.object)).to.be.true
    })

    it('should warn when migration finishes with errors', async () => {
      documentOrgIdMigrationStub.resolves({
        updated: 3,
        errored: 2,
      })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdMigration()

      expect(mockLogger.warn.calledWith(
        '⚠️  Document orgId migration finished with errors — will retry on next boot',
        sinon.match.object,
      )).to.be.true
    })

    it('should catch and log migration errors', async () => {
      documentOrgIdMigrationStub.rejects(new Error('DB connection failed'))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdMigration()

      expect(mockLogger.error.calledWith('Document orgId migration failed', sinon.match.object)).to.be.true
    })
  })
})
