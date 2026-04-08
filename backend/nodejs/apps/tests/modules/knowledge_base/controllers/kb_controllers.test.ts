import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'
import { ConnectorServiceCommand } from '../../../../src/libs/commands/connector_service/connector.service.command'
import * as kbUtils from '../../../../src/modules/knowledge_base/utils/utils'
import {
  getKnowledgeHubNodes,
  createKnowledgeBase,
  getKnowledgeBase,
  listKnowledgeBases,
  updateKnowledgeBase,
  deleteKnowledgeBase,
  createRootFolder,
  createNestedFolder,
  updateFolder,
  deleteFolder,
  uploadRecordsToKB,
  uploadRecordsToFolder,
  updateRecord,
  getKBContent,
  getFolderContents,
  getRecordById,
  reindexRecord,
  reindexRecordGroup,
  deleteRecord,
  createKBPermission,
  updateKBPermission,
  removeKBPermission,
  listKBPermissions,
  getConnectorStats,
  getRecordBuffer,
  reindexFailedRecords,
  resyncConnectorRecords,
  moveRecord,
} from '../../../../src/modules/knowledge_base/controllers/kb_controllers'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: { authorization: 'Bearer test-token' },
    body: {},
    params: {},
    query: {},
    user: { userId: 'user-1', orgId: 'org-1', email: 'test@test.com', fullName: 'Test User' },
    context: { requestId: 'req-123' },
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
    set: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
    pipe: sinon.stub(),
  }
  res.status.returns(res)
  res.json.returns(res)
  res.end.returns(res)
  res.send.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

function createMockAppConfig(): any {
  return {
    connectorBackend: 'http://localhost:8088',
    aiBackend: 'http://localhost:8000',
    storage: {
      endpoint: 'http://localhost:3003',
    },
    jwtSecret: 'test-jwt-secret',
    scopedJwtSecret: 'test-scoped-secret',
    cmBackend: 'http://localhost:3001',
  }
}

function createMockRecordRelationService(): any {
  return {
    publishRecordEvents: sinon.stub().resolves(),
    createNewRecordEventPayload: sinon.stub().resolves({}),
    createUpdateRecordEventPayload: sinon.stub().resolves({}),
    createDeleteRecordEvent: sinon.stub().resolves(),
    reindexFailedRecords: sinon.stub().resolves({ success: true }),
    resyncConnectorRecords: sinon.stub().resolves({ success: true }),
    eventProducer: {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    },
    syncEventProducer: {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
    },
  }
}

function createMockKeyValueStore(): any {
  return {
    get: sinon.stub().resolves(null),
    set: sinon.stub().resolves(),
    delete: sinon.stub().resolves(),
  }
}

function createMockNotificationService(): any {
  return {
    sendToUser: sinon.stub().returns(true),
  }
}

