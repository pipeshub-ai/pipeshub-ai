import { BaseError, ErrorMetadata } from './base.error';

export class ETCDError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode = 400,
    metadata?: ErrorMetadata,
  ) {
    super(`ETCD_${code}`, message, statusCode, metadata);
  }
}

export class KeyNotFoundError extends ETCDError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('KEY_NOT_FOUND', message, 400, metadata);
  }
}

export class KeyAlreadyExistsError extends ETCDError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('KEY_ALREADY_EXISTS', message, 400, metadata);
  }
}

export class InvalidDataFormatError extends ETCDError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_FORMAT', message, 400, metadata);
  }
}

export class SchemaValidationFailedError extends ETCDError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('SCHEMA_VALIDATION_FAILED', message, 400, metadata);
  }
}

export class EtcdConnectionError extends ETCDError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('CONNECTION_ERROR', message, 502, metadata);
  }
}

export class EtcdOperationNotSupportedError extends ETCDError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('OPERATION_NOT_SUPPORTED', message, 400, metadata);
  }
}

export class EtcdTimeoutError extends ETCDError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('TIMEOUT_ERROR', message, 504, metadata);
  }
}

export class EtcdPermissionError extends ETCDError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('PERMISSION_ERROR', message, 401, metadata);
  }
}