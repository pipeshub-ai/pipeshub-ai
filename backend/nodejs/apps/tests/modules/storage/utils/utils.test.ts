import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  parseBoolean,
  isValidStorageVendor,
  getExtension,
  hasExtension,
  getBaseUrl,
  getStorageVendor,
  encodeRFC5987,
  extractOrgId,
  extractUserId,
  validateFileAndDocumentName,
  getDocumentInfo,
  generatePresignedUrlForDirectUpload,
  createPlaceholderDocument,
  serveFileFromLocalStorage,
} from '../../../../src/modules/storage/utils/utils'
import { StorageVendor } from '../../../../src/modules/storage/types/storage.service.types'
import { BadRequestError, NotFoundError, InternalServerError } from '../../../../src/libs/errors/http.errors'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import mongoose from 'mongoose'

describe('storage/utils/utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -------------------------------------------------------------------------
  // parseBoolean
  // -------------------------------------------------------------------------
  describe('parseBoolean', () => {
    it('should return true for boolean true', () => {
      expect(parseBoolean(true)).to.be.true
    })

    it('should return false for boolean false', () => {
      expect(parseBoolean(false)).to.be.false
    })

    it('should return true for string "true"', () => {
      expect(parseBoolean('true')).to.be.true
    })

    it('should return true for string "TRUE"', () => {
      expect(parseBoolean('TRUE')).to.be.true
    })

    it('should return true for string "True"', () => {
      expect(parseBoolean('True')).to.be.true
    })

    it('should return false for string "false"', () => {
      expect(parseBoolean('false')).to.be.false
    })

    it('should return false for undefined', () => {
      expect(parseBoolean(undefined)).to.be.false
    })

    it('should return false for null', () => {
      expect(parseBoolean(null)).to.be.false
    })

    it('should return false for random string', () => {
      expect(parseBoolean('yes')).to.be.false
    })

    it('should return false for empty string', () => {
      expect(parseBoolean('')).to.be.false
    })
  })

  // -------------------------------------------------------------------------
  // isValidStorageVendor
  // -------------------------------------------------------------------------
  describe('isValidStorageVendor', () => {
    it('should return true for s3', () => {
      expect(isValidStorageVendor('s3')).to.be.true
    })

    it('should return true for azureBlob', () => {
      expect(isValidStorageVendor('azureBlob')).to.be.true
    })

    it('should return true for local', () => {
      expect(isValidStorageVendor('local')).to.be.true
    })

    it('should return false for unknown vendor', () => {
      expect(isValidStorageVendor('gcs')).to.be.false
    })

    it('should return false for empty string', () => {
      expect(isValidStorageVendor('')).to.be.false
    })

    it('should return false for capitalized vendor', () => {
      expect(isValidStorageVendor('S3')).to.be.false
    })
  })

  // -------------------------------------------------------------------------
  // getExtension
  // -------------------------------------------------------------------------
  describe('getExtension', () => {
    it('should return file extension', () => {
      expect(getExtension('file.pdf')).to.equal('pdf')
    })

    it('should return last extension for multiple dots', () => {
      expect(getExtension('file.name.pdf')).to.equal('pdf')
    })

    it('should return empty string for no extension', () => {
      expect(getExtension('file')).to.equal('')
    })

    it('should return empty string for empty input', () => {
      expect(getExtension('')).to.equal('')
    })

    it('should return empty string for undefined/null', () => {
      expect(getExtension(undefined as any)).to.equal('')
      expect(getExtension(null as any)).to.equal('')
    })

    it('should return empty string for file ending with dot', () => {
      expect(getExtension('file.')).to.equal('')
    })

    it('should handle long extensions', () => {
      expect(getExtension('document.docx')).to.equal('docx')
    })
  })

  // -------------------------------------------------------------------------
  // hasExtension
  // -------------------------------------------------------------------------
  describe('hasExtension', () => {
    it('should return false for undefined', () => {
      expect(hasExtension(undefined)).to.be.false
    })

    it('should return true for name with valid extension', () => {
      expect(hasExtension('file.pdf')).to.be.true
    })

    it('should return false for name without extension', () => {
      expect(hasExtension('filename')).to.be.false
    })

    it('should return false for name with unknown extension', () => {
      expect(hasExtension('file.xyz123unknown')).to.be.false
    })
  })

  // -------------------------------------------------------------------------
  // getBaseUrl
  // -------------------------------------------------------------------------
  describe('getBaseUrl', () => {
    it('should return URL without query string', () => {
      expect(getBaseUrl('https://example.com/file?token=abc')).to.equal('https://example.com/file')
    })

    it('should return full URL if no query string', () => {
      expect(getBaseUrl('https://example.com/file')).to.equal('https://example.com/file')
    })

    it('should handle URL with multiple query params', () => {
      expect(getBaseUrl('https://example.com/file?a=1&b=2')).to.equal('https://example.com/file')
    })

    it('should return empty string for empty input', () => {
      expect(getBaseUrl('')).to.equal('')
    })
  })

  // -------------------------------------------------------------------------
  // getStorageVendor
  // -------------------------------------------------------------------------
  describe('getStorageVendor', () => {
    it('should return S3 for "s3"', () => {
      expect(getStorageVendor('s3')).to.equal(StorageVendor.S3)
    })

    it('should return AzureBlob for "azureBlob"', () => {
      expect(getStorageVendor('azureBlob')).to.equal(StorageVendor.AzureBlob)
    })

    it('should return Local for "local"', () => {
      expect(getStorageVendor('local')).to.equal(StorageVendor.Local)
    })

    it('should throw for invalid storage type', () => {
      expect(() => getStorageVendor('unknown')).to.throw('Invalid storage type')
    })

    it('should throw for empty string', () => {
      expect(() => getStorageVendor('')).to.throw('Invalid storage type')
    })
  })

  // -------------------------------------------------------------------------
  // encodeRFC5987
  // -------------------------------------------------------------------------
  describe('encodeRFC5987', () => {
    it('should encode special characters', () => {
      const result = encodeRFC5987("file'name.pdf")
      expect(result).to.include('%27')
    })

    it('should encode parentheses', () => {
      const result = encodeRFC5987('file(1).pdf')
      expect(result).to.include('%28')
      expect(result).to.include('%29')
    })

    it('should encode asterisks', () => {
      const result = encodeRFC5987('file*.pdf')
      expect(result).to.include('%2A')
    })

    it('should handle simple filename', () => {
      const result = encodeRFC5987('simple.pdf')
      expect(result).to.equal('simple.pdf')
    })

    it('should encode spaces', () => {
      const result = encodeRFC5987('file name.pdf')
      expect(result).to.include('file%20name.pdf')
    })

    it('should encode unicode characters', () => {
      const result = encodeRFC5987('dokument-ä.pdf')
      expect(result).to.include('%C3%A4')
    })
  })

  // -------------------------------------------------------------------------
  // extractOrgId
  // -------------------------------------------------------------------------
  describe('extractOrgId', () => {
    it('should extract orgId from user request', () => {
      const req: any = { user: { orgId: 'org-123' } }
      expect(extractOrgId(req)).to.equal('org-123')
    })

    it('should extract orgId from service request', () => {
      const req: any = { tokenPayload: { orgId: 'org-456' } }
      expect(extractOrgId(req)).to.equal('org-456')
    })

    it('should throw BadRequestError when orgId missing', () => {
      const req: any = {}
      expect(() => extractOrgId(req)).to.throw(BadRequestError)
    })

    it('should prefer user orgId over tokenPayload', () => {
      const req: any = { user: { orgId: 'org-user' }, tokenPayload: { orgId: 'org-token' } }
      expect(extractOrgId(req)).to.equal('org-user')
    })
  })

  // -------------------------------------------------------------------------
  // extractUserId
  // -------------------------------------------------------------------------
  describe('extractUserId', () => {
    it('should extract userId from user request', () => {
      const req: any = { user: { userId: 'user-123' } }
      expect(extractUserId(req)).to.equal('user-123')
    })

    it('should return null for service request without user', () => {
      const req: any = { tokenPayload: { orgId: 'org-1' } }
      expect(extractUserId(req)).to.be.null
    })

    it('should return null for empty request', () => {
      const req: any = {}
      expect(extractUserId(req)).to.be.null
    })
  })

  // -------------------------------------------------------------------------
  // validateFileAndDocumentName
  // -------------------------------------------------------------------------
  describe('validateFileAndDocumentName', () => {
    it('should not throw for valid extension and name', () => {
      expect(() => validateFileAndDocumentName('pdf', 'myDocument', 'file.pdf')).to.not.throw()
    })

    it('should throw for unsupported extension', () => {
      expect(() => validateFileAndDocumentName('xyz123', 'myDoc', 'file.xyz123')).to.throw(BadRequestError)
    })

    it('should throw when document name contains forward slash', () => {
      expect(() => validateFileAndDocumentName('pdf', 'my/doc', 'file.pdf')).to.throw(BadRequestError)
    })

    it('should throw when document name contains a file extension', () => {
      expect(() => validateFileAndDocumentName('pdf', 'myDoc.pdf', 'file.pdf')).to.throw(BadRequestError)
    })

    it('should not throw for undefined document name', () => {
      expect(() => validateFileAndDocumentName('pdf', undefined, 'file.pdf')).to.not.throw()
    })
  })

  // -------------------------------------------------------------------------
  // getDocumentInfo
  // -------------------------------------------------------------------------
  describe('getDocumentInfo', () => {
    it('should call next on error when documentId is missing', async () => {
      const next = sinon.stub()
      const req = {
        params: {},
        user: { orgId: '507f1f77bcf86cd799439011' },
      } as any

      try {
        await getDocumentInfo(req, next)
      } catch {
        // expected
      }
      expect(next.calledOnce).to.be.true
    })

    it('should return document when found', async () => {
      const mockDoc = { _id: 'doc-1', documentName: 'test' }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)
      const next = sinon.stub()
      const req = {
        params: { documentId: '507f1f77bcf86cd799439011' },
        user: { orgId: '507f1f77bcf86cd799439012' },
      } as any

      const result = await getDocumentInfo(req, next)
      expect(result).to.exist
      expect(result!.document).to.deep.equal(mockDoc)
    })

    it('should call next when document not found', async () => {
      sinon.stub(DocumentModel, 'findOne').resolves(null)
      const next = sinon.stub()
      const req = {
        params: { documentId: '507f1f77bcf86cd799439011' },
        user: { orgId: '507f1f77bcf86cd799439012' },
      } as any

      try {
        await getDocumentInfo(req, next)
      } catch {
        // expected
      }
      expect(next.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // generatePresignedUrlForDirectUpload
  // -------------------------------------------------------------------------
  describe('generatePresignedUrlForDirectUpload', () => {
    it('should throw BadRequestError when documentPath is undefined', async () => {
      const mockAdapter = { generatePresignedUrlForDirectUpload: sinon.stub() }
      try {
        await generatePresignedUrlForDirectUpload(mockAdapter as any, undefined)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should return URL on success', async () => {
      const mockAdapter = {
        generatePresignedUrlForDirectUpload: sinon.stub().resolves({
          statusCode: 200,
          data: { url: 'https://presigned.url' },
        }),
      }
      const result = await generatePresignedUrlForDirectUpload(mockAdapter as any, 'path/file.pdf')
      expect(result).to.equal('https://presigned.url')
    })

    it('should throw InternalServerError when response status is not 200', async () => {
      const mockAdapter = {
        generatePresignedUrlForDirectUpload: sinon.stub().resolves({
          statusCode: 500,
          data: { url: null },
          msg: 'error',
        }),
      }
      try {
        await generatePresignedUrlForDirectUpload(mockAdapter as any, 'path/file.pdf')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // serveFileFromLocalStorage
  // -------------------------------------------------------------------------
  describe('serveFileFromLocalStorage', () => {
    it('should throw NotFoundError when local path is missing', () => {
      const doc = { local: {} } as any
      const res = {} as any

      try {
        serveFileFromLocalStorage(doc, res)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw NotFoundError when local is undefined', () => {
      const doc = { local: undefined } as any
      const res = {} as any

      try {
        serveFileFromLocalStorage(doc, res)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // createPlaceholderDocument
  // -------------------------------------------------------------------------
  describe('createPlaceholderDocument', () => {
    it('should create document and return it', async () => {
      const savedDoc = { _id: 'doc-1', documentName: 'test' }
      const createStub = sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      const next = sinon.stub()

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: { documentName: 'test' },
      } as any

      const result = await createPlaceholderDocument(req, next, 1000, 'pdf', 'test.pdf')
      expect(result).to.exist
      expect(result!.document).to.deep.equal(savedDoc)
      expect(createStub.calledOnce).to.be.true
      expect(createStub.firstCall.args[0].documentPath).to.equal('507f1f77bcf86cd799439011/PipesHub')
    })

    it('should prefix org and PipesHub when documentPath is provided', async () => {
      const savedDoc = { _id: 'doc-2', documentName: 'test' }
      const createStub = sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      const next = sinon.stub()

      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: { documentName: 'test', documentPath: 'folderA' },
      } as any

      const result = await createPlaceholderDocument(req, next, 1000, 'pdf', 'test.pdf')
      expect(result).to.exist
      expect(result!.document).to.deep.equal(savedDoc)
      expect(createStub.calledOnce).to.be.true
      expect(createStub.firstCall.args[0].documentPath).to.equal('507f1f77bcf86cd799439011/PipesHub/folderA')
    })

    it('should call next on validation error', async () => {
      const next = sinon.stub()
      const req = {
        user: { orgId: '507f1f77bcf86cd799439011', userId: '507f1f77bcf86cd799439012' },
        body: { documentName: 'test' },
      } as any

      try {
        await createPlaceholderDocument(req, next, 1000, 'xyz_invalid_ext', 'test.xyz')
      } catch {
        // expected
      }
      expect(next.calledOnce).to.be.true
    })
  })
})
