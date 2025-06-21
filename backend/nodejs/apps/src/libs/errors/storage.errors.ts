import { BaseError, ErrorMetadata } from './base.error';

// Base class for all storage-related errors
export class StorageError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode = 500,
    metadata?: ErrorMetadata,
  ) {
    super(`STORAGE_${code}`, message, statusCode, metadata);
  }
}

// Specific storage error subclasses for common scenarios

// Upload errors
export class StorageUploadError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('UPLOAD_ERROR', message, 500, {
      ...metadata,
      hint: 'Check if the file size exceeds the allowed limit or if the storage is full.',
    });
  }
}

// Download errors
export class StorageDownloadError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('DOWNLOAD_ERROR', message, 500, {
      ...metadata,
      hint: 'Verify that the file exists and that the user has sufficient permissions.',
    });
  }
}

// File not found errors
export class StorageNotFoundError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('NOT_FOUND', message, 404, {
      ...metadata,
      hint: 'Ensure the file path and key are correct.',
    });
  }
}

// Authentication errors
export class StorageAuthenticationError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('AUTHENTICATION_ERROR', message, 401, {
      ...metadata,
      hint: 'Check if the authentication credentials are valid or expired.',
    });
  }
}

// Permission errors
export class StoragePermissionError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('PERMISSION_ERROR', message, 403, {
      ...metadata,
      hint: 'Ensure the user has the necessary permissions to access this resource.',
    });
  }
}

// Validation errors
export class StorageValidationError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('VALIDATION_ERROR', message, 400, {
      ...metadata,
      hint: 'Verify that the input parameters meet the required validation rules.',
    });
  }
}

// Configuration errors
export class StorageConfigurationError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('CONFIGURATION_ERROR', message, 500, {
      ...metadata,
      hint: 'Check the storage service configuration settings.',
    });
  }
}

// Multipart upload errors
export class MultipartUploadError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('MULTIPART_UPLOAD_ERROR', message, 500, {
      ...metadata,
      hint: 'Ensure all parts are uploaded and the manifest is correct.',
    });
  }
}

// Presigned URL generation errors
export class PresignedUrlError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('PRESIGNED_URL_ERROR', message, 500, {
      ...metadata,
      hint: 'Ensure the bucket policy allows presigned URL generation.',
    });
  }
}

// Delete errors
export class StorageDeleteError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('DELETE_ERROR', message, 500, {
      ...metadata,
      hint: 'Check if the file exists and if the user has delete permissions.',
    });
  }
}

// Generic throttling error
export class StorageThrottlingError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('THROTTLING_ERROR', message, 429, {
      ...metadata,
      hint: 'The request rate exceeds the allowed limit. Retry after some time.',
    });
  }
}

// Insufficient storage space error
export class StorageInsufficientSpaceError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INSUFFICIENT_SPACE', message, 507, {
      ...metadata,
      hint: 'The storage system has run out of space. Free up some space and retry.',
    });
  }
}

// Network-related errors
export class StorageNetworkError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('NETWORK_ERROR', message, 502, {
      ...metadata,
      hint: 'Check the network connectivity and retry the request.',
    });
  }
}

// Precondition failed error
export class StoragePreconditionFailedError extends StorageError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('PRECONDITION_FAILED', message, 412, {
      ...metadata,
      hint: 'Ensure the precondition headers or resource state match the requirements.',
    });
  }
}
