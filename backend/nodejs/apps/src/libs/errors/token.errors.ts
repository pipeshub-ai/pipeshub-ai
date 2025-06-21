import { BaseError, ErrorMetadata } from './base.error';

export class TokenError extends BaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(
      'TOKEN_ERROR', // Error code for token-related errors
      message,
      401, // Using 401 Unauthorized as the default status code for token errors
      metadata,
    );
  }
}
