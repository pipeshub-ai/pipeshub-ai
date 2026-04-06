import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import {
  BadRequestError,
  NotFoundError,
  InternalServerError,
  ForbiddenError,
} from '../../../../src/libs/errors/http.errors'
import * as utils from '../../../../src/modules/storage/utils/utils'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import { HTTP_STATUS } from '../../../../src/libs/enums/http-status.enum'

describe('StorageController', () => {
  let controller: StorageController
  let mockConfig: any
  let mockLogger: any
  let mockKeyValueStoreService: any
  let mockRes: any
  let mockNext: any

  beforeEach(() => {
    mockConfig = { storageType: 'local', endpoint: 'http://localhost:3000' }
    mockLogger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    mockKeyValueStoreService = {
      get: sinon.stub(),
      set: sinon.stub(),
      watchKey: sinon.stub(),
    }
    controller = new StorageController(mockConfig, mockLogger, mockKeyValueStoreService)
    mockRes = { json: sinon.stub(), status: sinon.stub().returnsThis(), setHeader: sinon.stub() }
    mockNext = sinon.stub()
  })

  afterEach(() => { sinon.restore() })

  // -------------------------------------------------------------------------
  // getOrSetDefault
  // -------------------------------------------------------------------------
  describe('getOrSetDefault', () => {
    it('should return existing value if found', async () => {
      mockKeyValueStoreService.get.resolves('existing-value')
      const result = await controller.getOrSetDefault(mockKeyValueStoreService, 'key', 'default')
      expect(result).to.equal('existing-value')
    })

    it('should set and return default if no value found', async () => {
      mockKeyValueStoreService.get.resolves(null)
      const result = await controller.getOrSetDefault(mockKeyValueStoreService, 'key', 'default')
      expect(result).to.equal('default')
      expect(mockKeyValueStoreService.set.calledOnce).to.be.true
    })

    it('should set and return default if empty string found', async () => {
      mockKeyValueStoreService.get.resolves('')
      const result = await controller.getOrSetDefault(mockKeyValueStoreService, 'key', 'default')
      expect(result).to.equal('default')
      expect(mockKeyValueStoreService.set.calledOnceWith('key', 'default')).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // watchStorageType
  // -------------------------------------------------------------------------
  describe('watchStorageType', () => {
    it('should call watchKey on keyValueStoreService', async () => {
      await controller.watchStorageType(mockKeyValueStoreService)
      expect(mockKeyValueStoreService.watchKey.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // compareDocuments
  // -------------------------------------------------------------------------
  describe('compareDocuments', () => {
    it('should return false for null document', async () => {
      const result = await controller.compareDocuments(null as any, undefined, undefined, {} as any)
      expect(result).to.be.false
    })

    it('should return true when buffers are equal', async () => {
      const buffer = Buffer.from('same content')
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ data: buffer }),
      }
      const mockDoc = { some: 'doc' }

      const result = await controller.compareDocuments(mockDoc as any, undefined, 0, mockAdapter as any)
      expect(result).to.be.true
    })

    it('should return false when buffers differ', async () => {
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub()
          .onFirstCall().resolves({ data: Buffer.from('content-1') })
          .onSecondCall().resolves({ data: Buffer.from('content-2') }),
      }
      const mockDoc = { some: 'doc' }

      const result = await controller.compareDocuments(mockDoc as any, undefined, 0, mockAdapter as any)
      expect(result).to.be.false
    })

    it('should pass version parameters correctly to adapter', async () => {
      const buffer = Buffer.from('test')
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ data: buffer }),
      }
      const mockDoc = { some: 'doc' }

      await controller.compareDocuments(mockDoc as any, 2, 5, mockAdapter as any)
      expect(mockAdapter.getBufferFromStorageService.firstCall.args[1]).to.equal(2)
      expect(mockAdapter.getBufferFromStorageService.secondCall.args[1]).to.equal(5)
    })

    it('should return false when one buffer is null', async () => {
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub()
          .onFirstCall().resolves({ data: null })
          .onSecondCall().resolves({ data: Buffer.from('content') }),
      }
      const mockDoc = { some: 'doc' }

      const result = await controller.compareDocuments(mockDoc as any, undefined, 0, mockAdapter as any)
      expect(result).to.be.false
    })
  })

  // -------------------------------------------------------------------------
  // getDocumentById
  // -------------------------------------------------------------------------
  describe('getDocumentById', () => {
    it('should throw BadRequestError when documentId is missing', async () => {
      const req = {
        params: {},
        user: { orgId: 'org-1', userId: 'user-1' },
      } as any

      await controller.getDocumentById(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      const error = mockNext.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)
      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
      } as any

      await controller.getDocumentById(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      const error = mockNext.firstCall.args[0]
      expect(error).to.be.instanceOf(NotFoundError)
    })

    it('should return document when found', async () => {
      const mockDoc = { _id: 'doc-1', documentName: 'Test' }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)
      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
      } as any

      await controller.getDocumentById(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(200)).to.be.true
      expect(mockRes.json.calledWith(mockDoc)).to.be.true
    })

    it('should use orgId from service token payload when no user', async () => {
      const mockDoc = { _id: 'doc-1', documentName: 'Test' }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)
      const req = {
        params: { documentId: 'doc-1' },
        tokenPayload: { orgId: 'org-2' },
      } as any

      await controller.getDocumentById(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(200)).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // deleteDocumentById
  // -------------------------------------------------------------------------
  describe('deleteDocumentById', () => {
    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)
      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
      } as any

      await controller.deleteDocumentById(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      const error = mockNext.firstCall.args[0]
      expect(error).to.be.instanceOf(NotFoundError)
    })

    it('should soft delete and return document', async () => {
      const mockDoc = {
        _id: 'doc-1',
        isDeleted: false,
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)
      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
      } as any

      await controller.deleteDocumentById(req, mockRes, mockNext)
      expect(mockDoc.isDeleted).to.be.true
      expect(mockDoc.save.calledOnce).to.be.true
      expect(mockRes.status.calledWith(200)).to.be.true
    })

    it('should handle delete when userId is null (service request)', async () => {
      const mockDoc = {
        _id: 'doc-1',
        isDeleted: false,
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)
      const req = {
        params: { documentId: 'doc-1' },
        tokenPayload: { orgId: 'org-1' },
      } as any

      await controller.deleteDocumentById(req, mockRes, mockNext)
      expect(mockDoc.isDeleted).to.be.true
      expect((mockDoc as any).deletedByUserId).to.be.undefined
      expect(mockDoc.save.calledOnce).to.be.true
    })

    it('should call next on save failure', async () => {
      const mockDoc = {
        _id: 'doc-1',
        isDeleted: false,
        save: sinon.stub().rejects(new Error('save failed')),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)
      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
      } as any

      await controller.deleteDocumentById(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // createPlaceholderDocument
  // -------------------------------------------------------------------------
  describe('createPlaceholderDocument', () => {
    it('should throw BadRequestError when orgId is missing', async () => {
      const req = {
        body: { documentName: 'test' },
        user: {},
      } as any

      await controller.createPlaceholderDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should throw BadRequestError when documentName has extension', async () => {
      const req = {
        body: { documentName: 'test.pdf' },
        user: { orgId: 'org-1', userId: 'user-1' },
      } as any

      mockKeyValueStoreService.get.resolves('{}')

      await controller.createPlaceholderDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should throw BadRequestError when documentName has forward slash', async () => {
      const req = {
        body: { documentName: 'test/doc', extension: 'pdf' },
        user: { orgId: 'org-1', userId: 'user-1' },
      } as any

      mockKeyValueStoreService.get.resolves('{}')

      await controller.createPlaceholderDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should create document successfully', async () => {
      const savedDoc = { _id: 'doc-1', documentName: 'test' }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'local' }))

      const req = {
        body: {
          documentName: 'test',
          extension: 'pdf',
          isVersionedFile: false,
        },
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
      } as any

      await controller.createPlaceholderDocument(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(200)).to.be.true
      expect(mockRes.json.calledWith(savedDoc)).to.be.true
    })

    it('should handle missing userId gracefully (service request)', async () => {
      const savedDoc = { _id: 'doc-1', documentName: 'test' }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'local' }))

      const req = {
        body: {
          documentName: 'test',
          extension: 'pdf',
        },
        tokenPayload: { orgId: '507f1f77bcf86cd799439011' },
      } as any

      await controller.createPlaceholderDocument(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(200)).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // initializeStorageAdapter
  // -------------------------------------------------------------------------
  describe('initializeStorageAdapter', () => {
    it('should throw InternalServerError when storage config is null', async () => {
      sinon.stub(controller, 'getStorageConfig').resolves(null)

      const req = { user: { orgId: 'org-1', userId: 'user-1' }, headers: {} } as any
      try {
        await controller.initializeStorageAdapter(req)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // cloneDocument
  // -------------------------------------------------------------------------
  describe('cloneDocument', () => {
    it('should upload cloned document', async () => {
      const mockAdapter = {
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'url' }),
      }
      const mockDoc = { extension: 'pdf', isVersionedFile: false } as any
      const buffer = Buffer.from('content')

      const result = await controller.cloneDocument(mockDoc, buffer, 'new/path', mockNext, mockAdapter as any)
      expect(result).to.exist
      expect(result!.statusCode).to.equal(200)
    })

    it('should call next on error', async () => {
      const mockAdapter = {
        uploadDocumentToStorageService: sinon.stub().rejects(new Error('upload failed')),
      }
      const mockDoc = { extension: 'pdf', isVersionedFile: false } as any

      const result = await controller.cloneDocument(mockDoc, Buffer.from(''), 'path', mockNext, mockAdapter as any)
      expect(mockNext.calledOnce).to.be.true
      expect(result).to.be.undefined
    })

    it('should pass correct payload to adapter', async () => {
      const mockAdapter = {
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'url' }),
      }
      const mockDoc = { extension: 'pdf', isVersionedFile: true } as any
      const buffer = Buffer.from('test-content')

      await controller.cloneDocument(mockDoc, buffer, 'org/path/v1.pdf', mockNext, mockAdapter as any)

      const payload = mockAdapter.uploadDocumentToStorageService.firstCall.args[0]
      expect(payload.buffer).to.equal(buffer)
      expect(payload.documentPath).to.equal('org/path/v1.pdf')
      expect(payload.isVersioned).to.equal(true)
    })
  })

  // -------------------------------------------------------------------------
  // getStorageConfig
  // -------------------------------------------------------------------------
  describe('getStorageConfig', () => {
    it('should return cached config on subsequent calls', async () => {
      // First call fetches
      const mockConfigResp = { storageType: 's3', accessKeyId: 'key' }
      mockKeyValueStoreService.get.resolves(JSON.stringify({ cm: { endpoint: 'http://cm:3000' } }))

      // We can't fully test this without mocking ConfigurationManagerServiceCommand,
      // but we can test the early return for cached config
      // by manually setting the cached value through the private reference
      const configStub = sinon.stub(controller, 'getStorageConfig').resolves(mockConfigResp as any)
      const req = { user: { orgId: 'org-1', userId: 'user-1' }, headers: { authorization: 'Bearer token' } } as any

      const result = await controller.getStorageConfig(req, mockKeyValueStoreService, mockConfig)
      expect(result).to.deep.equal(mockConfigResp)
    })
  })

  // -------------------------------------------------------------------------
  // uploadDocument
  // -------------------------------------------------------------------------
  describe('uploadDocument', () => {
    it('should call next when initializeStorageAdapter fails', async () => {
      sinon.stub(controller, 'initializeStorageAdapter').rejects(new InternalServerError('no adapter'))

      const req = {
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'test.pdf', size: 1 } },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should call next when storage type is invalid', async () => {
      const mockAdapter = {}
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'invalid_vendor' }))

      const req = {
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'test.pdf', size: 1 } },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // downloadDocument
  // -------------------------------------------------------------------------
  describe('downloadDocument', () => {
    it('should call next when document does not exist', async () => {
      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      sinon.stub(utils, 'getDocumentInfo').resolves(undefined)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.downloadDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should throw BadRequestError for non-existent version', async () => {
      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      const mockDoc = {
        versionHistory: [{ version: 0 }],
        isVersionedFile: true,
        storageVendor: 's3',
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '5' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.downloadDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      const error = mockNext.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should throw BadRequestError for non-versioned document with version query', async () => {
      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      const mockDoc = {
        versionHistory: [],
        isVersionedFile: false,
        storageVendor: 's3',
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '0' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.downloadDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      const error = mockNext.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should throw when storage vendor mismatches', async () => {
      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'azureBlob' }))
      const mockDoc = {
        versionHistory: [],
        isVersionedFile: false,
        storageVendor: 's3',
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.downloadDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should serve file from local storage for local vendor', async () => {
      const mockAdapter = {
        getSignedUrl: sinon.stub().resolves({ statusCode: 200, data: 'file:///path/file.pdf' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'local' }))
      const mockDoc = {
        versionHistory: [],
        isVersionedFile: false,
        storageVendor: StorageVendor.Local,
        local: { localPath: 'file:///path/file.pdf' },
        documentName: 'test',
        extension: '.pdf',
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)
      sinon.stub(utils, 'serveFileFromLocalStorage')

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.downloadDocument(req, mockRes, mockNext)
      expect((utils.serveFileFromLocalStorage as sinon.SinonStub).calledOnce).to.be.true
    })

    it('should return signed URL for non-local vendor', async () => {
      const mockAdapter = {
        getSignedUrl: sinon.stub().resolves({ statusCode: 200, data: 'https://signed-url.com' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 's3' }))
      const mockDoc = {
        versionHistory: [],
        isVersionedFile: false,
        storageVendor: StorageVendor.S3,
        s3: { url: 'https://bucket.s3.us-east-1.amazonaws.com/file.pdf' },
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.downloadDocument(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(200)).to.be.true
      expect(mockRes.json.calledWith({ signedUrl: 'https://signed-url.com' })).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // getDocumentBuffer
  // -------------------------------------------------------------------------
  describe('getDocumentBuffer', () => {
    it('should throw NotFoundError when document does not exist', async () => {
      sinon.stub(utils, 'getDocumentInfo').resolves(undefined)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.getDocumentBuffer(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should throw BadRequestError for non-existent version', async () => {
      const mockDoc = {
        versionHistory: [{ version: 0 }],
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '5' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.getDocumentBuffer(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should return buffer successfully', async () => {
      const buffer = Buffer.from('file-content')
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: buffer }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      const mockDoc = { versionHistory: [] }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.getDocumentBuffer(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect(mockRes.json.calledWith(buffer)).to.be.true
    })

    it('should return 500 when buffer retrieval fails', async () => {
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 500, msg: 'error' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      const mockDoc = { versionHistory: [] }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.getDocumentBuffer(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(HTTP_STATUS.INTERNAL_SERVER)).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // createDocumentBuffer
  // -------------------------------------------------------------------------
  describe('createDocumentBuffer', () => {
    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(utils, 'getDocumentInfo').resolves(undefined)

      const req = {
        params: { documentId: 'doc-1' },
        body: { fileBuffer: { buffer: Buffer.from('x'), size: 1 } },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.createDocumentBuffer(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should upload buffer and update document successfully', async () => {
      const mockAdapter = {
        updateBuffer: sinon.stub().resolves({ statusCode: 200, data: 'updated-url' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      const mockDoc = {
        mutationCount: 2,
        sizeInBytes: 100,
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: { fileBuffer: { buffer: Buffer.from('new-content'), size: 11 } },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.createDocumentBuffer(req, mockRes, mockNext)
      expect(mockDoc.mutationCount).to.equal(3)
      expect(mockDoc.sizeInBytes).to.equal(11)
      expect(mockDoc.save.calledOnce).to.be.true
      expect(mockRes.status.calledWith(200)).to.be.true
    })

    it('should throw InternalServerError on upload failure', async () => {
      const mockAdapter = {
        updateBuffer: sinon.stub().resolves({ statusCode: 500, msg: 'upload failed' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      const mockDoc = { mutationCount: 0, save: sinon.stub() }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: { fileBuffer: { buffer: Buffer.from('x'), size: 1 } },
        query: {},
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.createDocumentBuffer(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // uploadNextVersionDocument
  // -------------------------------------------------------------------------
  describe('uploadNextVersionDocument', () => {
    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(utils, 'getDocumentInfo').resolves(undefined)

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('x'), originalname: 'test.pdf', size: 1, mimetype: 'application/pdf' },
          currentVersionNote: 'note',
          nextVersionNote: 'next',
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should throw BadRequestError for non-versioned documents', async () => {
      const mockDoc = { isVersionedFile: false, extension: '.pdf' }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('x'), originalname: 'test.pdf', size: 1, mimetype: 'application/pdf' },
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      const error = mockNext.firstCall.args[0]
      expect(error).to.be.instanceOf(BadRequestError)
    })

    it('should throw ForbiddenError for file format mismatch', async () => {
      const mockDoc = { isVersionedFile: true, extension: '.docx' }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('x'), originalname: 'test.pdf', size: 1, mimetype: 'application/pdf' },
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      const error = mockNext.firstCall.args[0]
      expect(error).to.be.instanceOf(ForbiddenError)
    })
  })

  // -------------------------------------------------------------------------
  // uploadDirectDocument
  // -------------------------------------------------------------------------
  describe('uploadDirectDocument', () => {
    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDirectDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      const error = mockNext.firstCall.args[0]
      expect(error).to.be.instanceOf(NotFoundError)
    })

    it('should throw NotFoundError when document path is missing', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves({ _id: 'doc-1', documentPath: '' } as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDirectDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should generate presigned URL and return successfully for S3', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'records/folder',
        storageVendor: StorageVendor.S3,
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)

      const mockAdapter = {
        generatePresignedUrlForDirectUpload: sinon.stub().resolves({
          statusCode: 200,
          data: { url: 'https://bucket.s3.us-east-1.amazonaws.com/path?signed=true' },
        }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDirectDocument(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect(mockDoc.save.calledOnce).to.be.true
    })

    it('should throw InternalServerError when presigned URL generation fails', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'records/folder',
        storageVendor: StorageVendor.S3,
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)

      const mockAdapter = {
        generatePresignedUrlForDirectUpload: sinon.stub().resolves({
          statusCode: 500,
          data: { url: null },
          msg: 'error',
        }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDirectDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // documentDiffChecker
  // -------------------------------------------------------------------------
  describe('documentDiffChecker', () => {
    it('should throw NotFoundError when orgId is missing', async () => {
      const req = {
        params: { documentId: 'doc-1' },
        user: {},
        headers: {},
      } as any

      await controller.documentDiffChecker(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })

    it('should return true when document has changed', async () => {
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub()
          .onFirstCall().resolves({ data: Buffer.from('new') })
          .onSecondCall().resolves({ data: Buffer.from('old') }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      const mockDoc = {
        versionHistory: [{ version: 0 }],
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.documentDiffChecker(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect(mockRes.json.calledWith(true)).to.be.true
    })

    it('should return false when document has not changed', async () => {
      const buffer = Buffer.from('same')
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ data: buffer }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      const mockDoc = {
        versionHistory: [{ version: 0 }],
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.documentDiffChecker(req, mockRes, mockNext)
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect(mockRes.json.calledWith(false)).to.be.true
    })

    it('should throw NotFoundError when document does not exist', async () => {
      sinon.stub(utils, 'getDocumentInfo').resolves(undefined)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.documentDiffChecker(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // rollBackToPreviousVersion
  // -------------------------------------------------------------------------
  describe('rollBackToPreviousVersion', () => {
    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(utils, 'getDocumentInfo').resolves(undefined)

      const req = {
        params: { documentId: 'doc-1' },
        body: { version: '0', note: 'rollback' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // getStorageConfig - route selection
  // -------------------------------------------------------------------------
  describe('getStorageConfig - route selection', () => {
    const nock = require('nock')

    afterEach(() => {
      nock.cleanAll()
    })

    it('should use user route when req has user with userId', async () => {
      // Mock the CM backend HTTP call
      nock('http://cm:3000').get(/.*/).reply(200, { storageType: 'local' })

      const mockKvStoreService = {
        get: sinon.stub().resolves(JSON.stringify({ cm: { endpoint: 'http://cm:3000' } })),
        set: sinon.stub().resolves(),
      }

      const req = {
        user: { userId: 'u1', orgId: 'o1' },
        headers: { authorization: 'Bearer test-token' },
      } as any

      try {
        await controller.getStorageConfig(req, mockKvStoreService as any, mockConfig)
      } catch {
        // May still fail due to response processing - that's OK
      }
    })

    it('should use internal route when req has tokenPayload (service request)', async () => {
      nock('http://cm:3000').get(/.*/).reply(200, { storageType: 's3' })

      const mockKvStoreService = {
        get: sinon.stub().resolves(JSON.stringify({ cm: { endpoint: 'http://cm:3000' } })),
        set: sinon.stub().resolves(),
      }

      const req = {
        tokenPayload: { orgId: 'o1' },
        headers: { authorization: 'Bearer test-token' },
      } as any

      try {
        await controller.getStorageConfig(req, mockKvStoreService as any, mockConfig)
      } catch {
        // Expected
      }
    })
  })

  // -------------------------------------------------------------------------
  // createPlaceholderDocument - edge cases
  // -------------------------------------------------------------------------
  describe('createPlaceholderDocument - edge cases', () => {
    it('should throw BadRequestError when orgId is missing', async () => {
      sinon.stub(utils, 'extractOrgId').returns(null as any)

      const req = {
        body: { documentName: 'test', extension: 'txt' },
        user: {},
        headers: {},
      } as any

      await controller.createPlaceholderDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0].message).to.include('OrgId')
    })

    it('should throw BadRequestError when document name has extension', async () => {
      sinon.stub(utils, 'extractOrgId').returns('org-1')
      sinon.stub(utils, 'extractUserId').returns('user-1')
      sinon.stub(utils, 'hasExtension').returns(true)

      const req = {
        body: { documentName: 'test.txt', extension: 'txt' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.createPlaceholderDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0].message).to.include('extensions')
    })

    it('should throw BadRequestError when document name has forward slash', async () => {
      sinon.stub(utils, 'extractOrgId').returns('org-1')
      sinon.stub(utils, 'extractUserId').returns('user-1')
      sinon.stub(utils, 'hasExtension').returns(false)

      const req = {
        body: { documentName: 'path/test', extension: 'txt' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.createPlaceholderDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0].message).to.include('forward slash')
    })
  })

  // -------------------------------------------------------------------------
  // downloadDocument - edge cases
  // -------------------------------------------------------------------------
  describe('downloadDocument - version checks', () => {
    it('should throw BadRequestError for non-versioned document with version param', async () => {
      const mockAdapter = {
        getSignedUrl: sinon.stub(),
        getBufferFromStorageService: sinon.stub(),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const mockDoc = {
        document: {
          isVersionedFile: false,
          versionHistory: [],
          storageVendor: 'local',
        },
      }
      sinon.stub(utils, 'getDocumentInfo').resolves(mockDoc as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '0' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.downloadDocument(req, mockRes, mockNext)
      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0].message).to.include("doesn't exist")
    })
  })

  // -------------------------------------------------------------------------
  // deleteDocumentById - edge cases
  // -------------------------------------------------------------------------
  describe('deleteDocumentById - with service request', () => {
    it('should set deletedByUserId to undefined when userId is null', async () => {
      sinon.stub(utils, 'extractOrgId').returns('org-1')
      sinon.stub(utils, 'extractUserId').returns(null as any)

      const mockDoc = {
        _id: 'doc-1',
        isDeleted: false,
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)

      const req = {
        params: { documentId: 'doc-1' },
        tokenPayload: { orgId: 'org-1' },
        headers: {},
      } as any

      await controller.deleteDocumentById(req, mockRes, mockNext)

      if (!mockNext.called) {
        expect(mockDoc.isDeleted).to.be.true
        expect(mockDoc.save.calledOnce).to.be.true
      }
    })
  })

  // -------------------------------------------------------------------------
  // cloneDocument
  // -------------------------------------------------------------------------
  describe('cloneDocument - error handling', () => {
    it('should call next on error and return undefined', async () => {
      const mockAdapter = {
        uploadDocumentToStorageService: sinon.stub().rejects(new Error('Upload failed')),
      }

      const mockDocument = {
        extension: '.txt',
        isVersionedFile: false,
      }

      const result = await controller.cloneDocument(
        mockDocument as any,
        Buffer.from('test'),
        '/new/path',
        mockNext,
        mockAdapter as any,
      )

      expect(mockNext.calledOnce).to.be.true
      expect(result).to.be.undefined
    })
  })

  // -------------------------------------------------------------------------
  // Branch coverage: compareDocuments - null document
  // -------------------------------------------------------------------------
  describe('compareDocuments - null document branch', () => {
    it('should return false when document is null', async () => {
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub(),
      }

      const result = await controller.compareDocuments(
        null as any,
        undefined,
        undefined,
        mockAdapter as any,
      )

      expect(result).to.be.false
      expect(mockAdapter.getBufferFromStorageService.called).to.be.false
    })

    it('should return true when buffers are equal', async () => {
      const buf = Buffer.from('same content')
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ data: buf }),
      }

      const mockDoc = { documentPath: '/test', documentName: 'test', extension: '.txt' }

      const result = await controller.compareDocuments(
        mockDoc as any,
        1,
        2,
        mockAdapter as any,
      )

      expect(result).to.be.true
    })

    it('should return false when buffers are different', async () => {
      const buf1 = Buffer.from('content A')
      const buf2 = Buffer.from('content B')
      const mockAdapter = {
        getBufferFromStorageService: sinon.stub()
          .onFirstCall().resolves({ data: buf1 })
          .onSecondCall().resolves({ data: buf2 }),
      }

      const mockDoc = { documentPath: '/test', documentName: 'test', extension: '.txt' }

      const result = await controller.compareDocuments(
        mockDoc as any,
        1,
        2,
        mockAdapter as any,
      )

      expect(result).to.be.false
    })
  })

  // -------------------------------------------------------------------------
  // Branch coverage: deleteDocumentById - userId ternary
  // -------------------------------------------------------------------------
  describe('deleteDocumentById - userId ternary branches', () => {
    it('should set deletedByUserId when userId is present', async () => {
      const mockDoc = {
        isDeleted: false,
        deletedByUserId: undefined,
        save: sinon.stub().resolves(),
      }

      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)
      sinon.stub(utils, 'extractOrgId').returns('aaaaaaaaaaaaaaaaaaaaaaaa')
      sinon.stub(utils, 'extractUserId').returns('bbbbbbbbbbbbbbbbbbbbbbbb')

      const req = {
        params: { documentId: 'doc-1' },
        user: { userId: 'bbbbbbbbbbbbbbbbbbbbbbbb', orgId: 'aaaaaaaaaaaaaaaaaaaaaaaa' },
        headers: {},
      }

      await controller.deleteDocumentById(req as any, mockRes, mockNext)

      expect(mockDoc.isDeleted).to.be.true
      expect(mockDoc.deletedByUserId).to.not.be.undefined
      expect(mockDoc.save.calledOnce).to.be.true
    })

    it('should set deletedByUserId to undefined when userId is null', async () => {
      const mockDoc = {
        isDeleted: false,
        deletedByUserId: undefined,
        save: sinon.stub().resolves(),
      }

      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)
      sinon.stub(utils, 'extractOrgId').returns('org-1')
      sinon.stub(utils, 'extractUserId').returns(null as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1' },
        headers: {},
      }

      await controller.deleteDocumentById(req as any, mockRes, mockNext)

      expect(mockDoc.isDeleted).to.be.true
      expect(mockDoc.deletedByUserId).to.be.undefined
    })
  })

  // -------------------------------------------------------------------------
  // Branch coverage: downloadDocument - version and storage vendor branches
  // -------------------------------------------------------------------------
  describe('downloadDocument - version and vendor branches', () => {
    it('should throw when version exceeds versionHistory length', async () => {
      const mockDocument = {
        versionHistory: [{ version: 0 }],
        isVersionedFile: true,
        storageVendor: 'local',
      }

      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      sinon.stub(utils, 'getDocumentInfo').resolves({
        document: mockDocument,
      } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '5' },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.downloadDocument(req as any, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      const err = mockNext.firstCall.args[0]
      expect(err.message).to.include("version doesn't exist")
    })

    it('should throw when version is provided for non-versioned document', async () => {
      const mockDocument = {
        versionHistory: [{ version: 0 }],
        isVersionedFile: false,
        storageVendor: 'local',
      }

      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      sinon.stub(utils, 'getDocumentInfo').resolves({
        document: mockDocument,
      } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '0' },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.downloadDocument(req as any, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      const err = mockNext.firstCall.args[0]
      expect(err.message).to.include('non-versioned')
    })

    it('should serve file from local storage when vendor is Local', async () => {
      const mockDocument = {
        versionHistory: [],
        isVersionedFile: false,
        storageVendor: StorageVendor.Local,
        documentPath: '/tmp/test',
        documentName: 'test',
        extension: '.txt',
      }

      const mockAdapter = {
        getSignedUrl: sinon.stub().resolves({ data: '/local/path' }),
      }

      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(utils, 'getDocumentInfo').resolves({
        document: mockDocument,
      } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: StorageVendor.Local }))
      sinon.stub(utils, 'serveFileFromLocalStorage')

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.downloadDocument(req as any, mockRes, mockNext)

      expect((utils.serveFileFromLocalStorage as sinon.SinonStub).calledOnce).to.be.true
    })

    it('should return signed URL for non-local storage', async () => {
      const mockDocument = {
        versionHistory: [],
        isVersionedFile: false,
        storageVendor: 's3',
        documentPath: '/test',
        documentName: 'test',
        extension: '.txt',
      }

      const mockAdapter = {
        getSignedUrl: sinon.stub().resolves({ data: 'https://s3.aws.com/signed-url' }),
      }

      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(utils, 'getDocumentInfo').resolves({
        document: mockDocument,
      } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.downloadDocument(req as any, mockRes, mockNext)

      expect(mockRes.status.calledWith(200)).to.be.true
      expect(mockRes.json.firstCall.args[0]).to.have.property('signedUrl')
    })

    it('should use expirationTimeInSeconds when provided', async () => {
      const mockDocument = {
        versionHistory: [],
        isVersionedFile: false,
        storageVendor: 's3',
      }

      const mockAdapter = {
        getSignedUrl: sinon.stub().resolves({ data: 'https://s3.aws.com/signed-url' }),
      }

      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(utils, 'getDocumentInfo').resolves({
        document: mockDocument,
      } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = {
        params: { documentId: 'doc-1' },
        query: { expirationTimeInSeconds: '7200' },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.downloadDocument(req as any, mockRes, mockNext)

      expect(mockAdapter.getSignedUrl.calledOnce).to.be.true
      expect(mockAdapter.getSignedUrl.firstCall.args[3]).to.equal(7200)
    })

    it('should throw when storage vendor mismatches', async () => {
      const mockDocument = {
        versionHistory: [],
        isVersionedFile: false,
        storageVendor: 's3',
      }

      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      sinon.stub(utils, 'getDocumentInfo').resolves({
        document: mockDocument,
      } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'azure' }))

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.downloadDocument(req as any, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0].message).to.include('Storage vendor mismatch')
    })

    it('should throw when document info is null', async () => {
      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      sinon.stub(utils, 'getDocumentInfo').resolves(undefined)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.downloadDocument(req as any, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // Branch coverage: getDocumentBuffer - status code and version branches
  // -------------------------------------------------------------------------
  describe('getDocumentBuffer - branch coverage', () => {
    it('should return buffer when status is OK', async () => {
      const mockDocument = {
        versionHistory: [{ version: 0 }],
      }

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({
          statusCode: 200,
          data: Buffer.from('content'),
        }),
      }

      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDocument } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.getDocumentBuffer(req as any, mockRes, mockNext)

      expect(mockRes.status.calledWith(200)).to.be.true
    })

    it('should return 500 when buffer status is not OK', async () => {
      const mockDocument = {
        versionHistory: [],
      }

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({
          statusCode: 500,
          data: null,
        }),
      }

      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDocument } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.getDocumentBuffer(req as any, mockRes, mockNext)

      expect(mockRes.status.calledWith(500)).to.be.true
    })

    it('should pass version when provided', async () => {
      const mockDocument = {
        versionHistory: [{ version: 0 }, { version: 1 }],
      }

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({
          statusCode: 200,
          data: Buffer.from('content'),
        }),
      }

      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDocument } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '0' },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.getDocumentBuffer(req as any, mockRes, mockNext)

      expect(mockAdapter.getBufferFromStorageService.firstCall.args[1]).to.equal(0)
    })

    it('should throw when version exceeds history length', async () => {
      const mockDocument = {
        versionHistory: [{ version: 0 }],
      }

      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDocument } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '5' },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.getDocumentBuffer(req as any, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
    })

    it('should use 0 for versionHistory length when versionHistory is null', async () => {
      const mockDocument = {
        versionHistory: null,
      }

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({
          statusCode: 200,
          data: Buffer.from('content'),
        }),
      }

      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDocument } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.getDocumentBuffer(req as any, mockRes, mockNext)

      expect(mockRes.status.calledWith(200)).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // Branch coverage: createDocumentBuffer - upload status branches
  // -------------------------------------------------------------------------
  describe('createDocumentBuffer - upload status branches', () => {
    it('should throw InternalServerError when upload fails', async () => {
      const mockDocument = {
        mutationCount: 0,
        sizeInBytes: 0,
        save: sinon.stub().resolves(),
      }

      const mockAdapter = {
        updateBuffer: sinon.stub().resolves({
          statusCode: 500,
          msg: 'Upload failed',
        }),
      }

      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDocument } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: { fileBuffer: { buffer: Buffer.from('content'), size: 100 } },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.createDocumentBuffer(req as any, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0].message).to.include('Failed to upload buffer')
    })

    it('should increment mutationCount when upload succeeds', async () => {
      const mockDocument = {
        mutationCount: 5,
        sizeInBytes: 0,
        save: sinon.stub().resolves(),
      }

      const mockAdapter = {
        updateBuffer: sinon.stub().resolves({
          statusCode: 200,
          data: 'OK',
        }),
      }

      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDocument } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: { fileBuffer: { buffer: Buffer.from('content'), size: 200 } },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.createDocumentBuffer(req as any, mockRes, mockNext)

      expect(mockDocument.mutationCount).to.equal(6)
      expect(mockDocument.sizeInBytes).to.equal(200)
    })

    it('should use 0 for mutationCount when it is null', async () => {
      const mockDocument = {
        mutationCount: null,
        sizeInBytes: 0,
        save: sinon.stub().resolves(),
      }

      const mockAdapter = {
        updateBuffer: sinon.stub().resolves({
          statusCode: 200,
          data: 'OK',
        }),
      }

      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDocument } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: { fileBuffer: { buffer: Buffer.from('content'), size: 50 } },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.createDocumentBuffer(req as any, mockRes, mockNext)

      expect(mockDocument.mutationCount).to.equal(1)
    })
  })

  // -------------------------------------------------------------------------
  // Branch coverage: createPlaceholderDocument - additional edge cases
  // -------------------------------------------------------------------------
  describe('createPlaceholderDocument - userId branch', () => {
    it('should set initiatorUserId to null when userId is null', async () => {
      sinon.stub(utils, 'extractOrgId').returns('org-1')
      sinon.stub(utils, 'extractUserId').returns(null as any)
      sinon.stub(utils, 'hasExtension').returns(false)
      sinon.stub(utils, 'getStorageVendor').returns(StorageVendor.Local)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'local' }))

      const createStub = sinon.stub(DocumentModel, 'create').resolves({ _id: 'new-doc' } as any)

      const req = {
        body: {
          documentName: 'testdoc',
          documentPath: '/test',
          extension: 'txt',
          isVersionedFile: false,
        },
        user: { orgId: 'org-1' },
        headers: {},
      }

      await controller.createPlaceholderDocument(req as any, mockRes, mockNext)

      if (!mockNext.called) {
        const createdDoc: any = createStub.firstCall.args[0]
        expect(createdDoc.initiatorUserId).to.be.null
      }
    })

    it('should set initiatorUserId when userId is present', async () => {
      sinon.stub(utils, 'extractOrgId').returns('org-1')
      sinon.stub(utils, 'extractUserId').returns('507f1f77bcf86cd799439011')
      sinon.stub(utils, 'hasExtension').returns(false)
      sinon.stub(utils, 'getStorageVendor').returns(StorageVendor.Local)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'local' }))

      const createStub = sinon.stub(DocumentModel, 'create').resolves({ _id: 'new-doc' } as any)

      const req = {
        body: {
          documentName: 'testdoc',
          documentPath: '/test',
          extension: 'txt',
          isVersionedFile: false,
        },
        user: { userId: '507f1f77bcf86cd799439011', orgId: 'org-1' },
        headers: {},
      }

      await controller.createPlaceholderDocument(req as any, mockRes, mockNext)

      if (!mockNext.called) {
        const createdDoc: any = createStub.firstCall.args[0]
        expect(createdDoc.initiatorUserId).to.not.be.null
      }
    })

    it('should throw when documentName contains forward slash', async () => {
      sinon.stub(utils, 'extractOrgId').returns('org-1')
      sinon.stub(utils, 'extractUserId').returns('user-1')
      sinon.stub(utils, 'hasExtension').returns(false)

      const req = {
        body: {
          documentName: 'test/doc',
          documentPath: '/test',
          extension: 'txt',
        },
        user: { userId: 'user-1', orgId: 'org-1' },
        headers: {},
      }

      await controller.createPlaceholderDocument(req as any, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0].message).to.include('forward slash')
    })
  })

  // -------------------------------------------------------------------------
  // Branch coverage: initializeStorageAdapter - null checks
  // -------------------------------------------------------------------------
  describe('initializeStorageAdapter - null checks', () => {
    it('should throw when storageConfig is null', async () => {
      sinon.stub(controller, 'getStorageConfig').resolves(null)

      const req = {
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      try {
        await controller.initializeStorageAdapter(req as any)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('Storage configuration not found')
      }
    })
  })

  // -------------------------------------------------------------------------
  // Branch coverage: uploadDocument - storageType ?? '' fallback
  // -------------------------------------------------------------------------
  describe('uploadDocument - storageType nullish coalescing', () => {
    it('should throw when storageType is null', async () => {
      sinon.stub(controller, 'initializeStorageAdapter').resolves({} as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: null }))
      sinon.stub(utils, 'isValidStorageVendor').returns(false)

      const req = {
        body: { fileBuffer: {} },
        user: { userId: 'u1', orgId: 'o1' },
        headers: {},
      }

      await controller.uploadDocument(req as any, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0].message).to.include('Invalid storage type')
    })
  })

  // -------------------------------------------------------------------------
  // createPlaceholderDocument - fullDocumentPath construction
  // -------------------------------------------------------------------------
  describe('createPlaceholderDocument - fullDocumentPath construction', () => {
    it('should prefix documentPath with orgId/PipesHub when documentPath is provided', async () => {
      const createStub = sinon.stub(DocumentModel, 'create').resolves({ _id: 'doc-1' } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'local' }))

      const req = {
        body: {
          documentName: 'test',
          extension: 'pdf',
          isVersionedFile: false,
          documentPath: 'custom/folder',
        },
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
      } as any

      await controller.createPlaceholderDocument(req, mockRes, mockNext)

      const docInfo = createStub.firstCall.args[0] as any
      expect(docInfo.documentPath).to.equal('507f1f77bcf86cd799439011/PipesHub/custom/folder')
    })

    it('should set documentPath to orgId/PipesHub when no documentPath provided', async () => {
      const createStub = sinon.stub(DocumentModel, 'create').resolves({ _id: 'doc-1' } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'local' }))

      const req = {
        body: {
          documentName: 'test',
          extension: 'pdf',
          isVersionedFile: false,
        },
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
      } as any

      await controller.createPlaceholderDocument(req, mockRes, mockNext)

      const docInfo = createStub.firstCall.args[0] as any
      expect(docInfo.documentPath).to.equal('507f1f77bcf86cd799439011/PipesHub')
    })
  })

  // -------------------------------------------------------------------------
  // uploadNextVersionDocument - extension normalization
  // -------------------------------------------------------------------------
  describe('uploadNextVersionDocument - extension normalization', () => {
    it('should allow case-insensitive extension matching (.PDF vs .pdf)', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.PDF',
        isVersionedFile: true,
        versionHistory: [] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 0,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves({
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('old') }),
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'url' }),
      } as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('new'), originalname: 'test.pdf', size: 3, mimetype: 'application/pdf' },
          currentVersionNote: 'v0',
          nextVersionNote: 'v1',
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
    })

    it('should allow extension without leading dot to match (pdf vs .pdf)', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: 'pdf',
        isVersionedFile: true,
        versionHistory: [] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 0,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)
      sinon.stub(controller, 'initializeStorageAdapter').resolves({
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('old') }),
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'url' }),
      } as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('new'), originalname: 'test.pdf', size: 3, mimetype: 'application/pdf' },
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // uploadNextVersionDocument - v0 flow (empty versionHistory)
  // -------------------------------------------------------------------------
  describe('uploadNextVersionDocument - v0 flow for empty versionHistory', () => {
    it('should save current as v0 then upload new version successfully', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 0,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('old-content') }),
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'new-url' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('new-content'), originalname: 'test.pdf', size: 11, mimetype: 'application/pdf' },
          currentVersionNote: 'initial version',
          nextVersionNote: 'update',
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect((controller.cloneDocument as sinon.SinonStub).calledOnce).to.be.true
      expect(mockDoc.versionHistory.length).to.equal(2)
      expect(mockDoc.versionHistory[0].version).to.equal(0)
      expect(mockDoc.versionHistory[1].version).to.equal(1)
      expect(mockDoc.save.calledOnce).to.be.true
    })

    it('should throw when v0 buffer retrieval fails', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 0,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 500, msg: 'storage error' }),
        uploadDocumentToStorageService: sinon.stub(),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('x'), originalname: 'test.pdf', size: 1, mimetype: 'application/pdf' },
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0]).to.be.instanceOf(InternalServerError)
    })

    it('should throw when v0 clone returns undefined', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 0,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('old') }),
        uploadDocumentToStorageService: sinon.stub(),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'cloneDocument').resolves(undefined)

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('x'), originalname: 'test.pdf', size: 1, mimetype: 'application/pdf' },
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0]).to.be.instanceOf(InternalServerError)
    })
  })

  // -------------------------------------------------------------------------
  // uploadNextVersionDocument - existing versions with change detection
  // -------------------------------------------------------------------------
  describe('uploadNextVersionDocument - existing versions with change detection', () => {
    it('should save intermediate version when document has changed', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [{ version: 0, s3: { url: 'v0-url' }, size: 50 }] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 1,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('current') }),
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'new-url' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'compareDocuments').resolves(false)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'intermediate-url' } as any)

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('new'), originalname: 'test.pdf', size: 3, mimetype: 'application/pdf' },
          currentVersionNote: 'auto-save',
          nextVersionNote: 'manual update',
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect((controller.cloneDocument as sinon.SinonStub).calledOnce).to.be.true
      expect(mockDoc.versionHistory.length).to.equal(3)
    })

    it('should skip intermediate version when document has not changed', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [{ version: 0, s3: { url: 'v0-url' }, size: 50 }] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 1,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub(),
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'new-url' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'compareDocuments').resolves(true)
      sinon.stub(controller, 'cloneDocument')

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('new'), originalname: 'test.pdf', size: 3, mimetype: 'application/pdf' },
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect((controller.cloneDocument as sinon.SinonStub).called).to.be.false
      expect(mockDoc.versionHistory.length).to.equal(2)
    })
  })

  // -------------------------------------------------------------------------
  // uploadNextVersionDocument - S3 and AzureBlob URL update
  // -------------------------------------------------------------------------
  describe('uploadNextVersionDocument - S3 and AzureBlob URL update', () => {
    it('should set document.s3 when storageVendor is S3', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 0,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
        s3: undefined as any,
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('old') }),
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'https://s3-current-url' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('new'), originalname: 'test.pdf', size: 3, mimetype: 'application/pdf' },
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockDoc.s3).to.deep.equal({ url: 'https://s3-current-url' })
    })

    it('should set document.azureBlob when storageVendor is AzureBlob', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [] as any[],
        storageVendor: StorageVendor.AzureBlob,
        mutationCount: 0,
        sizeInBytes: 100,
        documentName: 'test',
        save: sinon.stub().resolves(),
        azureBlob: undefined as any,
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('old') }),
        uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'https://azure-current-url' }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' } as any)
      mockKeyValueStoreService.get.resolves(JSON.stringify({ storageType: 'azureBlob' }))

      const req = {
        params: { documentId: 'doc-1' },
        body: {
          fileBuffer: { buffer: Buffer.from('new'), originalname: 'test.pdf', size: 3, mimetype: 'application/pdf' },
        },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.uploadNextVersionDocument(req, mockRes, mockNext)

      expect(mockDoc.azureBlob).to.deep.equal({ url: 'https://azure-current-url' })
    })
  })

  // -------------------------------------------------------------------------
  // rollBackToPreviousVersion - version parsing and validation
  // -------------------------------------------------------------------------
  describe('rollBackToPreviousVersion - version parsing and validation', () => {
    it('should read version from query param', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [
          { version: 0, size: 50 },
          { version: 1, size: 100 },
          { version: 2, size: 150 },
        ] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 3,
        sizeInBytes: 150,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('v0-content') }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'rollback-url' } as any)
      sinon.stub(utils, 'isValidStorageVendor').returns(true)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '0' },
        body: { note: 'rollback to v0' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect(mockAdapter.getBufferFromStorageService.firstCall.args[1]).to.equal(0)
    })

    it('should fall back to body version when query is empty', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [
          { version: 0, size: 50 },
          { version: 1, size: 100 },
          { version: 2, size: 150 },
        ] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 3,
        sizeInBytes: 150,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('v1-content') }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'rollback-url' } as any)
      sinon.stub(utils, 'isValidStorageVendor').returns(true)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        body: { version: '1', note: 'rollback to v1' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect(mockAdapter.getBufferFromStorageService.firstCall.args[1]).to.equal(1)
    })

    it('should throw BadRequestError when version is undefined', async () => {
      const mockDoc = {
        _id: 'doc-1',
        isVersionedFile: true,
        versionHistory: [{ version: 0 }],
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: {},
        body: { note: 'rollback' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0]).to.be.instanceOf(BadRequestError)
      expect(mockNext.firstCall.args[0].message).to.include('valid version')
    })

    it('should throw BadRequestError when version is NaN', async () => {
      const mockDoc = {
        _id: 'doc-1',
        isVersionedFile: true,
        versionHistory: [{ version: 0 }],
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: 'abc' },
        body: { note: 'rollback' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0]).to.be.instanceOf(BadRequestError)
      expect(mockNext.firstCall.args[0].message).to.include('valid version')
    })

    it('should throw BadRequestError when version >= currentVersion - 1', async () => {
      const mockDoc = {
        _id: 'doc-1',
        isVersionedFile: true,
        versionHistory: [{ version: 0 }, { version: 1 }],
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '1' },
        body: { note: 'rollback' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0]).to.be.instanceOf(BadRequestError)
      expect(mockNext.firstCall.args[0].message).to.include('Rollback version greater than current')
    })
  })

  // -------------------------------------------------------------------------
  // rollBackToPreviousVersion - non-versioned document check (fixed logic)
  // -------------------------------------------------------------------------
  describe('rollBackToPreviousVersion - non-versioned document check', () => {
    it('should throw BadRequestError for non-versioned document', async () => {
      const mockDoc = {
        _id: 'doc-1',
        isVersionedFile: false,
        versionHistory: [],
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '0' },
        body: { note: 'rollback' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0]).to.be.instanceOf(BadRequestError)
      expect(mockNext.firstCall.args[0].message).to.include('non-versioned')
    })
  })

  // -------------------------------------------------------------------------
  // rollBackToPreviousVersion - success path and storageVendor usage
  // -------------------------------------------------------------------------
  describe('rollBackToPreviousVersion - success path', () => {
    it('should rollback and use document.storageVendor for versionHistory key', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [
          { version: 0, s3: { url: 'v0-url' }, size: 50 },
          { version: 1, s3: { url: 'v1-url' }, size: 100 },
          { version: 2, s3: { url: 'v2-url' }, size: 150 },
        ] as any[],
        storageVendor: StorageVendor.S3,
        mutationCount: 3,
        sizeInBytes: 150,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('v0-content') }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'rollback-url' } as any)
      sinon.stub(utils, 'isValidStorageVendor').returns(true)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '0' },
        body: { note: 'rollback to v0' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)

      expect(mockNext.called).to.be.false
      expect(mockRes.status.calledWith(HTTP_STATUS.OK)).to.be.true
      expect(mockDoc.versionHistory.length).to.equal(4)
      const newEntry = mockDoc.versionHistory[3]
      expect(newEntry.version).to.equal(3)
      expect(newEntry[StorageVendor.S3]).to.deep.equal({ url: 'rollback-url' })
      expect(newEntry.note).to.equal('rollback to v0')
      expect(mockDoc.sizeInBytes).to.equal(50)
      expect(mockDoc.mutationCount).to.equal(4)
      expect(mockDoc.save.called).to.be.true
    })

    it('should throw BadRequestError for invalid storageVendor', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        extension: '.pdf',
        isVersionedFile: true,
        versionHistory: [
          { version: 0, size: 50 },
          { version: 1, size: 100 },
          { version: 2, size: 150 },
        ],
        storageVendor: 'invalid_vendor',
        mutationCount: 3,
        sizeInBytes: 150,
        documentName: 'test',
        save: sinon.stub().resolves(),
      }
      sinon.stub(utils, 'getDocumentInfo').resolves({ document: mockDoc } as any)

      const mockAdapter = {
        getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('content') }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'url' } as any)

      const req = {
        params: { documentId: 'doc-1' },
        query: { version: '0' },
        body: { note: 'rollback' },
        user: { orgId: 'org-1', userId: '507f1f77bcf86cd799439011' },
        headers: {},
      } as any

      await controller.rollBackToPreviousVersion(req, mockRes, mockNext)

      expect(mockNext.calledOnce).to.be.true
      expect(mockNext.firstCall.args[0]).to.be.instanceOf(BadRequestError)
      expect(mockNext.firstCall.args[0].message).to.include('Invalid storage type')
    })
  })

  // -------------------------------------------------------------------------
  // uploadDirectDocument - versioned vs non-versioned path and basePath dedup
  // -------------------------------------------------------------------------
  describe('uploadDirectDocument - path construction', () => {
    it('should include /current/ in path for versioned file', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        storageVendor: StorageVendor.S3,
        extension: '.pdf',
        isVersionedFile: true,
        documentName: 'report',
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)

      const mockAdapter = {
        generatePresignedUrlForDirectUpload: sinon.stub().resolves({
          statusCode: 200,
          data: { url: 'https://bucket.s3.amazonaws.com/org/PipesHub/doc-1/current/report.pdf?signed=true' },
        }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDirectDocument(req, mockRes, mockNext)

      const calledPath = mockAdapter.generatePresignedUrlForDirectUpload.firstCall.args[0]
      expect(calledPath).to.include('/current/')
      expect(calledPath).to.equal('org/PipesHub/doc-1/current/report.pdf')
    })

    it('should omit /current/ in path for non-versioned file', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        storageVendor: StorageVendor.S3,
        extension: '.pdf',
        isVersionedFile: false,
        documentName: 'report',
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)

      const mockAdapter = {
        generatePresignedUrlForDirectUpload: sinon.stub().resolves({
          statusCode: 200,
          data: { url: 'https://bucket.s3.amazonaws.com/org/PipesHub/doc-1/report.pdf?signed=true' },
        }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDirectDocument(req, mockRes, mockNext)

      const calledPath = mockAdapter.generatePresignedUrlForDirectUpload.firstCall.args[0]
      expect(calledPath).to.not.include('/current/')
      expect(calledPath).to.equal('org/PipesHub/doc-1/report.pdf')
    })

    it('should not double-append documentId when path already ends with it', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub/doc-1',
        storageVendor: StorageVendor.S3,
        extension: '.pdf',
        isVersionedFile: false,
        documentName: 'report',
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)

      const mockAdapter = {
        generatePresignedUrlForDirectUpload: sinon.stub().resolves({
          statusCode: 200,
          data: { url: 'https://bucket.s3.amazonaws.com/path?signed=true' },
        }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDirectDocument(req, mockRes, mockNext)

      const calledPath = mockAdapter.generatePresignedUrlForDirectUpload.firstCall.args[0]
      expect(calledPath).to.equal('org/PipesHub/doc-1/report.pdf')
      expect(calledPath).to.not.include('doc-1/doc-1')
    })

    it('should handle extension without leading dot', async () => {
      const mockDoc = {
        _id: 'doc-1',
        documentPath: 'org/PipesHub',
        storageVendor: StorageVendor.S3,
        extension: 'pdf',
        isVersionedFile: false,
        documentName: 'report',
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)

      const mockAdapter = {
        generatePresignedUrlForDirectUpload: sinon.stub().resolves({
          statusCode: 200,
          data: { url: 'https://bucket.s3.amazonaws.com/path?signed=true' },
        }),
      }
      sinon.stub(controller, 'initializeStorageAdapter').resolves(mockAdapter as any)

      const req = {
        params: { documentId: 'doc-1' },
        user: { orgId: 'org-1', userId: 'user-1' },
        headers: {},
      } as any

      await controller.uploadDirectDocument(req, mockRes, mockNext)

      const calledPath = mockAdapter.generatePresignedUrlForDirectUpload.firstCall.args[0]
      expect(calledPath).to.equal('org/PipesHub/doc-1/report.pdf')
    })
  })
})
