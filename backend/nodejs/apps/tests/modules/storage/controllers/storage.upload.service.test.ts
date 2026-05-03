import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { UploadDocumentService } from '../../../../src/modules/storage/controllers/storage.upload.service'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import { BadRequestError, InternalServerError } from '../../../../src/libs/errors/http.errors'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { HTTP_STATUS } from '../../../../src/libs/enums/http-status.enum'

describe('UploadDocumentService', () => {
  let mockAdapter: any
  let mockKeyValueStoreService: any
  let mockDefaultConfig: any

  beforeEach(() => {
    mockAdapter = {
      uploadDocumentToStorageService: sinon.stub(),
      generatePresignedUrlForDirectUpload: sinon.stub(),
    }
    mockKeyValueStoreService = {
      get: sinon.stub(),
      set: sinon.stub(),
    }
    mockDefaultConfig = {
      storageType: 'local',
      endpoint: 'http://localhost:3000',
    }
  })

  afterEach(() => { sinon.restore() })

  // -------------------------------------------------------------------------
  // constructor
  // -------------------------------------------------------------------------
  describe('constructor', () => {
    it('should create an instance with all dependencies', () => {
      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )
      expect(service).to.be.instanceOf(UploadDocumentService)
    })

    it('should create an instance with S3 vendor', () => {
      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.S3,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )
      expect(service).to.be.instanceOf(UploadDocumentService)
    })

    it('should create an instance with Azure vendor', () => {
      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.AzureBlob,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )
      expect(service).to.be.instanceOf(UploadDocumentService)
    })
  })

  // -------------------------------------------------------------------------
  // uploadDocument
  // -------------------------------------------------------------------------
  describe('uploadDocument', () => {
    it('should throw BadRequestError when file has no extension', async () => {
      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'noextension', size: 4, mimetype: 'text/plain' } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: 'org-1', userId: 'user-1' },
        body: { documentName: 'test' },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis(), setHeader: sinon.stub() } as any
      const next = sinon.stub()

      try {
        await service.uploadDocument(req, res, next)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should throw BadRequestError when filename contains forward slash', async () => {
      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'path/test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: 'org-1', userId: 'user-1' },
        body: { documentName: 'test' },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis(), setHeader: sinon.stub() } as any
      const next = sinon.stub()

      try {
        await service.uploadDocument(req, res, next)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should throw BadRequestError for file with dot but no valid extension', async () => {
      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'file.', size: 4, mimetype: 'text/plain' } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: 'org-1', userId: 'user-1' },
        body: { documentName: 'test' },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis(), setHeader: sinon.stub() } as any
      const next = sinon.stub()

      try {
        await service.uploadDocument(req, res, next)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // handleDocumentUpload
  // -------------------------------------------------------------------------
  describe('handleDocumentUpload', () => {
    it('should create document and upload for non-versioned file with S3', async () => {
      const savedDoc = {
        _id: 'doc-1',
        documentPath: '',
        versionHistory: [],
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      mockAdapter.uploadDocumentToStorageService.resolves({
        statusCode: 200,
        data: 'https://bucket.s3.us-east-1.amazonaws.com/org/PipesHub/doc-1/test.pdf',
      })

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.S3,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'test',
          isVersionedFile: false,
        },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis() } as any

      await service.handleDocumentUpload(req, res, () => ({
        buffer: Buffer.from('test'),
        mimeType: 'application/pdf',
        originalName: 'test.pdf',
        size: 4,
      }))

      expect(res.status.calledWith(200)).to.be.true
      expect(savedDoc.save.calledOnce).to.be.true
    })

    it('should create document and upload with version history for versioned file', async () => {
      const savedDoc = {
        _id: 'doc-1',
        documentPath: '',
        versionHistory: [],
        sizeInBytes: 4,
        extension: '.pdf',
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      mockAdapter.uploadDocumentToStorageService.resolves({
        statusCode: 200,
        data: 'https://bucket.s3.us-east-1.amazonaws.com/org/PipesHub/doc-1/current/test.pdf',
      })

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.S3,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'test',
          isVersionedFile: true,
        },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis() } as any

      await service.handleDocumentUpload(req, res, () => ({
        buffer: Buffer.from('test'),
        mimeType: 'application/pdf',
        originalName: 'test.pdf',
        size: 4,
      }))

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should handle local storage vendor with URL normalization', async () => {
      const savedDoc = {
        _id: 'doc-1',
        documentPath: '',
        versionHistory: [],
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      mockAdapter.uploadDocumentToStorageService.resolves({
        statusCode: 200,
        data: 'file:///path/to/file.pdf',
      })
      mockKeyValueStoreService.get.resolves(JSON.stringify({
        storage: { endpoint: 'http://localhost:3004' },
      }))

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'test',
          isVersionedFile: false,
        },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis() } as any

      await service.handleDocumentUpload(req, res, () => ({
        buffer: Buffer.from('test'),
        mimeType: 'application/pdf',
        originalName: 'test.pdf',
        size: 4,
      }))

      expect(res.status.calledWith(200)).to.be.true
    })

    it('should use documentPath when provided', async () => {
      const savedDoc = {
        _id: 'doc-1',
        documentPath: '',
        versionHistory: [],
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      mockAdapter.uploadDocumentToStorageService.resolves({
        statusCode: 200,
        data: 'https://bucket.s3.us-east-1.amazonaws.com/path',
      })

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.S3,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'test',
          documentPath: 'custom/path',
          isVersionedFile: false,
        },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis() } as any

      await service.handleDocumentUpload(req, res, () => ({
        buffer: Buffer.from('test'),
        mimeType: 'application/pdf',
        originalName: 'test.pdf',
        size: 4,
      }))

      expect(savedDoc.documentPath).to.include('custom/path')
    })
  })

  // -------------------------------------------------------------------------
  // cloneDocument (private)
  // -------------------------------------------------------------------------
  describe('cloneDocument (private)', () => {
    it('should clone document with correct payload', async () => {
      mockAdapter.uploadDocumentToStorageService.resolves({ statusCode: 200, data: 'url' })

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4 } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const doc = { extension: '.pdf', isVersionedFile: true } as any
      const result = await (service as any).cloneDocument(doc, Buffer.from('test'), 'new/path')
      expect(result.statusCode).to.equal(200)
    })

    it('should throw InternalServerError on upload failure', async () => {
      mockAdapter.uploadDocumentToStorageService.resolves({ statusCode: 500, msg: 'fail' })

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4 } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const doc = { extension: '.pdf', isVersionedFile: true } as any
      try {
        await (service as any).cloneDocument(doc, Buffer.from('test'), 'new/path')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.exist
      }
    })

    it('should throw BadRequestError for invalid extension', async () => {
      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4 } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const doc = { extension: '.xyz_invalid_format', isVersionedFile: true } as any
      try {
        await (service as any).cloneDocument(doc, Buffer.from('test'), 'new/path')
      } catch (error) {
        expect(error).to.exist
      }
    })

    it('should strip leading dot from extension before getting mime type', async () => {
      mockAdapter.uploadDocumentToStorageService.resolves({ statusCode: 200, data: 'url' })

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4 } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const doc = { extension: '.docx', isVersionedFile: false } as any
      const result = await (service as any).cloneDocument(doc, Buffer.from('test'), 'path')
      expect(result.statusCode).to.equal(200)
    })
  })

  // -------------------------------------------------------------------------
  // handleDocumentUpload - documentPath stored as fullDocumentPath
  // -------------------------------------------------------------------------
  describe('handleDocumentUpload - documentPath stored as fullDocumentPath', () => {
    it('should save documentPath as orgId/PipesHub/path without docId appended', async () => {
      const savedDoc = {
        _id: 'doc-1',
        documentPath: '',
        versionHistory: [],
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      mockAdapter.uploadDocumentToStorageService.resolves({
        statusCode: 200,
        data: 'https://bucket.s3.amazonaws.com/org/PipesHub/custom/path/doc-1/test.pdf',
      })

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.S3,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'test',
          documentPath: 'custom/path',
          isVersionedFile: false,
        },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis() } as any

      await service.handleDocumentUpload(req, res, () => ({
        buffer: Buffer.from('test'),
        mimeType: 'application/pdf',
        originalName: 'test.pdf',
        size: 4,
      }))

      expect(savedDoc.documentPath).to.equal('507f1f77bcf86cd799439011/PipesHub/custom/path')
      expect(savedDoc.documentPath).to.not.include('doc-1')
    })

    it('should save documentPath as orgId/PipesHub when no documentPath provided', async () => {
      const savedDoc = {
        _id: 'doc-1',
        documentPath: '',
        versionHistory: [],
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      mockAdapter.uploadDocumentToStorageService.resolves({
        statusCode: 200,
        data: 'https://bucket.s3.amazonaws.com/org/PipesHub/doc-1/test.pdf',
      })

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.S3,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'test',
          isVersionedFile: false,
        },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis() } as any

      await service.handleDocumentUpload(req, res, () => ({
        buffer: Buffer.from('test'),
        mimeType: 'application/pdf',
        originalName: 'test.pdf',
        size: 4,
      }))

      expect(savedDoc.documentPath).to.equal('507f1f77bcf86cd799439011/PipesHub')
    })
  })

  // -------------------------------------------------------------------------
  // handleDocumentUpload - versionLocalPath fix for Local vendor
  // -------------------------------------------------------------------------
  describe('handleDocumentUpload - versionLocalPath fix for Local vendor', () => {
    it('should use clone response path as localPath for version entry in Local storage', async () => {
      const savedDoc = {
        _id: 'doc-1',
        documentPath: '',
        versionHistory: [] as any[],
        sizeInBytes: 4,
        extension: '.pdf',
        isVersionedFile: true,
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)

      mockAdapter.uploadDocumentToStorageService
        .onFirstCall().resolves({
          statusCode: 200,
          data: 'file:///storage/current/test.pdf',
        })
        .onSecondCall().resolves({
          statusCode: 200,
          data: 'file:///storage/versions/v0/test.pdf',
        })

      mockKeyValueStoreService.get.resolves(JSON.stringify({
        storage: { endpoint: 'http://localhost:3004' },
      }))

      const service = new UploadDocumentService(
        mockAdapter,
        { buffer: Buffer.from('test'), originalname: 'test.pdf', size: 4, mimetype: 'application/pdf' } as any,
        StorageVendor.Local,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'test',
          isVersionedFile: true,
        },
      } as any
      const res = { json: sinon.stub(), status: sinon.stub().returnsThis() } as any

      await service.handleDocumentUpload(req, res, () => ({
        buffer: Buffer.from('test'),
        mimeType: 'application/pdf',
        originalName: 'test.pdf',
        size: 4,
      }))

      expect(savedDoc.versionHistory.length).to.equal(1)
      const versionEntry = savedDoc.versionHistory[0] as any
      expect(versionEntry[StorageVendor.Local].localPath).to.equal('file:///storage/versions/v0/test.pdf')
    })
  })

  // ---------------------------------------------------------------------------
  // uploadDocument - direct-upload path (large files)
  //
  // Review Fixes introduced a `strippedDocPath` step that strips the already-
  // stored `orgId/PipesHub/` prefix off the placeholder's documentPath before
  // rebuilding the upload path via the new helpers. Without the strip the
  // orgId/PipesHub segment would be duplicated in the final path.
  // ---------------------------------------------------------------------------
  describe('uploadDocument - direct upload for large files', () => {
    // 11 MB > 10 MB cutoff so the code routes through the presigned-URL path
    const LARGE_SIZE = 11 * 1024 * 1024

    it('should strip stored orgId/PipesHub prefix before generating presigned URL', async () => {
      // Placeholder comes back already carrying the persisted full path
      const placeholderDoc: any = {
        _id: 'doc-42',
        documentName: 'big',
        documentPath: '507f1f77bcf86cd799439011/PipesHub/Finance',
        isVersionedFile: false,
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(placeholderDoc)

      mockAdapter.generatePresignedUrlForDirectUpload.resolves({
        statusCode: 200,
        data: { url: 'https://bucket.s3.amazonaws.com/presigned?x=1' },
      })

      const service = new UploadDocumentService(
        mockAdapter,
        {
          buffer: Buffer.from('x'),
          originalname: 'big.pdf',
          size: LARGE_SIZE,
          mimetype: 'application/pdf',
        } as any,
        StorageVendor.S3,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'big',
          documentPath: 'Finance',
          extension: 'pdf',
          isVersionedFile: false,
        },
      } as any
      const res = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub(),
        setHeader: sinon.stub(),
      } as any
      const next = sinon.stub()

      await service.uploadDocument(req, res, next)

      // The underlying presigned-URL generator is given one argument — the final path
      const generatedPath =
        mockAdapter.generatePresignedUrlForDirectUpload.firstCall.args[0]

      // The orgId/PipesHub segment must appear exactly once (not duplicated)
      const matches = generatedPath.match(/507f1f77bcf86cd799439011\/PipesHub/g) || []
      expect(matches.length).to.equal(1)

      // For a non-versioned file the path is {org}/PipesHub/{subpath}/{docId}/{name}{ext}
      expect(generatedPath).to.equal(
        '507f1f77bcf86cd799439011/PipesHub/Finance/doc-42/big.pdf',
      )

      // And the placeholder is written back with the (un-stripped) full path
      expect(placeholderDoc.documentPath).to.equal(
        '507f1f77bcf86cd799439011/PipesHub/Finance',
      )
    })

    it('should include /current/ in direct-upload path for versioned files', async () => {
      const placeholderDoc: any = {
        _id: 'doc-99',
        documentName: 'report',
        documentPath: '507f1f77bcf86cd799439011/PipesHub',
        isVersionedFile: true,
        save: sinon.stub().resolves(),
      }
      sinon.stub(DocumentModel, 'create').resolves(placeholderDoc)

      mockAdapter.generatePresignedUrlForDirectUpload.resolves({
        statusCode: 200,
        data: { url: 'https://bucket.s3.amazonaws.com/presigned?x=1' },
      })

      const service = new UploadDocumentService(
        mockAdapter,
        {
          buffer: Buffer.from('x'),
          originalname: 'report.pdf',
          size: LARGE_SIZE,
          mimetype: 'application/pdf',
        } as any,
        StorageVendor.S3,
        mockKeyValueStoreService,
        mockDefaultConfig,
      )

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: {
          documentName: 'report',
          extension: 'pdf',
          isVersionedFile: 'true',
        },
      } as any
      const res = {
        status: sinon.stub().returnsThis(),
        json: sinon.stub(),
        setHeader: sinon.stub(),
      } as any
      const next = sinon.stub()

      await service.uploadDocument(req, res, next)

      const generatedPath =
        mockAdapter.generatePresignedUrlForDirectUpload.firstCall.args[0]

      expect(generatedPath).to.equal(
        '507f1f77bcf86cd799439011/PipesHub/doc-99/current/report.pdf',
      )
    })
  })
})
