import { BaseError, ErrorMetadata } from './base.error';

export class KafkaError extends BaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super(
      'KAFKA_ERROR', // Error code for Kafka-related errors
      message,
      503, // Service Unavailable - appropriate for messaging service errors
      metadata,
    );
  }
}
