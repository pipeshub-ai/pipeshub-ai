import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'
import {
  getGoogleWorkspaceBusinessCredentials,
  getGoogleWorkspaceIndividualCredentials,
  getGoogleWorkspaceConfig,
  getAtlassianOauthConfig,
  getOneDriveConfig,
  getSharePointConfig,
  getRefreshTokenCredentials,
  getRefreshTokenConfig,
  setGoogleWorkspaceBusinessCredentials,
  deleteGoogleWorkspaceCredentials,
  setOneDriveConfig,
  setSharePointConfig,
  setAtlassianOauthConfig,
  setGoogleWorkspaceConfig,
  setGoogleWorkspaceIndividualCredentials,
  setRefreshTokenCredentials,
} from '../../../../src/modules/tokens_manager/services/connectors-config.service'

describe('tokens_manager/services/connectors-config.service', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('getGoogleWorkspaceBusinessCredentials', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = {}
      try {
        await getGoogleWorkspaceBusinessCredentials(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getGoogleWorkspaceConfig', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = {}
      try {
        await getGoogleWorkspaceConfig(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getAtlassianOauthConfig', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = {}
      try {
        await getAtlassianOauthConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getOneDriveConfig', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = {}
      try {
        await getOneDriveConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getSharePointConfig', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = {}
      try {
        await getSharePointConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getRefreshTokenCredentials', () => {
    it('should throw NotFoundError when tokenPayload is missing', async () => {
      const req: any = {}
      try {
        await getRefreshTokenCredentials(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getRefreshTokenConfig', () => {
    it('should throw NotFoundError when tokenPayload is missing', async () => {
      const req: any = {}
      try {
        await getRefreshTokenConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getGoogleWorkspaceIndividualCredentials', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = {}
      try {
        await getGoogleWorkspaceIndividualCredentials(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setGoogleWorkspaceBusinessCredentials', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = { body: {} }
      try {
        await setGoogleWorkspaceBusinessCredentials(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('deleteGoogleWorkspaceCredentials', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = {}
      try {
        await deleteGoogleWorkspaceCredentials(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setOneDriveConfig', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = { body: {} }
      try {
        await setOneDriveConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setSharePointConfig', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = { body: {} }
      try {
        await setSharePointConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setGoogleWorkspaceConfig', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = { body: {} }
      try {
        await setGoogleWorkspaceConfig(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setAtlassianOauthConfig', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = { body: {} }
      try {
        await setAtlassianOauthConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setRefreshTokenCredentials', () => {
    it('should throw NotFoundError when tokenPayload is missing', async () => {
      const req: any = { headers: {} }
      try {
        await setRefreshTokenCredentials(req, 'http://localhost', 'at', 'rt', 1234567890)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw NotFoundError when tokenPayload is null', async () => {
      const req: any = { headers: {}, tokenPayload: null }
      try {
        await setRefreshTokenCredentials(req, 'http://localhost', 'at', 'rt', 1234567890)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setGoogleWorkspaceIndividualCredentials', () => {
    it('should throw NotFoundError when user is missing', async () => {
      const req: any = { body: {} }
      try {
        await setGoogleWorkspaceIndividualCredentials(
          req,
          'http://localhost',
          'secret',
          'access-token',
          'refresh-token',
          Date.now() + 3600000,
        )
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw NotFoundError when user is null', async () => {
      const req: any = { body: {}, user: null }
      try {
        await setGoogleWorkspaceIndividualCredentials(
          req,
          'http://localhost',
          'secret',
          'access-token',
          'refresh-token',
          Date.now() + 3600000,
        )
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw NotFoundError with optional params when user is missing', async () => {
      const req: any = { body: {} }
      try {
        await setGoogleWorkspaceIndividualCredentials(
          req,
          'http://localhost',
          'secret',
          'access-token',
          'refresh-token',
          Date.now() + 3600000,
          Date.now() + 86400000,
          true,
          'projects/my-project/topics/my-topic',
        )
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  // Additional edge cases for already-covered functions
  describe('getGoogleWorkspaceBusinessCredentials (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { user: null }
      try {
        await getGoogleWorkspaceBusinessCredentials(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw NotFoundError when user is undefined', async () => {
      const req: any = { user: undefined }
      try {
        await getGoogleWorkspaceBusinessCredentials(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getGoogleWorkspaceIndividualCredentials (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { user: null }
      try {
        await getGoogleWorkspaceIndividualCredentials(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('deleteGoogleWorkspaceCredentials (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { user: null }
      try {
        await deleteGoogleWorkspaceCredentials(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getRefreshTokenCredentials (additional)', () => {
    it('should throw NotFoundError when tokenPayload is null', async () => {
      const req: any = { tokenPayload: null }
      try {
        await getRefreshTokenCredentials(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getRefreshTokenConfig (additional)', () => {
    it('should throw NotFoundError when tokenPayload is null', async () => {
      const req: any = { tokenPayload: null }
      try {
        await getRefreshTokenConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setGoogleWorkspaceConfig (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { body: {}, user: null }
      try {
        await setGoogleWorkspaceConfig(req, 'http://localhost', 'secret')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setAtlassianOauthConfig (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { body: {}, user: null }
      try {
        await setAtlassianOauthConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getAtlassianOauthConfig (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { user: null }
      try {
        await getAtlassianOauthConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getOneDriveConfig (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { user: null }
      try {
        await getOneDriveConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getSharePointConfig (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { user: null }
      try {
        await getSharePointConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setOneDriveConfig (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { body: {}, user: null }
      try {
        await setOneDriveConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('setSharePointConfig (additional)', () => {
    it('should throw NotFoundError when user is null', async () => {
      const req: any = { body: {}, user: null }
      try {
        await setSharePointConfig(req, 'http://localhost')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  // =========================================================================
  // Happy path tests with mocked ConfigurationManagerServiceCommand
  // =========================================================================
  describe('getGoogleWorkspaceBusinessCredentials - happy path', () => {
    it('should call execute and return response when user exists', async () => {
      const mockResponse = { statusCode: 200, data: { credentials: 'test' } }
      const executeStub = sinon.stub().resolves(mockResponse)

      // Stub the ConfigurationManagerServiceCommand constructor
      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
      }

      const result = await getGoogleWorkspaceBusinessCredentials(req, 'http://localhost', 'secret')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('getGoogleWorkspaceIndividualCredentials - happy path', () => {
    it('should call execute and return response when user exists', async () => {
      const mockResponse = { statusCode: 200, data: { token: 'abc' } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
      }

      const result = await getGoogleWorkspaceIndividualCredentials(req, 'http://localhost', 'secret')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('setGoogleWorkspaceBusinessCredentials - happy path', () => {
    it('should call execute with body and return response', async () => {
      const mockResponse = { statusCode: 200, data: { saved: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
        body: { clientId: 'cid', clientSecret: 'cs' },
      }

      const result = await setGoogleWorkspaceBusinessCredentials(req, 'http://localhost', 'secret')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('deleteGoogleWorkspaceCredentials - happy path', () => {
    it('should call execute and return response', async () => {
      const mockResponse = { statusCode: 200, data: { deleted: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
      }

      const result = await deleteGoogleWorkspaceCredentials(req, 'http://localhost', 'secret')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('getGoogleWorkspaceConfig - happy path', () => {
    it('should call execute and return response', async () => {
      const mockResponse = { statusCode: 200, data: { clientId: 'cid' } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
      }

      const result = await getGoogleWorkspaceConfig(req, 'http://localhost', 'secret')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('getAtlassianOauthConfig - happy path', () => {
    it('should call execute with req headers and return response', async () => {
      const mockResponse = { statusCode: 200, data: { config: 'atlassian' } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
      }

      const result = await getAtlassianOauthConfig(req, 'http://localhost')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('getOneDriveConfig - happy path', () => {
    it('should call execute with req headers and return response', async () => {
      const mockResponse = { statusCode: 200, data: { config: 'onedrive' } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
      }

      const result = await getOneDriveConfig(req, 'http://localhost')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('getSharePointConfig - happy path', () => {
    it('should call execute with req headers and return response', async () => {
      const mockResponse = { statusCode: 200, data: { config: 'sharepoint' } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
      }

      const result = await getSharePointConfig(req, 'http://localhost')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('setGoogleWorkspaceIndividualCredentials - happy path', () => {
    it('should call execute with token data and return response', async () => {
      const mockResponse = { statusCode: 200, data: { saved: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
      }

      const result = await setGoogleWorkspaceIndividualCredentials(
        req,
        'http://localhost',
        'secret',
        'access-token',
        'refresh-token',
        Date.now() + 3600000,
        Date.now() + 86400000,
        true,
        'projects/my-project/topics/my-topic',
      )
      expect(result).to.deep.equal(mockResponse)
    })

    it('should handle optional params being undefined', async () => {
      const mockResponse = { statusCode: 200, data: { saved: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
      }

      const result = await setGoogleWorkspaceIndividualCredentials(
        req,
        'http://localhost',
        'secret',
        'access-token',
        'refresh-token',
        Date.now() + 3600000,
      )
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('getRefreshTokenCredentials - happy path', () => {
    it('should call execute and return response when tokenPayload exists', async () => {
      const mockResponse = { statusCode: 200, data: { refresh_token: 'rt' } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        tokenPayload: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
      }

      const result = await getRefreshTokenCredentials(req, 'http://localhost')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('getRefreshTokenConfig - happy path', () => {
    it('should call execute and return response when tokenPayload exists', async () => {
      const mockResponse = { statusCode: 200, data: { clientId: 'cid' } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        tokenPayload: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
      }

      const result = await getRefreshTokenConfig(req, 'http://localhost')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('setRefreshTokenCredentials - happy path', () => {
    it('should call execute with token data and return response', async () => {
      const mockResponse = { statusCode: 200, data: { saved: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        tokenPayload: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
      }

      const result = await setRefreshTokenCredentials(
        req,
        'http://localhost',
        'new-access-token',
        'refresh-token',
        Date.now() + 3600000,
        Date.now() + 86400000,
        true,
        'projects/my-project/topics/my-topic',
      )
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('setOneDriveConfig - happy path', () => {
    it('should call execute with body and return response', async () => {
      const mockResponse = { statusCode: 200, data: { saved: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
        body: { tenantId: 'tid', clientId: 'cid' },
      }

      const result = await setOneDriveConfig(req, 'http://localhost')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('setSharePointConfig - happy path', () => {
    it('should call execute with body and return response', async () => {
      const mockResponse = { statusCode: 200, data: { saved: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
        body: { tenantId: 'tid' },
      }

      const result = await setSharePointConfig(req, 'http://localhost')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('setGoogleWorkspaceConfig - happy path', () => {
    it('should call execute with body and return response', async () => {
      const mockResponse = { statusCode: 200, data: { saved: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
        body: { clientId: 'cid', clientSecret: 'cs' },
      }

      const result = await setGoogleWorkspaceConfig(req, 'http://localhost', 'secret')
      expect(result).to.deep.equal(mockResponse)
    })
  })

  describe('setAtlassianOauthConfig - happy path', () => {
    it('should call execute with body and return response', async () => {
      const mockResponse = { statusCode: 200, data: { saved: true } }
      const executeStub = sinon.stub().resolves(mockResponse)

      const cmModule = require('../../../../src/libs/commands/configuration_manager/cm.service.command')
      sinon.stub(cmModule, 'ConfigurationManagerServiceCommand').returns({
        execute: executeStub,
      })

      const req: any = {
        user: { orgId: 'org1', userId: 'user1' },
        headers: { authorization: 'Bearer token' },
        body: { clientId: 'cid', clientSecret: 'cs' },
      }

      const result = await setAtlassianOauthConfig(req, 'http://localhost')
      expect(result).to.deep.equal(mockResponse)
    })
  })
})
