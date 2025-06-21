import { BaseError, ErrorMetadata } from './base.error';

export class EncryptionError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode = 500,
    metadata?: ErrorMetadata,
  ) {
    super(`ENCRYPTION_${code}`, message, statusCode, metadata);
  }
}

export class KeyGenerationError extends EncryptionError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('KEY_GENERATION_ERROR', message, 500, metadata);
  }
}

export class DecryptionError extends EncryptionError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('DECRYPTION_ERROR', message, 400, metadata);
  }
}

export class EncryptionKeyError extends EncryptionError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('KEY_ERROR', message, 500, metadata);
  }
}

export class InvalidKeyFormatError extends EncryptionError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_KEY_FORMAT', message, 400, metadata);
  }
}

export class AlgorithmError extends EncryptionError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('ALGORITHM_ERROR', message, 500, metadata);
  }
}

export class InvalidInputError extends EncryptionError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('INVALID_INPUT', message, 400, metadata);
  }
}

export class PaddingError extends EncryptionError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('PADDING_ERROR', message, 400, metadata);
  }
}

export class KeyExpirationError extends EncryptionError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('KEY_EXPIRED', message, 401, metadata);
  }
}
