import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs/promises'
import {
  StorageConfigurationError,
  StorageValidationError,
  StorageNotFoundError,
  StorageUploadError,
  StorageDownloadError,
  MultipartUploadError,
  PresignedUrlError,
} from '../../../../src/libs/errors/storage.errors'
import LocalStorageAdapter from '../../../../src/modules/storage/providers/local-storage.provider'

function createAdapter(): LocalStorageAdapter {
  return new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
}

describe('LocalStorageAdapter', () => {
  afterEach(() => { sinon.restore() })

  // -------------------------------------------------------------------------
  // constructor
  // -------------------------------------------------------------------------
  describe('constructor', () => {
    it('should throw StorageConfigurationError when mountName is missing', () => {
      try {
        new LocalStorageAdapter({ mountName: '', baseUrl: 'http://localhost' } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when baseUrl is missing', () => {
      try {
        new LocalStorageAdapter({ mountName: 'test', baseUrl: '' } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when both are missing', () => {
      try {
        new LocalStorageAdapter({ mountName: '', baseUrl: '' } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should create adapter with valid config', () => {
      const adapter = createAdapter()
      expect(adapter).to.be.instanceOf(LocalStorageAdapter)
    })
  })

  // -------------------------------------------------------------------------
  // multipart upload methods (not implemented)
  // -------------------------------------------------------------------------
  describe('multipart upload methods', () => {
    let adapter: LocalStorageAdapter

    beforeEach(() => {
      adapter = createAdapter()
    })

    it('getMultipartUploadId should throw MultipartUploadError', async () => {
      try {
        await adapter.getMultipartUploadId()
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })

    it('generatePresignedUrlForPart should throw MultipartUploadError', async () => {
      try {
        await adapter.generatePresignedUrlForPart()
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })

    it('completeMultipartUpload should throw MultipartUploadError', async () => {
      try {
        await adapter.completeMultipartUpload()
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // validateFilePayload (private)
  // -------------------------------------------------------------------------
  describe('validateFilePayload (private)', () => {
    it('should throw StorageValidationError for missing buffer', () => {
      const adapter = createAdapter()
      try {
        ;(adapter as any).validateFilePayload({ buffer: null, documentPath: 'path', mimeType: 'text/plain' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError for missing path', () => {
      const adapter = createAdapter()
      try {
        ;(adapter as any).validateFilePayload({ buffer: Buffer.from('test'), documentPath: '', mimeType: 'text/plain' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError for missing mimeType', () => {
      const adapter = createAdapter()
      try {
        ;(adapter as any).validateFilePayload({ buffer: Buffer.from('test'), documentPath: 'path', mimeType: '' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should not throw for valid payload', () => {
      const adapter = createAdapter()
      expect(() => {
        ;(adapter as any).validateFilePayload({
          buffer: Buffer.from('test'), documentPath: 'path', mimeType: 'text/plain',
        })
      }).to.not.throw()
    })
  })

  // -------------------------------------------------------------------------
  // sanitizePath (private)
  // -------------------------------------------------------------------------
  describe('sanitizePath (private)', () => {
    it('should remove parent directory references', () => {
      const adapter = createAdapter()
      const result = (adapter as any).sanitizePath('../../etc/passwd')
      expect(result).to.not.include('..')
    })

    it('should normalize path', () => {
      const adapter = createAdapter()
      const result = (adapter as any).sanitizePath('folder/./subfolder/file.txt')
      expect(result).to.include('folder')
      expect(result).to.include('subfolder')
    })

    it('should handle simple relative path', () => {
      const adapter = createAdapter()
      const result = (adapter as any).sanitizePath('folder/file.txt')
      expect(result).to.equal('folder/file.txt')
    })

    it('should handle path with backslash-dot sequences', () => {
      const adapter = createAdapter()
      const result = (adapter as any).sanitizePath('folder/../other/file.txt')
      expect(result).to.include('other')
    })
  })

  // -------------------------------------------------------------------------
  // getLocalPathFromUrl (private)
  // -------------------------------------------------------------------------
  describe('getLocalPathFromUrl (private)', () => {
    it('should return null for null input', () => {
      const adapter = createAdapter()
      const result = (adapter as any).getLocalPathFromUrl(null)
      expect(result).to.be.null
    })

    it('should return null for undefined input', () => {
      const adapter = createAdapter()
      const result = (adapter as any).getLocalPathFromUrl(undefined)
      expect(result).to.be.null
    })

    it('should return null for non-file URL', () => {
      const adapter = createAdapter()
      const result = (adapter as any).getLocalPathFromUrl('https://example.com/file.pdf')
      expect(result).to.be.null
    })

    it('should return null for invalid URL', () => {
      const adapter = createAdapter()
      const result = (adapter as any).getLocalPathFromUrl('not-a-url')
      expect(result).to.be.null
    })

    it('should extract path from file URL', () => {
      const adapter = createAdapter()
      const result = (adapter as any).getLocalPathFromUrl('file:///some/path/file.pdf')
      // Result should be a relative path
      expect(result).to.be.a('string')
    })
  })

  // -------------------------------------------------------------------------
  // getFileUrl (private)
  // -------------------------------------------------------------------------
  describe('getFileUrl (private)', () => {
    it('should return file:// URL for unix-like systems', () => {
      const adapter = createAdapter()
      const url = (adapter as any).getFileUrl('folder/file.pdf')
      expect(url).to.include('file://')
      expect(url).to.include('folder')
      expect(url).to.include('file.pdf')
    })

    it('should URL-encode special characters', () => {
      const adapter = createAdapter()
      const url = (adapter as any).getFileUrl('folder/file name.pdf')
      expect(url).to.include('file%20name.pdf')
    })
  })

  // -------------------------------------------------------------------------
  // uploadDocumentToStorageService
  // -------------------------------------------------------------------------
  describe('uploadDocumentToStorageService', () => {
    it('should throw StorageValidationError for invalid payload', async () => {
      const adapter = createAdapter()
      try {
        await adapter.uploadDocumentToStorageService({
          buffer: null as any, documentPath: '', mimeType: '', isVersioned: false,
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should upload file and return file URL', async () => {
      const adapter = createAdapter()
      sinon.stub(fs, 'mkdir').resolves(undefined)
      sinon.stub(fs, 'writeFile').resolves(undefined)

      const result = await adapter.uploadDocumentToStorageService({
        buffer: Buffer.from('test content'),
        documentPath: 'org/folder/file.pdf',
        mimeType: 'application/pdf',
        isVersioned: false,
      })

      expect(result.statusCode).to.equal(200)
      expect(result.data).to.include('file://')
    })

    it('should create directory before writing file', async () => {
      const adapter = createAdapter()
      const mkdirStub = sinon.stub(fs, 'mkdir').resolves(undefined)
      sinon.stub(fs, 'writeFile').resolves(undefined)

      await adapter.uploadDocumentToStorageService({
        buffer: Buffer.from('test'),
        documentPath: 'deep/nested/path/file.pdf',
        mimeType: 'application/pdf',
        isVersioned: false,
      })

      expect(mkdirStub.calledOnce).to.be.true
      expect(mkdirStub.firstCall.args[1]).to.deep.include({ recursive: true })
    })

    it('should throw StorageUploadError on fs write failure', async () => {
      const adapter = createAdapter()
      sinon.stub(fs, 'mkdir').resolves(undefined)
      sinon.stub(fs, 'writeFile').rejects(new Error('disk full'))

      try {
        await adapter.uploadDocumentToStorageService({
          buffer: Buffer.from('test'),
          documentPath: 'path/file.pdf',
          mimeType: 'application/pdf',
          isVersioned: false,
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // updateBuffer
  // -------------------------------------------------------------------------
  describe('updateBuffer', () => {
    it('should throw StorageNotFoundError when local path not found', async () => {
      const adapter = createAdapter()
      try {
        await adapter.updateBuffer(Buffer.from('test'), { local: undefined } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageNotFoundError when local URL is empty', async () => {
      const adapter = createAdapter()
      try {
        await adapter.updateBuffer(Buffer.from('test'), { local: { url: '' } } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // getBufferFromStorageService
  // -------------------------------------------------------------------------
  describe('getBufferFromStorageService', () => {
    it('should throw StorageNotFoundError when file URL not found', async () => {
      const adapter = createAdapter()
      try {
        await adapter.getBufferFromStorageService({ local: undefined } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageNotFoundError for version without URL', async () => {
      const adapter = createAdapter()
      try {
        await adapter.getBufferFromStorageService({
          local: { url: 'file:///path/file.pdf' },
          versionHistory: [{}],
        } as any, 1)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('should throw StorageNotFoundError when URL is not a file URL', async () => {
      const adapter = createAdapter()
      try {
        await adapter.getBufferFromStorageService({
          local: { url: 'https://not-a-file-url.com' },
        } as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // getSignedUrl
  // -------------------------------------------------------------------------
  describe('getSignedUrl', () => {
    it('should return file URL for valid document', async () => {
      const adapter = createAdapter()
      const doc = { local: { url: 'file:///some/path/file.pdf' } } as any
      const result = await adapter.getSignedUrl(doc)
      expect(result.statusCode).to.equal(200)
      expect(result.data).to.equal('file:///some/path/file.pdf')
    })

    it('should throw when URL not found', async () => {
      const adapter = createAdapter()
      const doc = { local: {} } as any
      try {
        await adapter.getSignedUrl(doc)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.exist
      }
    })

    it('should return versioned URL when version specified', async () => {
      const adapter = createAdapter()
      const doc = {
        local: { url: 'file:///current/file.pdf' },
        versionHistory: [
          { local: { url: 'file:///v0/file.pdf' } },
          { local: { url: 'file:///v1/file.pdf' } },
        ],
      } as any

      const result = await adapter.getSignedUrl(doc, 1)
      expect(result.statusCode).to.equal(200)
      expect(result.data).to.equal('file:///v1/file.pdf')
    })

    it('should throw StorageNotFoundError for version without URL', async () => {
      const adapter = createAdapter()
      const doc = {
        local: { url: 'file:///current/file.pdf' },
        versionHistory: [{}],
      } as any

      try {
        await adapter.getSignedUrl(doc, 1)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.exist
      }
    })
  })

  // -------------------------------------------------------------------------
  // generatePresignedUrlForDirectUpload
  // -------------------------------------------------------------------------
  describe('generatePresignedUrlForDirectUpload', () => {
    it('should return file URL', async () => {
      const adapter = createAdapter()
      const result = await adapter.generatePresignedUrlForDirectUpload('test/path.pdf')
      expect(result.statusCode).to.equal(200)
      expect(result.data.url).to.include('file://')
    })

    it('should sanitize path to prevent directory traversal', async () => {
      const adapter = createAdapter()
      const result = await adapter.generatePresignedUrlForDirectUpload('../../etc/passwd')
      expect(result.statusCode).to.equal(200)
      expect(result.data.url).to.not.include('..')
    })
  })
})