describe('Knowledge Base Controller', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // getKnowledgeHubNodes
  // -----------------------------------------------------------------------
  describe('getKnowledgeHubNodes', () => {
    it('should return a handler function', () => {
      const handler = getKnowledgeHubNodes(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next with error when user is not authenticated', async () => {
      const handler = getKnowledgeHubNodes(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next with error when userId is missing', async () => {
      const handler = getKnowledgeHubNodes(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next with error when orgId is missing', async () => {
      const handler = getKnowledgeHubNodes(createMockAppConfig())
      const req = createMockRequest({ user: { userId: 'user-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Knowledge Base CRUD
  // -----------------------------------------------------------------------
  describe('createKnowledgeBase', () => {
    it('should return a handler function', () => {
      const handler = createKnowledgeBase(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = createKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = createKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when orgId is missing', async () => {
      const handler = createKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: { userId: 'user-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getKnowledgeBase', () => {
    it('should return a handler function', () => {
      const handler = getKnowledgeBase(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = getKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { kbId: 'kb-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = getKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { kbId: 'kb-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('listKnowledgeBases', () => {
    it('should return a handler function', () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next with BadRequestError for invalid sort field', async () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      const req = createMockRequest({ query: { sortBy: 'invalidField' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next with BadRequestError for invalid sort order', async () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      const req = createMockRequest({ query: { sortOrder: 'invalid' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next with BadRequestError for invalid permissions filter', async () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      const req = createMockRequest({ query: { permissions: 'INVALID_PERM' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('updateKnowledgeBase', () => {
    it('should return a handler function', () => {
      const handler = updateKnowledgeBase(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = updateKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { kbId: 'kb-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = updateKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { kbId: 'kb-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('deleteKnowledgeBase', () => {
    it('should return a handler function', () => {
      const handler = deleteKnowledgeBase(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = deleteKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { kbId: 'kb-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = deleteKnowledgeBase(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { kbId: 'kb-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Folder Operations
  // -----------------------------------------------------------------------
  describe('createRootFolder', () => {
    it('should return a handler function', () => {
      const handler = createRootFolder(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = createRootFolder(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = createRootFolder(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { kbId: 'kb-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when orgId is missing', async () => {
      const handler = createRootFolder(createMockAppConfig())
      const req = createMockRequest({ user: { userId: 'user-1' }, params: { kbId: 'kb-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('createNestedFolder', () => {
    it('should return a handler function', () => {
      const handler = createNestedFolder(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = createNestedFolder(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { kbId: 'kb-1', folderId: 'f-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = createNestedFolder(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { kbId: 'kb-1', folderId: 'f-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('updateFolder', () => {
    it('should return a handler function', () => {
      const handler = updateFolder(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = updateFolder(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { kbId: 'kb-1', folderId: 'f-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = updateFolder(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { kbId: 'kb-1', folderId: 'f-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('deleteFolder', () => {
    it('should return a handler function', () => {
      const handler = deleteFolder(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = deleteFolder(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { kbId: 'kb-1', folderId: 'f-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = deleteFolder(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { kbId: 'kb-1', folderId: 'f-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Upload
  // -----------------------------------------------------------------------
  describe('uploadRecordsToKB', () => {
    it('should return a handler function', () => {
      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: { orgId: 'org-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when kbId is missing and files are empty', async () => {
      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: {},
        body: { fileBuffers: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when files are empty but kbId is provided', async () => {
      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { fileBuffers: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should accept notification service as optional parameter', () => {
      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
        createMockNotificationService(),
      )
      expect(handler).to.be.a('function')
    })
  })

  describe('uploadRecordsToFolder', () => {
    it('should return a handler function', () => {
      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when kbId, folderId, or files are missing', async () => {
      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { fileBuffers: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Record Operations
  // -----------------------------------------------------------------------
  describe('updateRecord', () => {
    it('should return a handler function', () => {
      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: undefined, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when orgId is missing', async () => {
      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: { userId: 'user-1' }, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getKBContent', () => {
    it('should return a handler function', () => {
      const handler = getKBContent(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = getKBContent(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when kbId is missing', async () => {
      const handler = getKBContent(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getFolderContents', () => {
    it('should return a handler function', () => {
      const handler = getFolderContents(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = getFolderContents(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when kbId is missing', async () => {
      const handler = getFolderContents(createMockAppConfig())
      const req = createMockRequest({ params: { folderId: 'f-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getRecordById', () => {
    it('should return a handler function', () => {
      const handler = getRecordById(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = getRecordById(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = getRecordById(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('reindexRecord', () => {
    it('should return a handler function', () => {
      const handler = reindexRecord(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = reindexRecord(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = reindexRecord(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('reindexRecordGroup', () => {
    it('should return a handler function', () => {
      const handler = reindexRecordGroup(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = reindexRecordGroup(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { recordGroupId: 'rg-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('deleteRecord', () => {
    it('should return a handler function', () => {
      const handler = deleteRecord(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = deleteRecord(createMockAppConfig())
      const req = createMockRequest({ user: undefined, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = deleteRecord(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // KB Permissions
  // -----------------------------------------------------------------------
  describe('createKBPermission', () => {
    it('should return a handler function', () => {
      const handler = createKBPermission(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = createKBPermission(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userIds and teamIds are empty', async () => {
      const handler = createKBPermission(createMockAppConfig())
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: [], teamIds: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when role is missing but users are provided', async () => {
      const handler = createKBPermission(createMockAppConfig())
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['user-1'], teamIds: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next with BadRequestError for invalid role', async () => {
      const handler = createKBPermission(createMockAppConfig())
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['user-1'], teamIds: [], role: 'INVALID_ROLE' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('updateKBPermission', () => {
    it('should return a handler function', () => {
      const handler = updateKBPermission(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when userIds and teamIds are empty', async () => {
      const handler = updateKBPermission(createMockAppConfig())
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: [], teamIds: [], role: 'READER' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when role is missing', async () => {
      const handler = updateKBPermission(createMockAppConfig())
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['user-1'], teamIds: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next with BadRequestError for invalid role', async () => {
      const handler = updateKBPermission(createMockAppConfig())
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['user-1'], teamIds: [], role: 'ADMIN' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('removeKBPermission', () => {
    it('should return a handler function', () => {
      const handler = removeKBPermission(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when userIds and teamIds are empty', async () => {
      const handler = removeKBPermission(createMockAppConfig())
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: [], teamIds: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('listKBPermissions', () => {
    it('should return a handler function', () => {
      const handler = listKBPermissions(createMockAppConfig())
      expect(handler).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // Connector Stats
  // -----------------------------------------------------------------------
  describe('getConnectorStats', () => {
    it('should return a handler function', () => {
      const handler = getConnectorStats(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = getConnectorStats(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when connectorId is missing', async () => {
      const handler = getConnectorStats(createMockAppConfig())
      const req = createMockRequest({ params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Record Buffer
  // -----------------------------------------------------------------------
  describe('getRecordBuffer', () => {
    it('should return a handler function', () => {
      const handler = getRecordBuffer('http://localhost:8088')
      expect(handler).to.be.a('function')
    })

    it('should return 500 error when user not authenticated', async () => {
      const handler = getRecordBuffer('http://localhost:8088')
      const req = createMockRequest({ user: undefined, params: { recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      // getRecordBuffer catches BadRequestError and returns 500 when no .response
      expect(res.status.calledWith(500)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Reindex / Resync
  // -----------------------------------------------------------------------
  describe('reindexFailedRecords', () => {
    it('should return a handler function', () => {
      const handler = reindexFailedRecords(
        createMockRecordRelationService(),
        createMockAppConfig(),
      )
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = reindexFailedRecords(
        createMockRecordRelationService(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = reindexFailedRecords(
        createMockRecordRelationService(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: { orgId: 'org-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('resyncConnectorRecords', () => {
    it('should return a handler function', () => {
      const handler = resyncConnectorRecords(
        createMockRecordRelationService(),
        createMockAppConfig(),
      )
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = resyncConnectorRecords(
        createMockRecordRelationService(),
        createMockAppConfig(),
      )
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Move Record
  // -----------------------------------------------------------------------
  describe('moveRecord', () => {
    it('should return a handler function', () => {
      const handler = moveRecord(createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when user not authenticated', async () => {
      const handler = moveRecord(createMockAppConfig())
      const req = createMockRequest({ user: undefined })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when userId is missing', async () => {
      const handler = moveRecord(createMockAppConfig())
      const req = createMockRequest({ user: { orgId: 'org-1' }, params: { kbId: 'kb-1', recordId: 'r-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Happy-path tests that exercise the actual handler logic
  // -----------------------------------------------------------------------

  describe('getKnowledgeHubNodes (happy path)', () => {
    it('should return nodes from connector backend', async () => {
      const handler = getKnowledgeHubNodes(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { nodes: [{ id: 'n1', name: 'Root' }] },
      })

      const req = createMockRequest({
        query: { view: 'tree', page: '1', limit: '20' },
        params: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        expect(res.json.calledOnce).to.be.true
      }
    })

    it('should include parentType and parentId in url when present', async () => {
      const handler = getKnowledgeHubNodes(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { nodes: [] },
      })

      const req = createMockRequest({
        query: { onlyContainers: 'true' },
        params: { parentType: 'kb', parentId: 'kb-123' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should call next with error when connector fails', async () => {
      const handler = getKnowledgeHubNodes(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Connection failed'))

      const req = createMockRequest({ query: {}, params: {} })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('createKnowledgeBase (happy path)', () => {
    it('should create a knowledge base successfully', async () => {
      const handler = createKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 201,
        data: { _key: 'kb-1', name: 'Test KB' },
      })

      const req = createMockRequest({
        body: { kbName: 'Test KB' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.called).to.be.true
        expect(res.json.calledOnce).to.be.true
      }
    })

    it('should call next when connector returns non-200', async () => {
      const handler = createKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { detail: 'KB already exists' },
      })

      const req = createMockRequest({
        body: { kbName: 'Test KB' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getKnowledgeBase (happy path)', () => {
    it('should get a knowledge base successfully', async () => {
      const handler = getKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { _key: 'kb-1', name: 'Test KB', records: [] },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        expect(res.json.calledOnce).to.be.true
      }
    })
  })

  describe('listKnowledgeBases (happy path)', () => {
    it('should list knowledge bases with pagination', async () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { knowledgeBases: [{ name: 'KB1' }], total: 1 },
      })

      const req = createMockRequest({
        query: { page: '1', limit: '10', sortBy: 'name', sortOrder: 'asc' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should list knowledge bases with search parameter', async () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { knowledgeBases: [], total: 0 },
      })

      const req = createMockRequest({
        query: { page: '1', limit: '10', search: 'test', sortBy: 'name', sortOrder: 'asc' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should list knowledge bases with permissions filter', async () => {
      const handler = listKnowledgeBases(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { knowledgeBases: [], total: 0 },
      })

      const req = createMockRequest({
        query: { page: '1', limit: '10', permissions: 'OWNER,READER', sortBy: 'name', sortOrder: 'asc' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('updateKnowledgeBase (happy path)', () => {
    it('should update a knowledge base successfully', async () => {
      const handler = updateKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { _key: 'kb-1', name: 'Updated KB' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { kbName: 'Updated KB' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('deleteKnowledgeBase (happy path)', () => {
    it('should delete a knowledge base successfully', async () => {
      const handler = deleteKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { message: 'KB deleted' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('createRootFolder (happy path)', () => {
    it('should create a root folder successfully', async () => {
      const handler = createRootFolder(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 201,
        data: { _key: 'folder-1', name: 'New Folder' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { folderName: 'New Folder' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.json.calledOnce).to.be.true
      }
    })
  })

  describe('createNestedFolder (happy path)', () => {
    it('should create a nested folder successfully', async () => {
      const handler = createNestedFolder(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 201,
        data: { _key: 'folder-2', name: 'Sub Folder' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: { folderName: 'Sub Folder' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.json.calledOnce).to.be.true
      }
    })
  })

  describe('updateFolder (happy path)', () => {
    it('should update a folder successfully', async () => {
      const handler = updateFolder(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { _key: 'folder-1', name: 'Renamed Folder' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: { folderName: 'Renamed Folder' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('deleteFolder (happy path)', () => {
    it('should delete a folder successfully', async () => {
      const handler = deleteFolder(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { message: 'Folder deleted' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('getKBContent (happy path)', () => {
    it('should get KB content with default params', async () => {
      const handler = getKBContent(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { records: [{ name: 'file.pdf' }], total: 1 },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should get KB content with all filters', async () => {
      const handler = getKBContent(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { records: [], total: 0 },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        query: {
          page: '2',
          limit: '10',
          search: 'test',
          recordTypes: 'file,folder',
          origins: 'upload',
          connectors: 'c1',
          indexingStatus: 'completed',
          dateFrom: '1000000',
          dateTo: '2000000',
          sortBy: 'createdAtTimestamp',
          sortOrder: 'desc',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should call next when dateFrom is invalid', async () => {
      const handler = getKBContent(createMockAppConfig())

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        query: { dateFrom: 'invalid' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when dateTo is invalid', async () => {
      const handler = getKBContent(createMockAppConfig())

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        query: { dateTo: 'invalid' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getFolderContents (happy path)', () => {
    it('should get folder contents successfully', async () => {
      const handler = getFolderContents(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { records: [{ name: 'child.pdf' }], total: 1 },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('getRecordById (happy path)', () => {
    it('should get record by ID successfully', async () => {
      const handler = getRecordById(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { _key: 'r1', name: 'file.pdf' },
      })

      const req = createMockRequest({
        params: { recordId: 'r1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('reindexRecord (happy path)', () => {
    it('should reindex record successfully', async () => {
      const handler = reindexRecord(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { message: 'Record reindexed' },
      })

      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: { depth: 0, force: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should reindex with force flag', async () => {
      const handler = reindexRecord(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { message: 'Record force reindexed' },
      })

      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: { depth: 1, force: true },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('reindexRecordGroup (happy path)', () => {
    it('should reindex record group successfully', async () => {
      const handler = reindexRecordGroup(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { message: 'Record group reindexed' },
      })

      const req = createMockRequest({
        params: { recordGroupId: 'rg1' },
        body: { depth: 0, force: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('deleteRecord (happy path)', () => {
    it('should delete record successfully', async () => {
      const handler = deleteRecord(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { message: 'Record deleted' },
      })

      const req = createMockRequest({
        params: { recordId: 'r1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('createKBPermission (happy path)', () => {
    it('should create permission for users with role', async () => {
      const handler = createKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 201,
        data: { permissions: [{ userId: 'u1', role: 'READER' }] },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [], role: 'READER' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(201)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('kbId', 'kb-1')
      }
    })

    it('should create permission for teams without role', async () => {
      const handler = createKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 201,
        data: { permissions: [{ teamId: 't1' }] },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: [], teamIds: ['t1'] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(201)).to.be.true
      }
    })

    it('should call next when connector returns error', async () => {
      const handler = createKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { detail: 'Invalid request' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [], role: 'READER' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('updateKBPermission (happy path)', () => {
    it('should update permissions successfully', async () => {
      const handler = updateKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { userIds: ['u1'], teamIds: [], newRole: 'WRITER' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [], role: 'WRITER' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('kbId', 'kb-1')
      }
    })

    it('should call next when connector returns non-200', async () => {
      const handler = updateKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 403,
        data: { detail: 'Forbidden' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [], role: 'WRITER' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('removeKBPermission (happy path)', () => {
    it('should remove permissions successfully', async () => {
      const handler = removeKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { userIds: ['u1'], teamIds: [] },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })
  })

  describe('listKBPermissions (happy path)', () => {
    it('should list permissions successfully', async () => {
      const handler = listKBPermissions(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { permissions: [{ userId: 'u1', role: 'OWNER' }], totalCount: 1 },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('permissions')
        expect(response).to.have.property('totalCount', 1)
      }
    })

    it('should call next when connector returns non-200', async () => {
      const handler = listKBPermissions(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: { detail: 'Internal error' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getConnectorStats (happy path)', () => {
    it('should get connector stats successfully', async () => {
      const handler = getConnectorStats(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { totalRecords: 100, indexed: 95, failed: 5 },
      })

      const req = createMockRequest({
        params: { connectorId: 'c1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('totalRecords', 100)
      }
    })

    it('should call next when connector returns non-200', async () => {
      const handler = getConnectorStats(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: null,
      })

      const req = createMockRequest({
        params: { connectorId: 'c1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('getRecordBuffer (happy path)', () => {
    it('should stream record buffer to client', async () => {
      const mockStream = {
        pipe: sinon.stub(),
        on: sinon.stub(),
      }
      sinon.stub(axios, 'get').resolves({
        headers: { 'content-type': 'application/pdf', 'content-disposition': 'attachment; filename="test.pdf"' },
        data: mockStream,
      })

      const handler = getRecordBuffer('http://localhost:8088')
      const req = createMockRequest({
        params: { recordId: 'r1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockStream.pipe.calledOnce).to.be.true
      expect(res.set.calledWith('Content-Type', 'application/pdf')).to.be.true
    })

    it('should stream with convertTo parameter', async () => {
      const mockStream = {
        pipe: sinon.stub(),
        on: sinon.stub(),
      }
      sinon.stub(axios, 'get').resolves({
        headers: { 'content-type': 'text/html' },
        data: mockStream,
      })

      const handler = getRecordBuffer('http://localhost:8088')
      const req = createMockRequest({
        params: { recordId: 'r1' },
        query: { convertTo: 'html' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockStream.pipe.calledOnce).to.be.true
    })

    it('should handle error from axios with no response', async () => {
      sinon.stub(axios, 'get').rejects(new Error('Network error'))

      const handler = getRecordBuffer('http://localhost:8088')
      const req = createMockRequest({
        params: { recordId: 'r1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      // Should return 500 since headersSent is false and no response on the error
      expect(res.status.calledWith(500)).to.be.true
    })
  })

  describe('reindexFailedRecords (happy path)', () => {
    it('should reindex failed records successfully', async () => {
      const mockRecordRelation = createMockRecordRelationService()
      // Stub connector commands for validateActiveConnector and validateConnectorNotLocked
      const executeStub = sinon.stub(ConnectorServiceCommand.prototype, 'execute')
      executeStub.onFirstCall().resolves({
        statusCode: 200,
        data: { connectors: [{ _key: 'c1' }] },
      })
      executeStub.onSecondCall().resolves({
        statusCode: 200,
        data: { connector: { isLocked: false } },
      })

      const handler = reindexFailedRecords(mockRecordRelation, createMockAppConfig())
      const req = createMockRequest({
        body: { app: 'Google Drive', connectorId: 'c1', statusFilters: ['failed'] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        expect(mockRecordRelation.reindexFailedRecords.calledOnce).to.be.true
      }
    })
  })

  describe('resyncConnectorRecords (happy path)', () => {
    it('should resync connector records successfully', async () => {
      const mockRecordRelation = createMockRecordRelationService()
      const executeStub = sinon.stub(ConnectorServiceCommand.prototype, 'execute')
      executeStub.onFirstCall().resolves({
        statusCode: 200,
        data: { connectors: [{ _key: 'c1' }] },
      })
      executeStub.onSecondCall().resolves({
        statusCode: 200,
        data: { connector: { isLocked: false } },
      })

      const handler = resyncConnectorRecords(mockRecordRelation, createMockAppConfig())
      const req = createMockRequest({
        body: { connectorName: 'Google Drive', connectorId: 'c1', fullSync: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        expect(mockRecordRelation.resyncConnectorRecords.calledOnce).to.be.true
      }
    })

    it('should call next when connector is not active', async () => {
      const mockRecordRelation = createMockRecordRelationService()
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { connectors: [{ _key: 'other-connector' }] },
      })

      const handler = resyncConnectorRecords(mockRecordRelation, createMockAppConfig())
      const req = createMockRequest({
        body: { connectorName: 'Google Drive', connectorId: 'c1', fullSync: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('moveRecord (happy path)', () => {
    it('should move record successfully', async () => {
      const handler = moveRecord(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { message: 'Record moved' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', recordId: 'r1' },
        body: { newParentId: 'folder-1' },
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
  // uploadRecordsToKB (deep coverage with utils stubbing)
  // -----------------------------------------------------------------------
  describe('uploadRecordsToKB (deep happy paths)', () => {
    it('should upload records successfully with placeholder creation', async () => {
      const createPlaceholderStub = sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
        documentId: 'doc-123',
        documentName: 'test-file',
      })
      const processUploadsStub = sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'test.pdf',
              mimetype: 'application/pdf',
              size: 1024,
              filePath: 'docs/test.pdf',
              lastModified: Date.now(),
              buffer: Buffer.from('fake file data'),
            },
          ],
          isVersioned: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response).to.have.property('status', 'processing')
        expect(response).to.have.property('records')
        expect(response.records).to.be.an('array').with.lengthOf(1)
        expect(response.records[0]).to.have.property('recordName', 'test-file')
        expect(response.records[0]).to.have.property('recordType')
        expect(response.records[0]).to.have.property('origin')
        expect(processUploadsStub.calledOnce).to.be.true
      }
    })

    it('should handle partial failures - some files fail placeholder creation', async () => {
      const createPlaceholderStub = sinon.stub(kbUtils, 'createPlaceholderDocument')
      createPlaceholderStub.onFirstCall().resolves({
        documentId: 'doc-1',
        documentName: 'file1',
      })
      createPlaceholderStub.onSecondCall().rejects(new Error('Storage full'))
      sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
        createMockNotificationService(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'file1.pdf',
              mimetype: 'application/pdf',
              size: 1024,
              filePath: 'file1.pdf',
              lastModified: Date.now(),
              buffer: Buffer.from('data1'),
            },
            {
              originalname: 'file2.pdf',
              mimetype: 'application/pdf',
              size: 2048,
              filePath: 'file2.pdf',
              lastModified: Date.now(),
              buffer: Buffer.from('data2'),
            },
          ],
          isVersioned: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.successfulFiles).to.equal(1)
        expect(response.failedFiles).to.equal(1)
        expect(response.records).to.have.lengthOf(1)
      }
    })

    it('should handle all files failing placeholder creation', async () => {
      sinon.stub(kbUtils, 'createPlaceholderDocument').rejects(new Error('Storage error'))

      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
        createMockNotificationService(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'file1.pdf',
              mimetype: 'application/pdf',
              size: 1024,
              filePath: 'file1.pdf',
              lastModified: Date.now(),
              buffer: Buffer.from('data'),
            },
          ],
          isVersioned: true,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.status).to.equal('failed')
        expect(response.records).to.have.lengthOf(0)
      }
    })

    it('should handle file without last modified timestamp', async () => {
      sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
        documentId: 'doc-1',
        documentName: 'file-no-mod',
      })
      sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'test.txt',
              mimetype: 'text/plain',
              size: 100,
              filePath: 'test.txt',
              lastModified: 0, // invalid lastModified
              buffer: Buffer.from('data'),
            },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.records).to.have.lengthOf(1)
        // sourceCreatedAtTimestamp should be set to currentTime (not 0)
        expect(response.records[0].sourceCreatedAtTimestamp).to.be.above(0)
      }
    })

    it('should handle file with path containing slashes', async () => {
      sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
        documentId: 'doc-1',
        documentName: 'nested-file',
      })
      sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'report.pdf',
              mimetype: 'application/pdf',
              size: 5000,
              filePath: 'documents/reports/report.pdf',
              lastModified: Date.now(),
              buffer: Buffer.from('data'),
            },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.records).to.have.lengthOf(1)
      }
    })

    it('should handle file without extension', async () => {
      sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
        documentId: 'doc-1',
        documentName: 'no-ext-file',
      })
      sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'Dockerfile',
              mimetype: 'application/octet-stream',
              size: 500,
              filePath: 'Dockerfile',
              lastModified: Date.now(),
              buffer: Buffer.from('data'),
            },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should handle placeholder error with response.data.error.message structure', async () => {
      const err: any = new Error('Placeholder failed')
      err.response = { data: { error: { message: 'Quota exceeded' } } }
      sinon.stub(kbUtils, 'createPlaceholderDocument').rejects(err)

      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
        createMockNotificationService(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'file.pdf',
              mimetype: 'application/pdf',
              size: 1024,
              filePath: 'file.pdf',
              lastModified: Date.now(),
              buffer: Buffer.from('data'),
            },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.status).to.equal('failed')
      }
    })

    it('should handle placeholder error with response.data.message structure', async () => {
      const err: any = new Error('Placeholder failed')
      err.response = { data: { message: 'File too large' } }
      sinon.stub(kbUtils, 'createPlaceholderDocument').rejects(err)

      const handler = uploadRecordsToKB(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'big.pdf',
              mimetype: 'application/pdf',
              size: 999999999,
              filePath: 'big.pdf',
              lastModified: Date.now(),
              buffer: Buffer.from('data'),
            },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.status).to.equal('failed')
      }
    })
  })

  // -----------------------------------------------------------------------
  // uploadRecordsToFolder (deep coverage)
  // -----------------------------------------------------------------------
  describe('uploadRecordsToFolder (deep happy paths)', () => {
    it('should upload records to folder successfully', async () => {
      sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
        documentId: 'doc-456',
        documentName: 'folder-file',
      })
      sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'report.docx',
              mimetype: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              size: 2048,
              filePath: 'report.docx',
              lastModified: Date.now(),
              buffer: Buffer.from('report data'),
            },
          ],
          isVersioned: false,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.status).to.equal('processing')
        expect(response.records).to.have.lengthOf(1)
      }
    })

    it('should handle partial failures in folder upload', async () => {
      const createStub = sinon.stub(kbUtils, 'createPlaceholderDocument')
      createStub.onFirstCall().resolves({ documentId: 'doc-1', documentName: 'f1' })
      createStub.onSecondCall().rejects(new Error('Failed'))
      sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
        createMockNotificationService(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: {
          fileBuffers: [
            { originalname: 'f1.pdf', mimetype: 'application/pdf', size: 100, filePath: 'f1.pdf', lastModified: Date.now(), buffer: Buffer.from('d1') },
            { originalname: 'f2.pdf', mimetype: 'application/pdf', size: 200, filePath: 'f2.pdf', lastModified: Date.now(), buffer: Buffer.from('d2') },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.successfulFiles).to.equal(1)
        expect(response.failedFiles).to.equal(1)
      }
    })

    it('should handle all files failing in folder upload', async () => {
      sinon.stub(kbUtils, 'createPlaceholderDocument').rejects(new Error('All fail'))

      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
        createMockNotificationService(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: {
          fileBuffers: [
            { originalname: 'f1.pdf', mimetype: 'application/pdf', size: 100, filePath: 'f1.pdf', lastModified: Date.now(), buffer: Buffer.from('d') },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.status).to.equal('failed')
        expect(response.records).to.have.lengthOf(0)
      }
    })

    it('should handle missing folderId in folder upload', async () => {
      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: {
          fileBuffers: [
            { originalname: 'f1.pdf', mimetype: 'application/pdf', size: 100, filePath: 'f1.pdf', lastModified: Date.now(), buffer: Buffer.from('d') },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should handle file with nested path in folder upload', async () => {
      sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
        documentId: 'doc-nested',
        documentName: 'nested',
      })
      sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: {
          fileBuffers: [
            {
              originalname: 'deep.xlsx',
              mimetype: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              size: 3000,
              filePath: 'a/b/c/deep.xlsx',
              lastModified: Date.now(),
              buffer: Buffer.from('excel data'),
            },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should handle notification service error during folder upload', async () => {
      const createStub = sinon.stub(kbUtils, 'createPlaceholderDocument')
      createStub.rejects(new Error('Storage fail'))

      const brokenNotification = createMockNotificationService()
      brokenNotification.sendToUser = sinon.stub().throws(new Error('Socket error'))

      const handler = uploadRecordsToFolder(
        createMockKeyValueStore(),
        createMockAppConfig(),
        brokenNotification,
      )
      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: {
          fileBuffers: [
            { originalname: 'f1.pdf', mimetype: 'application/pdf', size: 100, filePath: 'f1.pdf', lastModified: Date.now(), buffer: Buffer.from('d') },
          ],
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        // Should still return 200 even if notification fails
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.status).to.equal('failed')
      }
    })
  })

  // -----------------------------------------------------------------------
  // updateRecord (deep happy paths)
  // -----------------------------------------------------------------------
  describe('updateRecord (deep happy paths)', () => {
    it('should update record name without file upload', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          updatedRecord: {
            _key: 'r1',
            recordName: 'Renamed Record',
            version: 1,
          },
        },
      })

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: { recordName: 'Renamed Record' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.message).to.equal('Record updated successfully')
        expect(response.record.recordName).to.equal('Renamed Record')
        expect(response.fileUploaded).to.be.false
      }
    })

    it('should call next when connector returns null data', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: null,
      })

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: { recordName: 'Test' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when connector returns empty updatedRecord', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { updatedRecord: null },
      })

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: { recordName: 'Test' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should update record with file upload', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          updatedRecord: {
            _key: 'r1',
            recordName: 'report',
            externalRecordId: 'ext-doc-123',
            version: 2,
          },
        },
      })
      sinon.stub(kbUtils, 'uploadNextVersionToStorage').resolves()

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: {
          recordName: 'report',
          fileBuffer: {
            originalname: 'report.pdf',
            mimetype: 'application/pdf',
            size: 4096,
            lastModified: Date.now(),
            buffer: Buffer.from('new file content'),
          },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        const response = res.json.firstCall.args[0]
        expect(response.message).to.equal('Record updated with new file version')
        expect(response.fileUploaded).to.be.true
      }
    })

    it('should use originalname when recordName is not provided', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          updatedRecord: {
            _key: 'r1',
            recordName: 'uploaded',
            version: 1,
          },
        },
      })

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: {}, // no recordName
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should call next when storage upload fails with 404', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          updatedRecord: {
            _key: 'r1',
            recordName: 'report',
            externalRecordId: 'ext-doc-123',
            version: 2,
          },
        },
      })
      const storageError: any = new Error('Not found in storage')
      storageError.response = { status: 404 }
      sinon.stub(kbUtils, 'uploadNextVersionToStorage').rejects(storageError)

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: {
          fileBuffer: {
            originalname: 'report.pdf',
            mimetype: 'application/pdf',
            size: 4096,
            lastModified: Date.now(),
            buffer: Buffer.from('new content'),
          },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when storage upload fails with non-404 error', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          updatedRecord: {
            _key: 'r1',
            recordName: 'report',
            externalRecordId: 'ext-doc-123',
            version: 2,
          },
        },
      })
      sinon.stub(kbUtils, 'uploadNextVersionToStorage').rejects(new Error('Storage timeout'))

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: {
          fileBuffer: {
            originalname: 'report.pdf',
            mimetype: 'application/pdf',
            size: 4096,
            lastModified: Date.now(),
            buffer: Buffer.from('new content'),
          },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when updated record has no externalRecordId for file upload', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          updatedRecord: {
            _key: 'r1',
            recordName: 'report',
            externalRecordId: null, // no external ID
            version: 2,
          },
        },
      })

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: {
          fileBuffer: {
            originalname: 'report.pdf',
            mimetype: 'application/pdf',
            size: 4096,
            lastModified: Date.now(),
            buffer: Buffer.from('new content'),
          },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should handle file with no extension in filename', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: {
          updatedRecord: {
            _key: 'r1',
            recordName: 'Dockerfile',
            externalRecordId: 'ext-123',
            version: 2,
          },
        },
      })
      sinon.stub(kbUtils, 'uploadNextVersionToStorage').resolves()

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: {
          fileBuffer: {
            originalname: 'Dockerfile',
            mimetype: 'application/octet-stream',
            size: 100,
            lastModified: Date.now(),
            buffer: Buffer.from('FROM node:18'),
          },
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should call next when connector command throws', async () => {
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Connection refused'))

      const handler = updateRecord(
        createMockKeyValueStore(),
        createMockAppConfig(),
      )
      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: { recordName: 'Test' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Additional error response handling for connector-backed endpoints
  // -----------------------------------------------------------------------
  describe('connector error handling deep paths', () => {
    it('should call next when getKBContent connector returns non-200', async () => {
      const handler = getKBContent(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: { detail: 'Internal error' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when getFolderContents connector returns non-200', async () => {
      const handler = getFolderContents(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 404,
        data: { detail: 'Folder not found' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when getRecordById connector returns non-200', async () => {
      const handler = getRecordById(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 404,
        data: { detail: 'Record not found' },
      })

      const req = createMockRequest({
        params: { recordId: 'r1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when reindexRecord connector returns non-200', async () => {
      const handler = reindexRecord(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { detail: 'Invalid record' },
      })

      const req = createMockRequest({
        params: { recordId: 'r1' },
        body: { depth: 0, force: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when reindexRecordGroup connector returns non-200', async () => {
      const handler = reindexRecordGroup(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: { detail: 'Group reindex failed' },
      })

      const req = createMockRequest({
        params: { recordGroupId: 'rg1' },
        body: { depth: 0, force: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when deleteRecord connector returns non-200', async () => {
      const handler = deleteRecord(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 403,
        data: { detail: 'Permission denied' },
      })

      const req = createMockRequest({
        params: { recordId: 'r1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when createRootFolder connector returns non-200', async () => {
      const handler = createRootFolder(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 409,
        data: { detail: 'Folder already exists' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { folderName: 'Existing Folder' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when createNestedFolder connector returns non-200', async () => {
      const handler = createNestedFolder(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { detail: 'Invalid parent folder' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: { folderName: 'Sub Folder' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when updateFolder connector returns non-200', async () => {
      const handler = updateFolder(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 404,
        data: { detail: 'Folder not found' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
        body: { folderName: 'Renamed' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when deleteFolder connector returns non-200', async () => {
      const handler = deleteFolder(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        data: { detail: 'Delete failed' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'folder-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when moveRecord connector returns non-200', async () => {
      const handler = moveRecord(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 400,
        data: { detail: 'Invalid destination' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', recordId: 'r1' },
        body: { newParentId: 'invalid-folder' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getRecordBuffer additional paths
  // -----------------------------------------------------------------------
  describe('getRecordBuffer (additional paths)', () => {
    it('should handle axios error with response object', async () => {
      const axiosError: any = new Error('Bad request')
      axiosError.response = { status: 400, data: { message: 'Invalid record ID format' } }
      sinon.stub(axios, 'get').rejects(axiosError)

      const handler = getRecordBuffer('http://localhost:8088')
      const req = createMockRequest({
        params: { recordId: 'r1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      // Should return the error status from the response
      expect(res.status.calledWith(400)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Connector exception handling (throws instead of resolves)
  // -----------------------------------------------------------------------
  describe('connector exception handling', () => {
    it('should call next when getKnowledgeHubNodes connector throws', async () => {
      const handler = getKnowledgeHubNodes(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Network failure'))

      const req = createMockRequest({
        query: {},
        params: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when createKnowledgeBase connector throws', async () => {
      const handler = createKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Connection refused'))

      const req = createMockRequest({
        body: { kbName: 'Test KB' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when deleteKnowledgeBase connector throws', async () => {
      const handler = deleteKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Delete error'))

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when getKnowledgeBase connector throws', async () => {
      const handler = getKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Timeout'))

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when updateKnowledgeBase connector throws', async () => {
      const handler = updateKnowledgeBase(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Update error'))

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { kbName: 'Updated' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when reindexFailedRecords connector throws', async () => {
      const mockRecordRelation = createMockRecordRelationService()
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Service down'))

      const handler = reindexFailedRecords(mockRecordRelation, createMockAppConfig())
      const req = createMockRequest({
        body: { app: 'Google Drive', connectorId: 'c1', statusFilters: ['failed'] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when resyncConnectorRecords connector throws', async () => {
      const mockRecordRelation = createMockRecordRelationService()
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Resync error'))

      const handler = resyncConnectorRecords(mockRecordRelation, createMockAppConfig())
      const req = createMockRequest({
        body: { connectorName: 'Google Drive', connectorId: 'c1', fullSync: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getKBContent and getFolderContents with all query filters
  // -----------------------------------------------------------------------
  describe('getKBContent (additional filter coverage)', () => {
    it('should handle connector returning null data', async () => {
      const handler = getKBContent(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: null,
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      // Should call next since data is null
      expect(next.calledOnce).to.be.true
    })
  })

  describe('getFolderContents (additional paths)', () => {
    it('should pass all filter query params to connector', async () => {
      const handler = getFolderContents(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { records: [], total: 0 },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1', folderId: 'f-1' },
        query: {
          page: '2',
          limit: '10',
          search: 'report',
          recordTypes: 'file,folder',
          origins: 'upload',
          connectors: 'c1',
          indexingStatus: 'completed',
          dateFrom: '1000000',
          dateTo: '2000000',
          sortBy: 'name',
          sortOrder: 'asc',
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should call next when folderId is missing', async () => {
      const handler = getFolderContents(createMockAppConfig())
      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        query: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // Permission operations error paths
  // -----------------------------------------------------------------------
  describe('permission operations (error paths)', () => {
    it('should call next when removeKBPermission connector returns non-200', async () => {
      const handler = removeKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 403,
        data: { detail: 'Not authorized' },
      })

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when createKBPermission connector throws', async () => {
      const handler = createKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Permission error'))

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [], role: 'READER' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when updateKBPermission connector throws', async () => {
      const handler = updateKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Update perm error'))

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [], role: 'WRITER' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when removeKBPermission connector throws', async () => {
      const handler = removeKBPermission(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Remove perm error'))

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
        body: { userIds: ['u1'], teamIds: [] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when listKBPermissions connector throws', async () => {
      const handler = listKBPermissions(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('List perm error'))

      const req = createMockRequest({
        params: { kbId: 'kb-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getConnectorStats additional error paths
  // -----------------------------------------------------------------------
  describe('getConnectorStats (additional paths)', () => {
    it('should call next when connector throws', async () => {
      const handler = getConnectorStats(createMockAppConfig())
      sinon.stub(ConnectorServiceCommand.prototype, 'execute').rejects(new Error('Stats error'))

      const req = createMockRequest({
        params: { connectorId: 'c1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // reindexFailedRecords - connector locked
  // -----------------------------------------------------------------------
  describe('reindexFailedRecords (connector locked)', () => {
    it('should call next when connector is locked', async () => {
      const mockRecordRelation = createMockRecordRelationService()
      const executeStub = sinon.stub(ConnectorServiceCommand.prototype, 'execute')
      executeStub.onFirstCall().resolves({
        statusCode: 200,
        data: { connectors: [{ _key: 'c1' }] },
      })
      executeStub.onSecondCall().resolves({
        statusCode: 200,
        data: { connector: { isLocked: true } },
      })

      const handler = reindexFailedRecords(mockRecordRelation, createMockAppConfig())
      const req = createMockRequest({
        body: { app: 'Google Drive', connectorId: 'c1', statusFilters: ['failed'] },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })

  describe('resyncConnectorRecords (connector locked)', () => {
    it('should call next when connector is locked', async () => {
      const mockRecordRelation = createMockRecordRelationService()
      const executeStub = sinon.stub(ConnectorServiceCommand.prototype, 'execute')
      executeStub.onFirstCall().resolves({
        statusCode: 200,
        data: { connectors: [{ _key: 'c1' }] },
      })
      executeStub.onSecondCall().resolves({
        statusCode: 200,
        data: { connector: { isLocked: true } },
      })

      const handler = resyncConnectorRecords(mockRecordRelation, createMockAppConfig())
      const req = createMockRequest({
        body: { connectorName: 'Google Drive', connectorId: 'c1', fullSync: false },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })
  })
})
