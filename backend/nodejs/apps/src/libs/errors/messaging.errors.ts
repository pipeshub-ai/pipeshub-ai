import { BaseError, ErrorMetadata } from './base.error';
import { MESSAGE_BROKER_ERROR_CODE } from '../constants/messaging.constants';

export class MessageBrokerError extends BaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(MESSAGE_BROKER_ERROR_CODE, message, 503, metadata);
  }
}
