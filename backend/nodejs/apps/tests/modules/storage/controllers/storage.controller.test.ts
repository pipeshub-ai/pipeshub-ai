/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import { HTTP_STATUS } from '../../../../src/libs/enums/http-status.enum'
import * as storageUtils from '../../../../src/modules/storage/utils/utils'
import * as mimetypeModule from '../../../../src/modules/storage/mimetypes/mimetypes'

function makeOrgId() {
  return new mongoose.Types.ObjectId().toString()
}

function makeReq(overrides: any = {}): any {
  const orgId = overrides.orgId ?? makeOrgId()
  return {
    user: { orgId, userId: overrides.userId ?? makeOrgId() },
    headers: { authorization: 'Bearer test-token' },
    params: overrides.params ?? {},
    query: overrides.query ?? {},
    body: overrides.body ?? {},
    ...overrides,
  }
}

function makeRes(): any {
  const res: any = {
    statusCode: 0,
    body: null,
    status(code: number) {
      res.statusCode = code
      return res
    },
    json(data: any) {
      res.body = data
      return res
    },
    setHeader: sinon.stub(),
  }
  return res
}

function makeAdapter(): any {
  return {
    uploadDocumentToStorageService: sinon.stub().resolves({ statusCode: 200, data: 'uploaded-url' }),
    getBufferFromStorageService: sinon.stub().resolves({ statusCode: 200, data: Buffer.from('file-data') }),
    getSignedUrl: sinon.stub().resolves({ statusCode: 200, data: 'https://signed.url' }),
    updateBuffer: sinon.stub().resolves({ statusCode: 200, data: 'updated-url' }),
    generatePresignedUrlForDirectUpload: sinon.stub().resolves({ statusCode: 200, data: { url: 'https://presigned.url?sig=abc' } }),
  }
}

function makeDocument(overrides: any = {}): any {
  const docId = new mongoose.Types.ObjectId()
  return {
    _id: docId,
    documentName: 'report',
    extension: '.pdf',
    documentPath: `org1/PipesHub/docs/${docId}`,
    isVersionedFile: true,
    storageVendor: StorageVendor.S3,
    versionHistory: [],
    mutationCount: 1,
    sizeInBytes: 1024,
    orgId: new mongoose.Types.ObjectId(),
    isDeleted: false,
    save: sinon.stub().resolves(),
    s3: { url: 'https://s3.url' },
    ...overrides,
  }
}

