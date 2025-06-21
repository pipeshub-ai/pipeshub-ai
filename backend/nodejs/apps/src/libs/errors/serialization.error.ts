import { BaseError, ErrorMetadata } from './base.error';

export class SerializationError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode = 400,
    metadata?: ErrorMetadata,
  ) {
    super(`SERIALIZATION_${code}`, message, statusCode, metadata);
  }
}

export class SerializationFailedError extends SerializationError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('FAILED', message, 500, metadata);
  }
}

export class DeserializationFailedError extends SerializationError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('DESERIALIZATION_FAILED', message, 500, metadata);
  }
}

export class InvalidDataFormatError extends SerializationError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_FORMAT', message, 400, metadata);
  }
}

export class SchemaValidationFailedError extends SerializationError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('SCHEMA_VALIDATION_FAILED', message, 400, metadata);
  }
}