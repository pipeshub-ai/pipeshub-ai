import { BaseError, ErrorMetadata } from './base.error';

export class MessageBrokerError extends BaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(
      'MESSAGE_BROKER_ERROR',
      message,
      503,
      metadata,
    );
  }
}
