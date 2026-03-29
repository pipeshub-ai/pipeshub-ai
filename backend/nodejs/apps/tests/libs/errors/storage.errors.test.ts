import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import {
  StorageError,
  StorageUploadError,
  StorageDownloadError,
  StorageNotFoundError,
  StorageAuthenticationError,
  StoragePermissionError,
  StorageValidationError,
  StorageConfigurationError,
  MultipartUploadError,
  PresignedUrlError,
  StorageDeleteError,
  StorageThrottlingError,
  StorageInsufficientSpaceError,
  StorageNetworkError,
  StoragePreconditionFailedError,
} from '../../../src/libs/errors/storage.errors';

describe('Storage Errors', () => {
  describe('StorageError', () => {
    it('should have correct name', () => {
      const error = new StorageError('CUSTOM', 'Storage failed', 500);
      expect(error.name).to.equal('StorageError');
    });

    it('should have correct code with STORAGE_ prefix', () => {
      const error = new StorageError('CUSTOM', 'Storage failed', 500);
      expect(error.code).to.equal('STORAGE_CUSTOM');
    });

    it('should have correct statusCode', () => {
      const error = new StorageError('CUSTOM', 'Storage failed', 404);
      expect(error.statusCode).to.equal(404);
    });

    it('should default statusCode to 500', () => {
      const error = new StorageError('CUSTOM', 'Storage failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new StorageError('CUSTOM', 'Storage failed', 500);
      expect(error.message).to.equal('Storage failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageError('CUSTOM', 'Storage failed', 500);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new StorageError('CUSTOM', 'Storage failed', 500);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { bucket: 'my-bucket' };
      const error = new StorageError('CUSTOM', 'Storage failed', 500, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new StorageError('CUSTOM', 'Storage failed', 500);
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageError('CUSTOM', 'Storage failed', 500);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageError');
      expect(json).to.have.property('code', 'STORAGE_CUSTOM');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Storage failed');
    });
  });

  describe('StorageUploadError', () => {
    it('should have correct name', () => {
      const error = new StorageUploadError('Upload failed');
      expect(error.name).to.equal('StorageUploadError');
    });

    it('should have correct code', () => {
      const error = new StorageUploadError('Upload failed');
      expect(error.code).to.equal('STORAGE_UPLOAD_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new StorageUploadError('Upload failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new StorageUploadError('Upload failed');
      expect(error.message).to.equal('Upload failed');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageUploadError('Upload failed');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageUploadError('Upload failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageUploadError('Upload failed');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('file size');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { fileName: 'test.pdf' };
      const error = new StorageUploadError('Upload failed', metadata);
      expect(error.metadata).to.have.property('fileName', 'test.pdf');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageUploadError('Upload failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageUploadError('Upload failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageUploadError');
      expect(json).to.have.property('code', 'STORAGE_UPLOAD_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('StorageDownloadError', () => {
    it('should have correct name', () => {
      const error = new StorageDownloadError('Download failed');
      expect(error.name).to.equal('StorageDownloadError');
    });

    it('should have correct code', () => {
      const error = new StorageDownloadError('Download failed');
      expect(error.code).to.equal('STORAGE_DOWNLOAD_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new StorageDownloadError('Download failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new StorageDownloadError('Download failed');
      expect(error.message).to.equal('Download failed');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageDownloadError('Download failed');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageDownloadError('Download failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageDownloadError('Download failed');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('file exists');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { fileKey: 'docs/report.pdf' };
      const error = new StorageDownloadError('Download failed', metadata);
      expect(error.metadata).to.have.property('fileKey', 'docs/report.pdf');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageDownloadError('Download failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageDownloadError('Download failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageDownloadError');
      expect(json).to.have.property('code', 'STORAGE_DOWNLOAD_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('StorageNotFoundError', () => {
    it('should have correct name', () => {
      const error = new StorageNotFoundError('File not found');
      expect(error.name).to.equal('StorageNotFoundError');
    });

    it('should have correct code', () => {
      const error = new StorageNotFoundError('File not found');
      expect(error.code).to.equal('STORAGE_NOT_FOUND');
    });

    it('should have correct statusCode of 404', () => {
      const error = new StorageNotFoundError('File not found');
      expect(error.statusCode).to.equal(404);
    });

    it('should preserve error message', () => {
      const error = new StorageNotFoundError('File not found');
      expect(error.message).to.equal('File not found');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageNotFoundError('File not found');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageNotFoundError('File not found');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageNotFoundError('File not found');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('file path');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { path: '/uploads/missing.pdf' };
      const error = new StorageNotFoundError('File not found', metadata);
      expect(error.metadata).to.have.property('path', '/uploads/missing.pdf');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageNotFoundError('File not found');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageNotFoundError('File not found');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageNotFoundError');
      expect(json).to.have.property('code', 'STORAGE_NOT_FOUND');
      expect(json).to.have.property('statusCode', 404);
    });
  });

  describe('StorageAuthenticationError', () => {
    it('should have correct name', () => {
      const error = new StorageAuthenticationError('Auth failed');
      expect(error.name).to.equal('StorageAuthenticationError');
    });

    it('should have correct code', () => {
      const error = new StorageAuthenticationError('Auth failed');
      expect(error.code).to.equal('STORAGE_AUTHENTICATION_ERROR');
    });

    it('should have correct statusCode of 401', () => {
      const error = new StorageAuthenticationError('Auth failed');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new StorageAuthenticationError('Auth failed');
      expect(error.message).to.equal('Auth failed');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageAuthenticationError('Auth failed');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageAuthenticationError('Auth failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageAuthenticationError('Auth failed');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('credentials');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { provider: 'S3' };
      const error = new StorageAuthenticationError('Auth failed', metadata);
      expect(error.metadata).to.have.property('provider', 'S3');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageAuthenticationError('Auth failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageAuthenticationError('Auth failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageAuthenticationError');
      expect(json).to.have.property('code', 'STORAGE_AUTHENTICATION_ERROR');
      expect(json).to.have.property('statusCode', 401);
    });
  });

  describe('StoragePermissionError', () => {
    it('should have correct name', () => {
      const error = new StoragePermissionError('Permission denied');
      expect(error.name).to.equal('StoragePermissionError');
    });

    it('should have correct code', () => {
      const error = new StoragePermissionError('Permission denied');
      expect(error.code).to.equal('STORAGE_PERMISSION_ERROR');
    });

    it('should have correct statusCode of 403', () => {
      const error = new StoragePermissionError('Permission denied');
      expect(error.statusCode).to.equal(403);
    });

    it('should preserve error message', () => {
      const error = new StoragePermissionError('Permission denied');
      expect(error.message).to.equal('Permission denied');
    });

    it('should be instanceof StorageError', () => {
      const error = new StoragePermissionError('Permission denied');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StoragePermissionError('Permission denied');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StoragePermissionError('Permission denied');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('permissions');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { bucket: 'restricted-bucket' };
      const error = new StoragePermissionError('Permission denied', metadata);
      expect(error.metadata).to.have.property('bucket', 'restricted-bucket');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StoragePermissionError('Permission denied');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StoragePermissionError('Permission denied');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StoragePermissionError');
      expect(json).to.have.property('code', 'STORAGE_PERMISSION_ERROR');
      expect(json).to.have.property('statusCode', 403);
    });
  });

  describe('StorageValidationError', () => {
    it('should have correct name', () => {
      const error = new StorageValidationError('Validation failed');
      expect(error.name).to.equal('StorageValidationError');
    });

    it('should have correct code', () => {
      const error = new StorageValidationError('Validation failed');
      expect(error.code).to.equal('STORAGE_VALIDATION_ERROR');
    });

    it('should have correct statusCode of 400', () => {
      const error = new StorageValidationError('Validation failed');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new StorageValidationError('Validation failed');
      expect(error.message).to.equal('Validation failed');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageValidationError('Validation failed');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageValidationError('Validation failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageValidationError('Validation failed');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('validation');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { field: 'fileName' };
      const error = new StorageValidationError('Validation failed', metadata);
      expect(error.metadata).to.have.property('field', 'fileName');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageValidationError('Validation failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageValidationError('Validation failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageValidationError');
      expect(json).to.have.property('code', 'STORAGE_VALIDATION_ERROR');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('StorageConfigurationError', () => {
    it('should have correct name', () => {
      const error = new StorageConfigurationError('Config error');
      expect(error.name).to.equal('StorageConfigurationError');
    });

    it('should have correct code', () => {
      const error = new StorageConfigurationError('Config error');
      expect(error.code).to.equal('STORAGE_CONFIGURATION_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new StorageConfigurationError('Config error');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new StorageConfigurationError('Config error');
      expect(error.message).to.equal('Config error');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageConfigurationError('Config error');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageConfigurationError('Config error');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageConfigurationError('Config error');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('configuration');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { setting: 'region' };
      const error = new StorageConfigurationError('Config error', metadata);
      expect(error.metadata).to.have.property('setting', 'region');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageConfigurationError('Config error');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageConfigurationError('Config error');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageConfigurationError');
      expect(json).to.have.property('code', 'STORAGE_CONFIGURATION_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('MultipartUploadError', () => {
    it('should have correct name', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      expect(error.name).to.equal('MultipartUploadError');
    });

    it('should have correct code', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      expect(error.code).to.equal('STORAGE_MULTIPART_UPLOAD_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      expect(error.message).to.equal('Multipart upload failed');
    });

    it('should be instanceof StorageError', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('parts');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { uploadId: 'upload-123', partNumber: 3 };
      const error = new MultipartUploadError('Multipart upload failed', metadata);
      expect(error.metadata).to.have.property('uploadId', 'upload-123');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new MultipartUploadError('Multipart upload failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'MultipartUploadError');
      expect(json).to.have.property('code', 'STORAGE_MULTIPART_UPLOAD_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('PresignedUrlError', () => {
    it('should have correct name', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      expect(error.name).to.equal('PresignedUrlError');
    });

    it('should have correct code', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      expect(error.code).to.equal('STORAGE_PRESIGNED_URL_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      expect(error.message).to.equal('Presigned URL generation failed');
    });

    it('should be instanceof StorageError', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('bucket policy');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { bucket: 'my-bucket', key: 'file.pdf' };
      const error = new PresignedUrlError('Presigned URL generation failed', metadata);
      expect(error.metadata).to.have.property('bucket', 'my-bucket');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new PresignedUrlError('Presigned URL generation failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'PresignedUrlError');
      expect(json).to.have.property('code', 'STORAGE_PRESIGNED_URL_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('StorageDeleteError', () => {
    it('should have correct name', () => {
      const error = new StorageDeleteError('Delete failed');
      expect(error.name).to.equal('StorageDeleteError');
    });

    it('should have correct code', () => {
      const error = new StorageDeleteError('Delete failed');
      expect(error.code).to.equal('STORAGE_DELETE_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new StorageDeleteError('Delete failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new StorageDeleteError('Delete failed');
      expect(error.message).to.equal('Delete failed');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageDeleteError('Delete failed');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageDeleteError('Delete failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageDeleteError('Delete failed');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('delete');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { key: 'docs/old-file.pdf' };
      const error = new StorageDeleteError('Delete failed', metadata);
      expect(error.metadata).to.have.property('key', 'docs/old-file.pdf');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageDeleteError('Delete failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageDeleteError('Delete failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageDeleteError');
      expect(json).to.have.property('code', 'STORAGE_DELETE_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('StorageThrottlingError', () => {
    it('should have correct name', () => {
      const error = new StorageThrottlingError('Throttled');
      expect(error.name).to.equal('StorageThrottlingError');
    });

    it('should have correct code', () => {
      const error = new StorageThrottlingError('Throttled');
      expect(error.code).to.equal('STORAGE_THROTTLING_ERROR');
    });

    it('should have correct statusCode of 429', () => {
      const error = new StorageThrottlingError('Throttled');
      expect(error.statusCode).to.equal(429);
    });

    it('should preserve error message', () => {
      const error = new StorageThrottlingError('Throttled');
      expect(error.message).to.equal('Throttled');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageThrottlingError('Throttled');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageThrottlingError('Throttled');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageThrottlingError('Throttled');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('rate');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { retryAfterSeconds: 60 };
      const error = new StorageThrottlingError('Throttled', metadata);
      expect(error.metadata).to.have.property('retryAfterSeconds', 60);
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageThrottlingError('Throttled');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageThrottlingError('Throttled');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageThrottlingError');
      expect(json).to.have.property('code', 'STORAGE_THROTTLING_ERROR');
      expect(json).to.have.property('statusCode', 429);
    });
  });

  describe('StorageInsufficientSpaceError', () => {
    it('should have correct name', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      expect(error.name).to.equal('StorageInsufficientSpaceError');
    });

    it('should have correct code', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      expect(error.code).to.equal('STORAGE_INSUFFICIENT_SPACE');
    });

    it('should have correct statusCode of 507', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      expect(error.statusCode).to.equal(507);
    });

    it('should preserve error message', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      expect(error.message).to.equal('Not enough space');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('space');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { requiredBytes: 1024000 };
      const error = new StorageInsufficientSpaceError('Not enough space', metadata);
      expect(error.metadata).to.have.property('requiredBytes', 1024000);
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageInsufficientSpaceError('Not enough space');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageInsufficientSpaceError');
      expect(json).to.have.property('code', 'STORAGE_INSUFFICIENT_SPACE');
      expect(json).to.have.property('statusCode', 507);
    });
  });

  describe('StorageNetworkError', () => {
    it('should have correct name', () => {
      const error = new StorageNetworkError('Network error');
      expect(error.name).to.equal('StorageNetworkError');
    });

    it('should have correct code', () => {
      const error = new StorageNetworkError('Network error');
      expect(error.code).to.equal('STORAGE_NETWORK_ERROR');
    });

    it('should have correct statusCode of 502', () => {
      const error = new StorageNetworkError('Network error');
      expect(error.statusCode).to.equal(502);
    });

    it('should preserve error message', () => {
      const error = new StorageNetworkError('Network error');
      expect(error.message).to.equal('Network error');
    });

    it('should be instanceof StorageError', () => {
      const error = new StorageNetworkError('Network error');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StorageNetworkError('Network error');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StorageNetworkError('Network error');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('network');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { endpoint: 's3.amazonaws.com' };
      const error = new StorageNetworkError('Network error', metadata);
      expect(error.metadata).to.have.property('endpoint', 's3.amazonaws.com');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StorageNetworkError('Network error');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StorageNetworkError('Network error');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StorageNetworkError');
      expect(json).to.have.property('code', 'STORAGE_NETWORK_ERROR');
      expect(json).to.have.property('statusCode', 502);
    });
  });

  describe('StoragePreconditionFailedError', () => {
    it('should have correct name', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      expect(error.name).to.equal('StoragePreconditionFailedError');
    });

    it('should have correct code', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      expect(error.code).to.equal('STORAGE_PRECONDITION_FAILED');
    });

    it('should have correct statusCode of 412', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      expect(error.statusCode).to.equal(412);
    });

    it('should preserve error message', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      expect(error.message).to.equal('Precondition failed');
    });

    it('should be instanceof StorageError', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      expect(error).to.be.an.instanceOf(StorageError);
    });

    it('should be instanceof BaseError', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should include hint in metadata', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      expect(error.metadata).to.have.property('hint');
      expect(error.metadata!.hint).to.include('precondition');
    });

    it('should merge provided metadata with hint', () => {
      const metadata = { ifMatch: 'etag-abc' };
      const error = new StoragePreconditionFailedError('Precondition failed', metadata);
      expect(error.metadata).to.have.property('ifMatch', 'etag-abc');
      expect(error.metadata).to.have.property('hint');
    });

    it('should have a stack trace', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new StoragePreconditionFailedError('Precondition failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'StoragePreconditionFailedError');
      expect(json).to.have.property('code', 'STORAGE_PRECONDITION_FAILED');
      expect(json).to.have.property('statusCode', 412);
    });
  });
});
