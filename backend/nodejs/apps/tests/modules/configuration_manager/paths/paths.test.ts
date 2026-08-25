/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths'

describe('configPaths', () => {
  it('should export a non-empty config object', () => {
    expect(configPaths).to.be.an('object')
    expect(Object.keys(configPaths).length).to.be.greaterThan(0)
  })

  it('should have correct top-level paths', () => {
    expect(configPaths.secretKeys).to.equal('/services/secretKeys')
    expect(configPaths.smtp).to.equal('/services/smtp')
    expect(configPaths.aiModels).to.equal('/services/aiModels')
    expect(configPaths.storageService).to.equal('/services/storage')
  })

  it('should have migration paths', () => {
    expect(configPaths.connectorSyncScheduledJobsMigration).to.equal('/migrations/connector_sync_scheduled_jobs')
    expect(configPaths.chatKbFiltersMigration).to.equal('/migrations/chat_kb_filters_v1')
    expect(configPaths.adminRoleMigration).to.equal('/migrations/admin_role_v1')
    expect(configPaths.documentOrgIdMigration).to.equal('/migrations/document_orgid_v1')
    expect(configPaths.kbAppsMigrationDone).to.equal('/migrations/kb_apps_v1')
  })

  it('should have nested connector paths', () => {
    expect(configPaths.connectors.googleWorkspace.base).to.be.a('string')
    expect(configPaths.connectors.atlassian.credentials).to.be.a('string')
    expect(configPaths.connectors.onedrive.config).to.be.a('string')
    expect(configPaths.connectors.sharepoint.config).to.be.a('string')
  })

  it('should have auth paths', () => {
    expect(configPaths.auth.base).to.equal('/services/auth')
    expect(configPaths.auth.azureAD).to.be.a('string')
    expect(configPaths.auth.google).to.be.a('string')
    expect(configPaths.auth.github).to.be.a('string')
    expect(configPaths.auth.oauth).to.be.a('string')
  })

  it('should have url paths', () => {
    expect(configPaths.url.auth).to.be.a('string')
    expect(configPaths.url.storage).to.be.a('string')
    expect(configPaths.url.frontend).to.be.a('string')
  })

  it('should have db paths', () => {
    expect(configPaths.db.mongodb).to.be.a('string')
    expect(configPaths.db.arangodb).to.be.a('string')
    expect(configPaths.db.qdrant).to.be.a('string')
  })

  it('should have broker paths', () => {
    expect(configPaths.broker.kafka).to.be.a('string')
    expect(configPaths.broker.redisStreams).to.be.a('string')
    expect(configPaths.broker.messageBroker).to.be.a('string')
  })

  it('should have web search and slack bot paths', () => {
    expect(configPaths.webSearch).to.equal('/services/webSearch')
    expect(configPaths.slackBot).to.equal('/services/slackBot')
  })

  it('should have platform settings path', () => {
    expect(configPaths.platform.settings).to.equal('/services/platform/settings')
  })
})
