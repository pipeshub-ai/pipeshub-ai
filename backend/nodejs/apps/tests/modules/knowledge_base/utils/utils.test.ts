import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  FileUploadMetadata,
  PlaceholderResult,
  PlaceholderResultWithMetadata,
  StorageResponseMetadata,
  ProcessedFile,
} from '../../../../src/modules/knowledge_base/utils/utils'

describe('Knowledge Base Utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('Type definitions', () => {
    it('should have correct shape for StorageResponseMetadata', () => {
      const metadata: StorageResponseMetadata = {
        documentId: 'doc-1',
        documentName: 'test.pdf',
      }
      expect(metadata.documentId).to.equal('doc-1')
      expect(metadata.documentName).to.equal('test.pdf')
    })

    it('should have correct shape for PlaceholderResult', () => {
      const result: PlaceholderResult = {
        documentId: 'doc-1',
        documentName: 'test.pdf',
        redirectUrl: 'http://storage/upload',
      }
      expect(result.documentId).to.equal('doc-1')
      expect(result.documentName).to.equal('test.pdf')
      expect(result.redirectUrl).to.equal('http://storage/upload')
    })

    it('should allow optional fields in PlaceholderResult', () => {
      const result: PlaceholderResult = {
        documentId: 'doc-1',
        documentName: 'test.pdf',
      }
      expect(result.uploadPromise).to.be.undefined
      expect(result.redirectUrl).to.be.undefined
    })

    it('should have correct shape for FileUploadMetadata', () => {
      const metadata: FileUploadMetadata = {
        file: {
          buffer: Buffer.from('test'),
          originalname: 'test.pdf',
          mimetype: 'application/pdf',
          size: 100,
          fieldname: 'file',
        } as any,
        filePath: '/uploads/test.pdf',
        fileName: 'test.pdf',
        extension: '.pdf',
        correctMimeType: 'application/pdf',
        key: 'doc-1',
        webUrl: 'http://storage/doc-1',
        validLastModified: Date.now(),
        size: 100,
      }
      expect(metadata.fileName).to.equal('test.pdf')
      expect(metadata.extension).to.equal('.pdf')
      expect(metadata.correctMimeType).to.equal('application/pdf')
    })

    it('should have correct shape for PlaceholderResultWithMetadata', () => {
      const combined: PlaceholderResultWithMetadata = {
        placeholderResult: {
          documentId: 'doc-1',
          documentName: 'test.pdf',
        },
        metadata: {
          file: {} as any,
          filePath: '/uploads/test.pdf',
          fileName: 'test.pdf',
          extension: '.pdf',
          correctMimeType: 'application/pdf',
          key: 'doc-1',
          webUrl: 'http://storage/doc-1',
          validLastModified: Date.now(),
          size: 100,
        },
      }
      expect(combined.placeholderResult.documentId).to.equal('doc-1')
      expect(combined.metadata.fileName).to.equal('test.pdf')
    })

    it('should have correct shape for ProcessedFile', () => {
      const processed: ProcessedFile = {
        record: {
          _key: 'r-1',
          orgId: 'org-1',
          recordName: 'test.pdf',
          externalRecordId: 'ext-1',
          recordType: 'FILE',
          origin: 'UPLOAD',
          connectorId: 'knowledgeBase_org-1',
          createdAtTimestamp: Date.now(),
          updatedAtTimestamp: Date.now(),
          sourceCreatedAtTimestamp: Date.now(),
          sourceLastModifiedTimestamp: Date.now(),
          isDeleted: false,
          isArchived: false,
          indexingStatus: 'QUEUED',
          version: 1,
          webUrl: '/record/r-1',
          mimeType: 'application/pdf',
        } as any,
        fileRecord: {
          _key: 'r-1',
          orgId: 'org-1',
          name: 'test.pdf',
          isFile: true,
          extension: '.pdf',
          mimeType: 'application/pdf',
          sizeInBytes: 100,
          webUrl: '/record/r-1',
        } as any,
        filePath: '/uploads/test.pdf',
        lastModified: Date.now(),
      }
      expect(processed.record._key).to.equal('r-1')
      expect(processed.fileRecord.name).to.equal('test.pdf')
      expect(processed.filePath).to.equal('/uploads/test.pdf')
    })

    it('should allow PlaceholderResult with uploadPromise', () => {
      const result: PlaceholderResult = {
        documentId: 'doc-1',
        documentName: 'test.pdf',
        uploadPromise: Promise.resolve(),
        redirectUrl: 'http://storage.example.com/upload?sig=abc',
      }
      expect(result.uploadPromise).to.be.instanceOf(Promise)
      expect(result.redirectUrl).to.include('storage.example.com')
    })

    it('should handle null extension in FileUploadMetadata', () => {
      const metadata: FileUploadMetadata = {
        file: {} as any,
        filePath: '/uploads/README',
        fileName: 'README',
        extension: null,
        correctMimeType: 'text/plain',
        key: 'doc-2',
        webUrl: '/record/doc-2',
        validLastModified: Date.now(),
        size: 50,
      }
      expect(metadata.extension).to.be.null
      expect(metadata.fileName).to.equal('README')
    })

    it('should handle zero-size file in FileUploadMetadata', () => {
      const metadata: FileUploadMetadata = {
        file: {} as any,
        filePath: '/uploads/empty.txt',
        fileName: 'empty.txt',
        extension: 'txt',
        correctMimeType: 'text/plain',
        key: 'doc-3',
        webUrl: '/record/doc-3',
        validLastModified: Date.now(),
        size: 0,
      }
      expect(metadata.size).to.equal(0)
    })

    it('should handle multiple PlaceholderResultWithMetadata', () => {
      const results: PlaceholderResultWithMetadata[] = [
        {
          placeholderResult: { documentId: 'doc-1', documentName: 'a.pdf' },
          metadata: {
            file: {} as any,
            filePath: '/a.pdf',
            fileName: 'a.pdf',
            extension: '.pdf',
            correctMimeType: 'application/pdf',
            key: 'k-1',
            webUrl: '/record/k-1',
            validLastModified: Date.now(),
            size: 100,
          },
        },
        {
          placeholderResult: { documentId: 'doc-2', documentName: 'b.txt' },
          metadata: {
            file: {} as any,
            filePath: '/b.txt',
            fileName: 'b.txt',
            extension: '.txt',
            correctMimeType: 'text/plain',
            key: 'k-2',
            webUrl: '/record/k-2',
            validLastModified: Date.now(),
            size: 50,
          },
        },
      ]
      expect(results).to.have.length(2)
      expect(results[0].placeholderResult.documentName).to.equal('a.pdf')
      expect(results[1].placeholderResult.documentName).to.equal('b.txt')
    })
  })

  // =========================================================================
  // uploadFileToSignedUrl
  // =========================================================================
  describe('uploadFileToSignedUrl', () => {
    const nock = require('nock')

    afterEach(() => {
      nock.cleanAll()
      sinon.restore()
    })

    it('should resolve on successful upload (status 200)', async () => {
      const { uploadFileToSignedUrl } = require('../../../../src/modules/knowledge_base/utils/utils')
      nock('https://storage.example.com')
        .put('/upload')
        .query({ sig: 'abc' })
        .reply(200)

      await uploadFileToSignedUrl(
        Buffer.from('test content'),
        'application/pdf',
        'https://storage.example.com/upload?sig=abc',
        'doc-1',
        'test.pdf',
      )
    })

    it('should resolve on successful upload (status 201)', async () => {
      const { uploadFileToSignedUrl } = require('../../../../src/modules/knowledge_base/utils/utils')
      nock('https://storage.example.com')
        .put('/upload')
        .reply(201)

      await uploadFileToSignedUrl(
        Buffer.from('test'),
        'text/plain',
        'https://storage.example.com/upload',
        'doc-2',
        'test.txt',
      )
    })

    it('should throw on unexpected status code', async () => {
      const { uploadFileToSignedUrl } = require('../../../../src/modules/knowledge_base/utils/utils')
      nock('https://storage.example.com')
        .put('/upload')
        .reply(403, 'Forbidden')

      try {
        await uploadFileToSignedUrl(
          Buffer.from('test'),
          'text/plain',
          'https://storage.example.com/upload',
          'doc-3',
          'test.txt',
        )
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('Upload failed with status 403')
      }
    })

    it('should handle different content types', async () => {
      const { uploadFileToSignedUrl } = require('../../../../src/modules/knowledge_base/utils/utils')
      nock('https://storage.example.com')
        .put('/upload-img')
        .reply(200)

      await uploadFileToSignedUrl(
        Buffer.from('image data'),
        'image/png',
        'https://storage.example.com/upload-img',
        'doc-5',
        'image.png',
      )
    })
  })

  // =========================================================================
  // processUploadsInBackground
  // =========================================================================
  describe('processUploadsInBackground', () => {
    it('should process successful direct uploads (no uploadPromise)', async () => {
      const { processUploadsInBackground } = require('../../../../src/modules/knowledge_base/utils/utils')
      const { ConnectorServiceCommand } = require('../../../../src/libs/commands/connector_service/connector.service.command')

      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { success: true },
      })

      const placeholderResults: PlaceholderResultWithMetadata[] = [
        {
          placeholderResult: { documentId: 'doc-1', documentName: 'test.pdf' },
          metadata: {
            file: { buffer: Buffer.from('test'), originalname: 'test.pdf', mimetype: 'application/pdf', size: 100, fieldname: 'file' } as any,
            filePath: '/uploads/test.pdf',
            fileName: 'test.pdf',
            extension: '.pdf',
            correctMimeType: 'application/pdf',
            key: 'key-1',
            webUrl: '/record/key-1',
            validLastModified: Date.now(),
            size: 100,
          },
        },
      ]

      const { Logger } = require('../../../../src/libs/services/logger.service')
      const loggerInstance = Logger.getInstance({ service: 'test' })

      await processUploadsInBackground(
        placeholderResults,
        'org-1',
        'user-1',
        Date.now(),
        'http://python:8088/api/v1/kb/kb-1/upload',
        { authorization: 'Bearer token' },
        loggerInstance,
      )

      // Should have called the connector command
      expect(ConnectorServiceCommand.prototype.execute.calledOnce).to.be.true
    })

    it('should handle upload promise failures gracefully', async () => {
      const { processUploadsInBackground } = require('../../../../src/modules/knowledge_base/utils/utils')
      const { ConnectorServiceCommand } = require('../../../../src/libs/commands/connector_service/connector.service.command')

      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { success: true },
      })

      const placeholderResults: PlaceholderResultWithMetadata[] = [
        {
          placeholderResult: {
            documentId: 'doc-fail',
            documentName: 'fail.pdf',
            uploadPromise: Promise.reject(new Error('Upload timeout')),
          },
          metadata: {
            file: {} as any,
            filePath: '/uploads/fail.pdf',
            fileName: 'fail.pdf',
            extension: '.pdf',
            correctMimeType: 'application/pdf',
            key: 'key-fail',
            webUrl: '/record/key-fail',
            validLastModified: Date.now(),
            size: 100,
          },
        },
        {
          placeholderResult: { documentId: 'doc-ok', documentName: 'ok.pdf' },
          metadata: {
            file: {} as any,
            filePath: '/uploads/ok.pdf',
            fileName: 'ok.pdf',
            extension: '.pdf',
            correctMimeType: 'application/pdf',
            key: 'key-ok',
            webUrl: '/record/key-ok',
            validLastModified: Date.now(),
            size: 200,
          },
        },
      ]

      const { Logger } = require('../../../../src/libs/services/logger.service')
      const loggerInstance = Logger.getInstance({ service: 'test' })

      // Should not throw - background processing swallows errors
      await processUploadsInBackground(
        placeholderResults,
        'org-1',
        'user-1',
        Date.now(),
        'http://python:8088/api/v1/kb/kb-1/upload',
        { authorization: 'Bearer token' },
        loggerInstance,
      )
    })

    it('should send failure notification when all uploads fail', async () => {
      const { processUploadsInBackground } = require('../../../../src/modules/knowledge_base/utils/utils')

      const placeholderResults: PlaceholderResultWithMetadata[] = [
        {
          placeholderResult: {
            documentId: 'doc-1',
            documentName: 'fail.pdf',
            uploadPromise: Promise.reject(new Error('Upload failed')),
          },
          metadata: {
            file: {} as any,
            filePath: '/uploads/fail.pdf',
            fileName: 'fail.pdf',
            extension: '.pdf',
            correctMimeType: 'application/pdf',
            key: 'key-1',
            webUrl: '/record/key-1',
            validLastModified: Date.now(),
            size: 100,
          },
        },
      ]

      const { Logger } = require('../../../../src/libs/services/logger.service')
      const loggerInstance = Logger.getInstance({ service: 'test' })

      const mockNotification = {
        sendToUser: sinon.stub().returns(true),
      }

      await processUploadsInBackground(
        placeholderResults,
        'org-1',
        'user-1',
        Date.now(),
        'http://python:8088/api/v1/kb/kb-1/upload',
        { authorization: 'Bearer token' },
        loggerInstance,
        mockNotification as any,
        'kb-1',
      )

      // Should have sent failure notification
      expect(mockNotification.sendToUser.called).to.be.true
      const callArgs = mockNotification.sendToUser.firstCall.args
      expect(callArgs[0]).to.equal('user-1')
      expect(callArgs[1]).to.equal('records:failed')
    })

    it('should handle empty placeholder results', async () => {
      const { processUploadsInBackground } = require('../../../../src/modules/knowledge_base/utils/utils')
      const { Logger } = require('../../../../src/libs/services/logger.service')
      const loggerInstance = Logger.getInstance({ service: 'test' })

      // Should not throw
      await processUploadsInBackground(
        [],
        'org-1',
        'user-1',
        Date.now(),
        'http://python:8088/api/v1/kb/kb-1/upload',
        { authorization: 'Bearer token' },
        loggerInstance,
      )
    })

    it('should send notification on Python API failure', async () => {
      const { processUploadsInBackground } = require('../../../../src/modules/knowledge_base/utils/utils')
      const { ConnectorServiceCommand } = require('../../../../src/libs/commands/connector_service/connector.service.command')

      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 500,
        msg: 'Internal server error',
      })

      const placeholderResults: PlaceholderResultWithMetadata[] = [
        {
          placeholderResult: { documentId: 'doc-1', documentName: 'test.pdf' },
          metadata: {
            file: {} as any,
            filePath: '/uploads/test.pdf',
            fileName: 'test.pdf',
            extension: '.pdf',
            correctMimeType: 'application/pdf',
            key: 'key-1',
            webUrl: '/record/key-1',
            validLastModified: Date.now(),
            size: 100,
          },
        },
      ]

      const { Logger } = require('../../../../src/libs/services/logger.service')
      const loggerInstance = Logger.getInstance({ service: 'test' })

      const mockNotification = {
        sendToUser: sinon.stub().returns(true),
      }

      await processUploadsInBackground(
        placeholderResults,
        'org-1',
        'user-1',
        Date.now(),
        'http://python:8088/api/v1/kb/kb-1/upload',
        { authorization: 'Bearer token' },
        loggerInstance,
        mockNotification as any,
        'kb-1',
        'folder-1',
      )

      // Should have sent failure notification for Python API failure
      expect(mockNotification.sendToUser.called).to.be.true
      const callArgs = mockNotification.sendToUser.firstCall.args
      expect(callArgs[1]).to.equal('records:failed')
    })

    it('should send processed notification on success', async () => {
      const { processUploadsInBackground } = require('../../../../src/modules/knowledge_base/utils/utils')
      const { ConnectorServiceCommand } = require('../../../../src/libs/commands/connector_service/connector.service.command')

      sinon.stub(ConnectorServiceCommand.prototype, 'execute').resolves({
        statusCode: 200,
        data: { success: true },
      })

      const placeholderResults: PlaceholderResultWithMetadata[] = [
        {
          placeholderResult: { documentId: 'doc-1', documentName: 'test.pdf' },
          metadata: {
            file: {} as any,
            filePath: '/uploads/test.pdf',
            fileName: 'test.pdf',
            extension: '.pdf',
            correctMimeType: 'application/pdf',
            key: 'key-1',
            webUrl: '/record/key-1',
            validLastModified: Date.now(),
            size: 100,
          },
        },
      ]

      const { Logger } = require('../../../../src/libs/services/logger.service')
      const loggerInstance = Logger.getInstance({ service: 'test' })

      const mockNotification = {
        sendToUser: sinon.stub().returns(true),
      }

      await processUploadsInBackground(
        placeholderResults,
        'org-1',
        'user-1',
        Date.now(),
        'http://python:8088/api/v1/kb/kb-1/upload',
        { authorization: 'Bearer token' },
        loggerInstance,
        mockNotification as any,
        'kb-1',
      )

      // Should have sent processed notification
      expect(mockNotification.sendToUser.called).to.be.true
      const callArgs = mockNotification.sendToUser.firstCall.args
      expect(callArgs[0]).to.equal('user-1')
      expect(callArgs[1]).to.equal('records:processed')
    })
  })
})
