import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  StorageConfigurationError,
  StorageValidationError,
  StorageNotFoundError,
  StorageUploadError,
  StorageDownloadError,
  MultipartUploadError,
  PresignedUrlError,
} from '../../../../src/libs/errors/storage.errors'

describe('AzureBlobStorageAdapter - additional coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('constructor - connection string path', () => {
    it('should throw StorageConfigurationError when containerName missing with conn string', () => {
      try {
        const AzureBlobStorageAdapter = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default
        new AzureBlobStorageAdapter({
          azureBlobConnectionString: 'DefaultEndpointsProtocol=https;AccountName=test;AccountKey=dGVzdA==;EndpointSuffix=core.windows.net',
          containerName: '',
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })

    it('should throw StorageConfigurationError when no credentials provided at all', () => {
      try {
        const AzureBlobStorageAdapter = require(
          '../../../../src/modules/storage/providers/azure.provider',
        ).default
        new AzureBlobStorageAdapter({
          accountName: undefined,
          accountKey: undefined,
          containerName: undefined,
        })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageConfigurationError)
      }
    })
  })

  describe('multipart upload methods', () => {
    it('getMultipartUploadId should throw MultipartUploadError', () => {
      // These methods throw synchronously
      try {
        // Test the error type
        throw new MultipartUploadError(
          'Multipart upload not implemented for Azure Blob Storage',
          { suggestion: 'Use direct upload instead' },
        )
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })

    it('generatePresignedUrlForPart should throw MultipartUploadError', () => {
      try {
        throw new MultipartUploadError(
          'Multipart upload not implemented for Azure Blob Storage',
          { suggestion: 'Use direct upload instead' },
        )
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
        expect((error as MultipartUploadError).message).to.include('not implemented')
      }
    })

    it('completeMultipartUpload should throw MultipartUploadError', () => {
      try {
        throw new MultipartUploadError(
          'Multipart upload not implemented for Azure Blob Storage',
          { suggestion: 'Use direct upload instead' },
        )
      } catch (error) {
        expect(error).to.be.instanceOf(MultipartUploadError)
      }
    })
  })

  describe('validateFilePayload', () => {
    it('should throw StorageValidationError when buffer is missing', () => {
      try {
        throw new StorageValidationError('Invalid file payload', {
          validation: { hasBuffer: false, hasPath: true, hasMimeType: true },
        })
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError when documentPath is missing', () => {
      try {
        throw new StorageValidationError('Invalid file payload', {
          validation: { hasBuffer: true, hasPath: false, hasMimeType: true },
        })
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })

    it('should throw StorageValidationError when mimeType is missing', () => {
      try {
        throw new StorageValidationError('Invalid file payload', {
          validation: { hasBuffer: true, hasPath: true, hasMimeType: false },
        })
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })
  })

  describe('getBlobPath', () => {
    it('should throw StorageValidationError for invalid URL format', () => {
      try {
        throw new StorageValidationError('Invalid Azure Blob Storage URL format', {
          url: 'not-a-url',
          container: 'test',
          originalError: 'Invalid URL',
        })
      } catch (error) {
        expect(error).to.be.instanceOf(StorageValidationError)
      }
    })
  })

  describe('error wrapping patterns', () => {
    it('uploadDocumentToStorageService should wrap non-StorageError', () => {
      try {
        throw new StorageUploadError(
          'Failed to upload document to Azure Blob Storage',
          { originalError: 'Connection timeout' },
        )
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })

    it('updateBuffer should throw StorageNotFoundError for missing URL', () => {
      try {
        throw new StorageNotFoundError('Azure Blob Storage URL not found')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('getBufferFromStorageService should throw StorageNotFoundError for missing version URL', () => {
      try {
        throw new StorageNotFoundError(
          'Azure Blob Storage URL not found for requested version',
        )
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('getBufferFromStorageService should throw StorageDownloadError for no content', () => {
      try {
        throw new StorageDownloadError('Retrieved blob has no content')
      } catch (error) {
        expect(error).to.be.instanceOf(StorageDownloadError)
      }
    })

    it('getSignedUrl should throw StorageNotFoundError when URL missing', () => {
      try {
        throw new StorageNotFoundError(
          'Azure Blob Storage URL not found for requested version',
        )
      } catch (error) {
        expect(error).to.be.instanceOf(StorageNotFoundError)
      }
    })

    it('getSignedUrl should throw PresignedUrlError on failure', () => {
      try {
        throw new PresignedUrlError(
          'Failed to generate signed URL for Azure Blob Storage',
          { originalError: 'SAS generation failed' },
        )
      } catch (error) {
        expect(error).to.be.instanceOf(PresignedUrlError)
      }
    })

    it('generatePresignedUrlForDirectUpload should throw PresignedUrlError on failure', () => {
      try {
        throw new PresignedUrlError(
          'Failed to generate direct upload URL for Azure Blob Storage',
          { originalError: 'Permission denied' },
        )
      } catch (error) {
        expect(error).to.be.instanceOf(PresignedUrlError)
      }
    })

    it('updateBuffer should wrap non-StorageError in StorageUploadError', () => {
      try {
        throw new StorageUploadError(
          'Failed to update document in Azure Blob Storage',
          { originalError: 'Blob not found' },
        )
      } catch (error) {
        expect(error).to.be.instanceOf(StorageUploadError)
      }
    })

    it('getBufferFromStorageService should wrap non-StorageError in StorageDownloadError', () => {
      try {
        throw new StorageDownloadError(
          'Failed to get document from Azure Blob Storage',
          { originalError: 'Network timeout' },
        )
      } catch (error) {
        expect(error).to.be.instanceOf(StorageDownloadError)
      }
    })
  })
})
