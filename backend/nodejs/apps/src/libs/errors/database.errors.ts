import { BaseError, ErrorMetadata } from './base.error';

export class DatabaseError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode = 500,
    metadata?: ErrorMetadata,
  ) {
    super(`DB_${code}`, message, statusCode, metadata);
  }
}

export class ConnectionError extends DatabaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('CONNECTION_ERROR', message, 503, metadata);
  }
}

export class QueryError extends DatabaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('QUERY_ERROR', message, 500, metadata);
  }
}

export class UniqueConstraintError extends DatabaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('UNIQUE_CONSTRAINT', message, 409, metadata);
  }
}

export class ForeignKeyError extends DatabaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('FOREIGN_KEY', message, 409, metadata);
  }
}

export class TransactionError extends DatabaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('TRANSACTION_ERROR', message, 500, metadata);
  }
}