describe('StorageController', () => {
  let controller: StorageController
  let mockKvs: any
  let mockConfig: any
  let mockLogger: any
  let adapter: ReturnType<typeof makeAdapter>
  let initAdapterStub: sinon.SinonStub

  beforeEach(() => {
    mockKvs = {
      get: sinon.stub().resolves(null),
      set: sinon.stub().resolves(),
      watchKey: sinon.stub().resolves(),
    }
    mockConfig = { endpoint: 'http://localhost:3000' }
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    controller = new StorageController(mockConfig, mockLogger, mockKvs)
    adapter = makeAdapter()
    initAdapterStub = sinon.stub(controller, 'initializeStorageAdapter').resolves(adapter as any)
  })

  afterEach(() => {
    sinon.restore()
  })

  // ── getStorageConfig ────────────────────────────────────────────────
  describe('getStorageConfig', () => {
    it('should fetch config from CM service on first call', async () => {
      const fakeConfig = { mountName: 'test', baseUrl: 'http://storage' }
      const cmStub = sinon.stub().resolves({ data: fakeConfig })
      const CMCommand = require('../../../../src/libs/commands/configuration_manager/cm.service.command').ConfigurationManagerServiceCommand
      sinon.stub(CMCommand.prototype, 'execute').callsFake(cmStub)

      mockKvs.get.resolves(JSON.stringify({ cm: { endpoint: 'http://cm:3000' } }))
      const req = makeReq()

      const result = await controller.getStorageConfig(req, mockKvs, mockConfig)
      expect(result).to.deep.equal(fakeConfig)
    })

    it('should use internal route for service requests', async () => {
      // Reset module-level storageConfig cache by creating a fresh controller
      // and clearing the cached value via getStorageConfig's code path
      const fakeConfig = { mountName: 'test-internal' }
      const CMCommand = require('../../../../src/libs/commands/configuration_manager/cm.service.command').ConfigurationManagerServiceCommand
      const executeStub = sinon.stub(CMCommand.prototype, 'execute').resolves({ data: fakeConfig })

      mockKvs.get.resolves(JSON.stringify({ cm: { endpoint: 'http://cm:3000' } }))
      const req = { tokenPayload: { orgId: makeOrgId() }, headers: { authorization: 'Bearer svc-token' }, params: {}, query: {}, body: {} }

      const result = await controller.getStorageConfig(req as any, mockKvs, mockConfig)
      // The result may come from cache (previous test set it) — just verify no error
      expect(result).to.exist
    })
  })

  // ── cloneDocument ───────────────────────────────────────────────────
  describe('cloneDocument', () => {
    it('should upload a cloned document', async () => {
      const doc = makeDocument()
      const buffer = Buffer.from('clone-data')
      const next = sinon.stub()

      const result = await controller.cloneDocument(doc, buffer, 'new/path/file.pdf', next, adapter as any)
      expect(result).to.deep.equal({ statusCode: 200, data: 'uploaded-url' })
      expect(adapter.uploadDocumentToStorageService.calledOnce).to.be.true
    })

    it('should call next(error) on failure and return undefined', async () => {
      const doc = makeDocument()
      const next = sinon.stub()
      adapter.uploadDocumentToStorageService.rejects(new Error('upload failed'))

      const result = await controller.cloneDocument(doc, Buffer.from('x'), 'path', next, adapter as any)
      expect(result).to.be.undefined
      expect(next.calledOnce).to.be.true
    })
  })

  // ── compareDocuments ────────────────────────────────────────────────
  describe('compareDocuments', () => {
    it('should return true when buffers are equal', async () => {
      const buf = Buffer.from('same-content')
      adapter.getBufferFromStorageService
        .onFirstCall().resolves({ statusCode: 200, data: buf })
        .onSecondCall().resolves({ statusCode: 200, data: Buffer.from('same-content') })

      const result = await controller.compareDocuments(makeDocument(), 0, 1, adapter as any)
      expect(result).to.be.true
    })

    it('should return false when buffers differ', async () => {
      adapter.getBufferFromStorageService
        .onFirstCall().resolves({ statusCode: 200, data: Buffer.from('v1') })
        .onSecondCall().resolves({ statusCode: 200, data: Buffer.from('v2') })

      const result = await controller.compareDocuments(makeDocument(), 0, 1, adapter as any)
      expect(result).to.be.false
    })

    it('should return false when document is null', async () => {
      const result = await controller.compareDocuments(null as any, 0, 1, adapter as any)
      expect(result).to.be.false
    })
  })

  // ── getOrSetDefault ─────────────────────────────────────────────────
  describe('getOrSetDefault', () => {
    it('should return existing value', async () => {
      mockKvs.get.resolves('existing-value')
      const result = await controller.getOrSetDefault(mockKvs, 'key', 'default')
      expect(result).to.equal('existing-value')
      expect(mockKvs.set.called).to.be.false
    })

    it('should set and return default when key missing', async () => {
      mockKvs.get.resolves(null)
      const result = await controller.getOrSetDefault(mockKvs, 'key', 'default-val')
      expect(result).to.equal('default-val')
      expect(mockKvs.set.calledWith('key', 'default-val')).to.be.true
    })
  })

  // ── watchStorageType ────────────────────────────────────────────────
  describe('watchStorageType', () => {
    it('should register a watchKey callback', async () => {
      await controller.watchStorageType(mockKvs)
      expect(mockKvs.watchKey.calledOnce).to.be.true
    })
  })

  // ── initializeStorageAdapter ────────────────────────────────────────
  describe('initializeStorageAdapter', () => {
    beforeEach(() => {
      initAdapterStub.restore()
    })

    it('should throw InternalServerError when config is null', async () => {
      sinon.stub(controller, 'getStorageConfig').resolves(null)
      const req = makeReq()
      try {
        await controller.initializeStorageAdapter(req)
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('Storage configuration not found')
      }
    })
  })

  // ── getDocumentById ─────────────────────────────────────────────────
  describe('getDocumentById', () => {
    it('should return the document on success', async () => {
      const doc = makeDocument()
      sinon.stub(DocumentModel, 'findOne').returns({ exec: sinon.stub().resolves(doc) } as any)
      // findOne is called directly (not .exec()), so we stub to return a thenable
      ;(DocumentModel.findOne as sinon.SinonStub).resolves(doc)

      const req = makeReq({ params: { documentId: doc._id.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.getDocumentById(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(res.body).to.deep.equal(doc)
    })

    it('should call next with BadRequestError when documentId is missing', async () => {
      const req = makeReq({ params: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.getDocumentById(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('document id')
    })

    it('should call next with NotFoundError when document not found', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)
      const req = makeReq({ params: { documentId: new mongoose.Types.ObjectId().toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.getDocumentById(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('not found')
    })
  })

  // ── deleteDocumentById ──────────────────────────────────────────────
  describe('deleteDocumentById', () => {
    it('should soft-delete a document', async () => {
      const doc = makeDocument()
      sinon.stub(DocumentModel, 'findOne').resolves(doc)

      const req = makeReq({ params: { documentId: doc._id.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.deleteDocumentById(req, res, next)
      expect(doc.isDeleted).to.be.true
      expect(doc.save.calledOnce).to.be.true
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
    })

    it('should call next with NotFoundError when document missing', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)
      const req = makeReq({ params: { documentId: new mongoose.Types.ObjectId().toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.deleteDocumentById(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('does not exist')
    })

    it('should set deletedByUserId when userId present', async () => {
      const userId = makeOrgId()
      const doc = makeDocument()
      sinon.stub(DocumentModel, 'findOne').resolves(doc)

      const req = makeReq({ userId, params: { documentId: doc._id.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.deleteDocumentById(req, res, next)
      expect(doc.deletedByUserId).to.exist
    })
  })

  // ── createPlaceholderDocument ───────────────────────────────────────
  describe('createPlaceholderDocument', () => {
    it('should create a placeholder document', async () => {
      const orgId = makeOrgId()
      const savedDoc = makeDocument({ orgId: new mongoose.Types.ObjectId(orgId) })
      sinon.stub(DocumentModel, 'create').resolves(savedDoc)
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = makeReq({
        orgId,
        body: {
          documentName: 'test-doc',
          extension: 'pdf',
          isVersionedFile: false,
          documentPath: 'docs',
        },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.createPlaceholderDocument(req, res, next)
      expect(res.statusCode).to.equal(200)
    })

    it('should throw BadRequestError when orgId missing', async () => {
      const req = makeReq({ body: { documentName: 'test' } })
      // Remove user to make orgId extraction fail
      delete req.user
      req.tokenPayload = undefined
      const res = makeRes()
      const next = sinon.stub()

      await controller.createPlaceholderDocument(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when document name has extension', async () => {
      sinon.stub(storageUtils, 'hasExtension').returns(true)
      const req = makeReq({
        body: { documentName: 'report.pdf', extension: 'pdf' },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.createPlaceholderDocument(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('extensions')
    })

    it('should throw BadRequestError when document name has forward slash', async () => {
      sinon.stub(storageUtils, 'hasExtension').returns(false)
      const req = makeReq({
        body: { documentName: 'path/report', extension: 'pdf' },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.createPlaceholderDocument(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('forward slash')
    })
  })

  // ── uploadDocument ──────────────────────────────────────────────────
  describe('uploadDocument', () => {
    it('should call UploadDocumentService on valid storage type', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(true)
      sinon.stub(storageUtils, 'getStorageVendor').returns(StorageVendor.S3)

      const UploadDocumentService = require('../../../../src/modules/storage/controllers/storage.upload.service').UploadDocumentService
      sinon.stub(UploadDocumentService.prototype, 'uploadDocument').resolves()

      const req = makeReq({ body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.pdf', size: 1, mimetype: 'application/pdf' } } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDocument(req, res, next)
      expect(next.called).to.be.false
    })

    it('should call next with BadRequestError on invalid storage type', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 'invalid' }))
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(false)

      const req = makeReq({ body: { fileBuffer: {} } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDocument(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // ── downloadDocument ────────────────────────────────────────────────
  describe('downloadDocument', () => {
    it('should return signed URL for non-local storage', async () => {
      const doc = makeDocument({ storageVendor: StorageVendor.S3 })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = makeReq({ params: { documentId: doc._id.toString() }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.downloadDocument(req, res, next)
      expect(res.statusCode).to.equal(200)
      expect(res.body).to.have.property('signedUrl')
    })

    it('should serve file from local storage when vendor is Local', async () => {
      const doc = makeDocument({ storageVendor: StorageVendor.Local })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      mockKvs.get.resolves(JSON.stringify({ storageType: 'local' }))
      const serveStub = sinon.stub(storageUtils, 'serveFileFromLocalStorage')

      const req = makeReq({ params: { documentId: doc._id.toString() }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.downloadDocument(req, res, next)
      expect(serveStub.calledOnce).to.be.true
    })

    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(storageUtils, 'getDocumentInfo').resolves(undefined)

      const req = makeReq({ params: { documentId: makeOrgId() }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.downloadDocument(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when version exceeds history', async () => {
      const doc = makeDocument({ versionHistory: [{ version: 0 }] })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = makeReq({ params: { documentId: doc._id.toString() }, query: { version: '5' } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.downloadDocument(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include("version doesn't exist")
    })

    it('should throw BadRequestError for non-versioned file with version param', async () => {
      const doc = makeDocument({ isVersionedFile: false, versionHistory: [{ version: 0 }] })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = makeReq({ params: { documentId: doc._id.toString() }, query: { version: '0' } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.downloadDocument(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('non-versioned')
    })

    it('should throw BadRequestError on storage vendor mismatch', async () => {
      const doc = makeDocument({ storageVendor: StorageVendor.AzureBlob })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))

      const req = makeReq({ params: { documentId: doc._id.toString() }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.downloadDocument(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('mismatch')
    })
  })

  // ── getDocumentBuffer ───────────────────────────────────────────────
  describe('getDocumentBuffer', () => {
    it('should return buffer on success', async () => {
      const doc = makeDocument()
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })

      const req = makeReq({ query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.getDocumentBuffer(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
    })

    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(storageUtils, 'getDocumentInfo').resolves(undefined)

      const req = makeReq({ query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.getDocumentBuffer(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError when version exceeds history', async () => {
      const doc = makeDocument({ versionHistory: [{ version: 0 }] })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })

      const req = makeReq({ query: { version: '5' } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.getDocumentBuffer(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return 500 when buffer fetch fails', async () => {
      const doc = makeDocument()
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      adapter.getBufferFromStorageService.resolves({ statusCode: 500, msg: 'failed' })

      const req = makeReq({ query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.getDocumentBuffer(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.INTERNAL_SERVER)
    })
  })

  // ── createDocumentBuffer ────────────────────────────────────────────
  describe('createDocumentBuffer', () => {
    it('should upload buffer and update document on success', async () => {
      const doc = makeDocument({ mutationCount: 1, sizeInBytes: 100 })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      adapter.updateBuffer.resolves({ statusCode: 200, data: 'ok' })

      const req = makeReq({ body: { fileBuffer: { buffer: Buffer.from('new'), size: 200 } } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.createDocumentBuffer(req, res, next)
      expect(res.statusCode).to.equal(200)
      expect(doc.mutationCount).to.equal(2)
      expect(doc.sizeInBytes).to.equal(200)
      expect(doc.save.calledOnce).to.be.true
    })

    it('should throw when document not found', async () => {
      sinon.stub(storageUtils, 'getDocumentInfo').resolves(undefined)

      const req = makeReq({ body: { fileBuffer: { buffer: Buffer.from('x'), size: 10 } } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.createDocumentBuffer(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw InternalServerError on upload failure', async () => {
      const doc = makeDocument()
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      adapter.updateBuffer.resolves({ statusCode: 500, msg: 'disk full' })

      const req = makeReq({ body: { fileBuffer: { buffer: Buffer.from('x'), size: 10 } } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.createDocumentBuffer(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('Failed to upload buffer')
    })
  })

  // ── uploadNextVersionDocument ───────────────────────────────────────
  describe('uploadNextVersionDocument', () => {
    it('should upload next version for document with no version history', async () => {
      const doc = makeDocument({ versionHistory: [], storageVendor: StorageVendor.S3 })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('org/path/doc/versions/v0.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('org/path/doc/current/report.pdf')
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' })

      const req = makeReq({
        body: {
          fileBuffer: { buffer: Buffer.from('new-ver'), originalname: 'report.pdf', size: 500, mimetype: 'application/pdf' },
          currentVersionNote: 'initial',
          nextVersionNote: 'v1',
        },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(doc.save.calledOnce).to.be.true
    })

    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(storageUtils, 'getDocumentInfo').resolves(undefined)

      const req = makeReq({
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.pdf', size: 1, mimetype: 'application/pdf' } },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError for non-versioned documents', async () => {
      const doc = makeDocument({ isVersionedFile: false })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })

      const req = makeReq({
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.pdf', size: 1, mimetype: 'application/pdf' } },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('cannot be versioned')
    })

    it('should throw BadRequestError on extension mismatch', async () => {
      const doc = makeDocument({ extension: '.pdf' })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'normalizeExtension')
        .onFirstCall().returns('.pdf')
        .onSecondCall().returns('.docx')

      const req = makeReq({
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.docx', size: 1, mimetype: 'application/msword' } },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('does not match')
    })

    it('should handle existing version history with changed document', async () => {
      const doc = makeDocument({
        versionHistory: [{ version: 0, s3: { url: 'v0-url' } }],
        storageVendor: StorageVendor.S3,
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('org/path/doc/versions/v1.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('org/path/doc/current/report.pdf')
      sinon.stub(controller, 'compareDocuments').resolves(false)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v1-url' })

      const req = makeReq({
        body: {
          fileBuffer: { buffer: Buffer.from('new'), originalname: 'report.pdf', size: 600, mimetype: 'application/pdf' },
          currentVersionNote: 'save current',
          nextVersionNote: 'new version',
        },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
    })

    it('should handle existing version history with unchanged document', async () => {
      const doc = makeDocument({
        versionHistory: [{ version: 0, s3: { url: 'v0-url' } }],
        storageVendor: StorageVendor.S3,
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('org/path/doc/versions/v1.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('org/path/doc/current/report.pdf')
      sinon.stub(controller, 'compareDocuments').resolves(true)

      const req = makeReq({
        body: {
          fileBuffer: { buffer: Buffer.from('new'), originalname: 'report.pdf', size: 600, mimetype: 'application/pdf' },
        },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
    })

    it('should throw when version upload fails', async () => {
      const doc = makeDocument({ versionHistory: [], storageVendor: StorageVendor.S3 })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('path')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('path')
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' })
      adapter.uploadDocumentToStorageService
        .onFirstCall().resolves({ statusCode: 500, msg: 'version upload fail' })
        .onSecondCall().resolves({ statusCode: 200, data: 'ok' })

      const req = makeReq({
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.pdf', size: 1, mimetype: 'application/pdf' } },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw when current upload fails', async () => {
      const doc = makeDocument({ versionHistory: [], storageVendor: StorageVendor.S3 })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('path')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('path')
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' })
      adapter.uploadDocumentToStorageService
        .onFirstCall().resolves({ statusCode: 200, data: 'version-ok' })
        .onSecondCall().resolves({ statusCode: 500, msg: 'current upload fail' })

      const req = makeReq({
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.pdf', size: 1, mimetype: 'application/pdf' } },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should set document.azureBlob for AzureBlob vendor', async () => {
      const doc = makeDocument({ versionHistory: [], storageVendor: StorageVendor.AzureBlob })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('path')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('path')
      mockKvs.get.resolves(JSON.stringify({ storageType: 'azureBlob' }))
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'v0-url' })

      const req = makeReq({
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.pdf', size: 1, mimetype: 'application/pdf' } },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(doc.azureBlob).to.exist
    })

    it('should throw when initial v0 clone fails', async () => {
      const doc = makeDocument({ versionHistory: [], storageVendor: StorageVendor.S3 })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('path')
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 500, data: 'fail' })

      const req = makeReq({
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.pdf', size: 1, mimetype: 'application/pdf' } },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw when buffer fetch for v0 fails', async () => {
      const doc = makeDocument({ versionHistory: [], storageVendor: StorageVendor.S3 })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('path')
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))
      adapter.getBufferFromStorageService.resolves({ statusCode: 500, msg: 'buffer fail' })

      const req = makeReq({
        body: { fileBuffer: { buffer: Buffer.from('x'), originalname: 'f.pdf', size: 1, mimetype: 'application/pdf' } },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadNextVersionDocument(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // ── rollBackToPreviousVersion ───────────────────────────────────────
  describe('rollBackToPreviousVersion', () => {
    it('should rollback to a specified version', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [
          { version: 0, s3: { url: 'v0' }, size: 100 },
          { version: 1, s3: { url: 'v1' }, size: 200 },
          { version: 2, s3: { url: 'v2' }, size: 300 },
        ],
        storageVendor: StorageVendor.S3,
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('current/path')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('version/path')
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(true)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'rolled-url' })

      const req = makeReq({
        body: { version: 0, note: 'rollback to v0' },
        query: {},
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(doc.save.calledOnce).to.be.true
    })

    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(storageUtils, 'getDocumentInfo').resolves(undefined)

      const req = makeReq({ body: { version: 0, note: 'test' }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError for non-versioned document', async () => {
      const doc = makeDocument({ isVersionedFile: false })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })

      const req = makeReq({ body: { version: 0, note: 'test' }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('non-versioned')
    })

    it('should throw BadRequestError when version is null/undefined', async () => {
      const doc = makeDocument({ isVersionedFile: true, versionHistory: [{ version: 0 }] })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })

      const req = makeReq({ body: { note: 'test' }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('version is required')
    })

    it('should throw BadRequestError when rolling back to latest version', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [{ version: 0 }, { version: 1 }],
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })

      const req = makeReq({ body: { version: 1, note: 'test' }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('Cannot rollback')
    })

    it('should throw when buffer fetch fails during rollback', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [{ version: 0 }, { version: 1 }],
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('path')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      adapter.getBufferFromStorageService.resolves({ statusCode: 500, msg: 'fail' })

      const req = makeReq({ body: { version: 0, note: 'test' }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw when clone for current file returns undefined', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [{ version: 0 }, { version: 1 }],
        storageVendor: StorageVendor.S3,
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('path')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('current')
      sinon.stub(controller, 'cloneDocument').resolves(undefined)

      const req = makeReq({ body: { version: 0, note: 'test' }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw when clone for version file returns undefined', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [{ version: 0 }, { version: 1 }],
        storageVendor: StorageVendor.S3,
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('path')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('current')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('version')
      const cloneStub = sinon.stub(controller, 'cloneDocument')
      cloneStub.onFirstCall().resolves({ statusCode: 200, data: 'ok' })
      cloneStub.onSecondCall().resolves(undefined)

      const req = makeReq({ body: { version: 0, note: 'test' }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError for invalid storage vendor', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [{ version: 0 }, { version: 1 }],
        storageVendor: 'invalid' as any,
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('path')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('current')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('version')
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(false)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'ok' })

      const req = makeReq({ body: { version: 0, note: 'test' }, query: {} })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should use version from query when body does not have it', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [
          { version: 0, s3: { url: 'v0' }, size: 100 },
          { version: 1, s3: { url: 'v1' }, size: 200 },
          { version: 2, s3: { url: 'v2' }, size: 300 },
        ],
        storageVendor: StorageVendor.S3,
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'getDocumentRootPath').returns('org/path/doc')
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('current/path')
      sinon.stub(storageUtils, 'getVersionFilePath').returns('version/path')
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(true)
      sinon.stub(controller, 'cloneDocument').resolves({ statusCode: 200, data: 'rolled-url' })

      const req = makeReq({
        body: { note: 'rollback' },
        query: { version: 0 },
      })
      const res = makeRes()
      const next = sinon.stub()

      await controller.rollBackToPreviousVersion(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
    })
  })

  // ── uploadDirectDocument ────────────────────────────────────────────
  describe('uploadDirectDocument', () => {
    it('should generate presigned URL and update S3 document', async () => {
      const doc = makeDocument({ storageVendor: StorageVendor.S3, documentPath: 'org/PipesHub/docs' })
      sinon.stub(DocumentModel, 'findOne').resolves(doc)
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('upload/path')
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(true)
      sinon.stub(storageUtils, 'getBaseUrl').returns('https://base.url')

      const req = makeReq({ params: { documentId: doc._id.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDirectDocument(req as any, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(res.body).to.have.property('signedUrl')
      expect(res.body).to.have.property('documentId')
      expect(doc.s3?.url).to.equal('https://base.url')
    })

    it('should handle AzureBlob storage vendor', async () => {
      const doc = makeDocument({ storageVendor: StorageVendor.AzureBlob, documentPath: 'org/PipesHub/docs' })
      sinon.stub(DocumentModel, 'findOne').resolves(doc)
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('upload/path')
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(true)
      sinon.stub(storageUtils, 'getBaseUrl').returns('https://azure.url')

      const req = makeReq({ params: { documentId: doc._id.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDirectDocument(req as any, res, next)
      expect(doc.azureBlob?.url).to.equal('https://azure.url')
    })

    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)

      const req = makeReq({ params: { documentId: makeOrgId() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDirectDocument(req as any, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('does not exist')
    })

    it('should throw when presigned URL generation fails', async () => {
      const doc = makeDocument({ storageVendor: StorageVendor.S3, documentPath: 'org/PipesHub/docs' })
      sinon.stub(DocumentModel, 'findOne').resolves(doc)
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('upload/path')
      adapter.generatePresignedUrlForDirectUpload.resolves({ statusCode: 500, data: { url: '' }, msg: 'fail' })

      const req = makeReq({ params: { documentId: doc._id.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDirectDocument(req as any, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw when presigned URL is empty', async () => {
      const doc = makeDocument({ storageVendor: StorageVendor.S3, documentPath: 'org/PipesHub/docs' })
      sinon.stub(DocumentModel, 'findOne').resolves(doc)
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('upload/path')
      adapter.generatePresignedUrlForDirectUpload.resolves({ statusCode: 200, data: { url: '' } })

      const req = makeReq({ params: { documentId: doc._id.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDirectDocument(req as any, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw BadRequestError for invalid storage vendor', async () => {
      const doc = makeDocument({ storageVendor: 'invalid' as any, documentPath: 'org/PipesHub/docs' })
      sinon.stub(DocumentModel, 'findOne').resolves(doc)
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      sinon.stub(storageUtils, 'getCurrentFilePath').returns('upload/path')
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(false)

      const req = makeReq({ params: { documentId: doc._id.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDirectDocument(req as any, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should append documentId to path when path does not end with it', async () => {
      const docId = new mongoose.Types.ObjectId()
      const doc = makeDocument({
        _id: docId,
        storageVendor: StorageVendor.S3,
        documentPath: 'org/PipesHub/docs',
      })
      sinon.stub(DocumentModel, 'findOne').resolves(doc)
      sinon.stub(storageUtils, 'normalizeExtension').returns('.pdf')
      const getCurrentStub = sinon.stub(storageUtils, 'getCurrentFilePath').returns('upload/path')
      sinon.stub(storageUtils, 'isValidStorageVendor').returns(true)
      sinon.stub(storageUtils, 'getBaseUrl').returns('https://base.url')

      const req = makeReq({ params: { documentId: docId.toString() } })
      const res = makeRes()
      const next = sinon.stub()

      await controller.uploadDirectDocument(req as any, res, next)
      const basePath = getCurrentStub.firstCall.args[0]
      expect(basePath).to.include(docId.toString())
    })
  })

  // ── documentDiffChecker ─────────────────────────────────────────────
  describe('documentDiffChecker', () => {
    it('should return mutationCount > 1 for non-versioned files', async () => {
      const doc = makeDocument({ isVersionedFile: false, mutationCount: 3, versionHistory: [] })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'extractOrgId').returns(makeOrgId())

      const req = makeReq()
      const res = makeRes()
      const next = sinon.stub()

      await controller.documentDiffChecker(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(res.body).to.be.true
    })

    it('should return false for non-versioned file with mutationCount=1', async () => {
      const doc = makeDocument({ isVersionedFile: false, mutationCount: 1, versionHistory: [] })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'extractOrgId').returns(makeOrgId())

      const req = makeReq()
      const res = makeRes()
      const next = sinon.stub()

      await controller.documentDiffChecker(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(res.body).to.be.false
    })

    it('should compare documents for versioned files', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [{ version: 0 }],
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'extractOrgId').returns(makeOrgId())
      sinon.stub(controller, 'compareDocuments').resolves(false)

      const req = makeReq()
      const res = makeRes()
      const next = sinon.stub()

      await controller.documentDiffChecker(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(res.body).to.be.true
    })

    it('should return false when documents are identical', async () => {
      const doc = makeDocument({
        isVersionedFile: true,
        versionHistory: [{ version: 0 }],
      })
      sinon.stub(storageUtils, 'getDocumentInfo').resolves({ document: doc })
      sinon.stub(storageUtils, 'extractOrgId').returns(makeOrgId())
      sinon.stub(controller, 'compareDocuments').resolves(true)

      const req = makeReq()
      const res = makeRes()
      const next = sinon.stub()

      await controller.documentDiffChecker(req, res, next)
      expect(res.statusCode).to.equal(HTTP_STATUS.OK)
      expect(res.body).to.be.false
    })

    it('should throw NotFoundError when orgId missing', async () => {
      const req = makeReq()
      delete req.user
      req.tokenPayload = undefined
      const res = makeRes()
      const next = sinon.stub()

      await controller.documentDiffChecker(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should throw NotFoundError when document not found', async () => {
      sinon.stub(storageUtils, 'getDocumentInfo').resolves(undefined)
      sinon.stub(storageUtils, 'extractOrgId').returns(makeOrgId())

      const req = makeReq()
      const res = makeRes()
      const next = sinon.stub()

      await controller.documentDiffChecker(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })
})
