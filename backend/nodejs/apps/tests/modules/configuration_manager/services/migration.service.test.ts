import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import * as docBackfillModule from '../../../../src/modules/configuration_manager/services/migrations/document_orgid_backfill.migration'
import * as scheduledBackfillModule from '../../../../src/modules/configuration_manager/services/migrations/scheduled_jobs_backfill.migration'
import { MigrationService } from '../../../../src/modules/configuration_manager/services/migration.service'
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'

describe('MigrationService', () => {
  let mockLogger: any
  let mockKeyValueStore: any
  let mockEncService: any

  const fakeConfig = {
    algorithm: 'aes-256-gcm',
    secretKey: 'a'.repeat(64),
    storeType: 'etcd',
    storeConfig: { host: 'localhost', port: 2379, dialTimeout: 2000 },
    redisConfig: { host: 'localhost', port: 6379 },
  }

  const mockScheduler: any = {
    scheduleJob: sinon.stub().resolves(),
    removeJob: sinon.stub().resolves(),
    getJobStatus: sinon.stub().resolves(null),
  }
  const mockAppConfig: any = { connectorBackend: 'http://localhost:8088' }

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

    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns(fakeConfig as any)
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns(mockEncService)
  })

  afterEach(() => {
    sinon.restore()
  })

  // -------------------------------------------------------------------------
  // constructor
  // -------------------------------------------------------------------------
  describe('constructor', () => {
    it('should create an instance', () => {
      const service = new MigrationService(mockLogger, mockKeyValueStore)
      expect(service).to.exist
    })
  })

  // -------------------------------------------------------------------------
  // runMigration
  // -------------------------------------------------------------------------
  describe('runMigration', () => {
    it('should call both sub-migrations and log start/complete messages', async () => {
      const service = new MigrationService(mockLogger, mockKeyValueStore)
      sinon.stub(service, 'connectorSyncScheduleMigration' as any).resolves()
      sinon.stub(service, 'documentOrgIdBackfillMigration' as any).resolves()

      await service.runMigration({ scheduler: mockScheduler, appConfig: mockAppConfig })

      expect(mockLogger.info.calledWith('Running migration...')).to.be.true
      expect(mockLogger.info.calledWith('✅ Migration completed')).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // documentOrgIdBackfillMigration
  // -------------------------------------------------------------------------
  describe('documentOrgIdBackfillMigration', () => {
    it('logs "skipped" info when the inner migration returns skipped=true', async () => {
      sinon
        .stub(docBackfillModule.DocumentOrgIdBackfillMigration.prototype, 'run')
        .resolves({ updated: 0, skipped: true })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdBackfillMigration()

      expect(mockLogger.error.called).to.be.false
      const infoCalls: string[] = mockLogger.info.args.map((a: any[]) => a[0])
      expect(infoCalls.some((m) => m.includes('skipped'))).to.be.true
    })

    it('logs "completed" info when the inner migration returns skipped=false', async () => {
      sinon
        .stub(docBackfillModule.DocumentOrgIdBackfillMigration.prototype, 'run')
        .resolves({ updated: 7, skipped: false })

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdBackfillMigration()

      expect(mockLogger.error.called).to.be.false
      const infoCalls: string[] = mockLogger.info.args.map((a: any[]) => a[0])
      expect(infoCalls.some((m) => m.includes('completed'))).to.be.true
    })

    it('catches the thrown error, logs it, and does not re-throw', async () => {
      sinon
        .stub(docBackfillModule.DocumentOrgIdBackfillMigration.prototype, 'run')
        .rejects(new Error('mongo down'))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.documentOrgIdBackfillMigration() // must not throw

      expect(mockLogger.error.calledOnce).to.be.true
      const [msg, meta] = mockLogger.error.firstCall.args
      expect(msg).to.include('Document orgId backfill migration failed')
      expect(meta.error).to.equal('mongo down')
    })
  })

  // -------------------------------------------------------------------------
  // connectorSyncScheduleMigration
  // -------------------------------------------------------------------------
  describe('connectorSyncScheduleMigration', () => {
    it('marks migration done and returns early on fresh install (no orgs)', async () => {
      sinon.stub(Org, 'countDocuments').resolves(0)

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.connectorSyncScheduleMigration(mockScheduler, mockAppConfig)

      expect(
        mockKeyValueStore.set.calledOnceWith(
          configPaths.connectorSyncScheduledJobsMigration,
          'true',
        ),
      ).to.be.true
      const infoCalls: string[] = mockLogger.info.args.map((a: any[]) => a[0])
      expect(infoCalls.some((m) => m.includes('fresh setup'))).to.be.true
    })

    it('logs success info when ScheduledJobsBackfillMigration completes without errors', async () => {
      sinon.stub(Org, 'countDocuments').resolves(1)
      sinon
        .stub(scheduledBackfillModule.ScheduledJobsBackfillMigration.prototype, 'run')
        .resolves({ scheduled: 2, skipped: 0, errored: 0 } as any)

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.connectorSyncScheduleMigration(mockScheduler, mockAppConfig)

      const infoCalls: string[] = mockLogger.info.args.map((a: any[]) => a[0])
      expect(infoCalls.some((m) => m.includes('migrated'))).to.be.true
      expect(mockLogger.warn.called).to.be.false
    })

    it('logs warning when ScheduledJobsBackfillMigration finishes with errors', async () => {
      sinon.stub(Org, 'countDocuments').resolves(1)
      sinon
        .stub(scheduledBackfillModule.ScheduledJobsBackfillMigration.prototype, 'run')
        .resolves({ scheduled: 1, skipped: 0, errored: 1 } as any)

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.connectorSyncScheduleMigration(mockScheduler, mockAppConfig)

      expect(mockLogger.warn.calledOnce).to.be.true
    })

    it('catches thrown errors from ScheduledJobsBackfillMigration and logs them', async () => {
      sinon.stub(Org, 'countDocuments').resolves(1)
      sinon
        .stub(scheduledBackfillModule.ScheduledJobsBackfillMigration.prototype, 'run')
        .rejects(new Error('backfill exploded'))

      const service = new MigrationService(mockLogger, mockKeyValueStore)
      await service.connectorSyncScheduleMigration(mockScheduler, mockAppConfig) // must not throw

      expect(mockLogger.error.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // aiModelsMigration
  // -------------------------------------------------------------------------
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
