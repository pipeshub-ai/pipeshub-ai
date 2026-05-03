import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import {
  DatabaseError,
  ConnectionError,
  QueryError,
  UniqueConstraintError,
  ForeignKeyError,
  TransactionError,
} from '../../../src/libs/errors/database.errors';

describe('Database Errors', () => {
  describe('DatabaseError', () => {
    it('should have correct name', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed', 500);
      expect(error.name).to.equal('DatabaseError');
    });

    it('should have correct code with DB_ prefix', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed', 500);
      expect(error.code).to.equal('DB_CUSTOM');
    });

    it('should have correct statusCode', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed', 503);
      expect(error.statusCode).to.equal(503);
    });

    it('should default statusCode to 500', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed', 500);
      expect(error.message).to.equal('Database failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed', 500);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed', 500);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { query: 'SELECT * FROM users', database: 'main' };
      const error = new DatabaseError('CUSTOM', 'Database failed', 500, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed', 500);
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new DatabaseError('CUSTOM', 'Database failed', 500);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'DatabaseError');
      expect(json).to.have.property('code', 'DB_CUSTOM');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Database failed');
    });
  });

  describe('ConnectionError', () => {
    it('should have correct name', () => {
      const error = new ConnectionError('Cannot connect to database');
      expect(error.name).to.equal('ConnectionError');
    });

    it('should have correct code', () => {
      const error = new ConnectionError('Cannot connect to database');
      expect(error.code).to.equal('DB_CONNECTION_ERROR');
    });

    it('should have correct statusCode of 503', () => {
      const error = new ConnectionError('Cannot connect to database');
      expect(error.statusCode).to.equal(503);
    });

    it('should preserve error message', () => {
      const error = new ConnectionError('Cannot connect to database');
      expect(error.message).to.equal('Cannot connect to database');
    });

    it('should be instanceof DatabaseError', () => {
      const error = new ConnectionError('Cannot connect to database');
      expect(error).to.be.an.instanceOf(DatabaseError);
    });

    it('should be instanceof BaseError', () => {
      const error = new ConnectionError('Cannot connect to database');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { host: 'localhost', port: 5432 };
      const error = new ConnectionError('Cannot connect to database', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new ConnectionError('Cannot connect to database');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new ConnectionError('Cannot connect to database');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ConnectionError');
      expect(json).to.have.property('code', 'DB_CONNECTION_ERROR');
      expect(json).to.have.property('statusCode', 503);
    });
  });

  describe('QueryError', () => {
    it('should have correct name', () => {
      const error = new QueryError('Query syntax error');
      expect(error.name).to.equal('QueryError');
    });

    it('should have correct code', () => {
      const error = new QueryError('Query syntax error');
      expect(error.code).to.equal('DB_QUERY_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new QueryError('Query syntax error');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new QueryError('Query syntax error');
      expect(error.message).to.equal('Query syntax error');
    });

    it('should be instanceof DatabaseError', () => {
      const error = new QueryError('Query syntax error');
      expect(error).to.be.an.instanceOf(DatabaseError);
    });

    it('should be instanceof BaseError', () => {
      const error = new QueryError('Query syntax error');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { sql: 'SELECT * FROM invalid_table' };
      const error = new QueryError('Query syntax error', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new QueryError('Query syntax error');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new QueryError('Query syntax error');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'QueryError');
      expect(json).to.have.property('code', 'DB_QUERY_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('UniqueConstraintError', () => {
    it('should have correct name', () => {
      const error = new UniqueConstraintError('Duplicate entry');
      expect(error.name).to.equal('UniqueConstraintError');
    });

    it('should have correct code', () => {
      const error = new UniqueConstraintError('Duplicate entry');
      expect(error.code).to.equal('DB_UNIQUE_CONSTRAINT');
    });

    it('should have correct statusCode of 409', () => {
      const error = new UniqueConstraintError('Duplicate entry');
      expect(error.statusCode).to.equal(409);
    });

    it('should preserve error message', () => {
      const error = new UniqueConstraintError('Duplicate entry');
      expect(error.message).to.equal('Duplicate entry');
    });

    it('should be instanceof DatabaseError', () => {
      const error = new UniqueConstraintError('Duplicate entry');
      expect(error).to.be.an.instanceOf(DatabaseError);
    });

    it('should be instanceof BaseError', () => {
      const error = new UniqueConstraintError('Duplicate entry');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { field: 'email', value: 'test@example.com' };
      const error = new UniqueConstraintError('Duplicate entry', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new UniqueConstraintError('Duplicate entry');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new UniqueConstraintError('Duplicate entry');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'UniqueConstraintError');
      expect(json).to.have.property('code', 'DB_UNIQUE_CONSTRAINT');
      expect(json).to.have.property('statusCode', 409);
    });
  });

  describe('ForeignKeyError', () => {
    it('should have correct name', () => {
      const error = new ForeignKeyError('Foreign key constraint violated');
      expect(error.name).to.equal('ForeignKeyError');
    });

    it('should have correct code', () => {
      const error = new ForeignKeyError('Foreign key constraint violated');
      expect(error.code).to.equal('DB_FOREIGN_KEY');
    });

    it('should have correct statusCode of 409', () => {
      const error = new ForeignKeyError('Foreign key constraint violated');
      expect(error.statusCode).to.equal(409);
    });

    it('should preserve error message', () => {
      const error = new ForeignKeyError('Foreign key constraint violated');
      expect(error.message).to.equal('Foreign key constraint violated');
    });

    it('should be instanceof DatabaseError', () => {
      const error = new ForeignKeyError('Foreign key constraint violated');
      expect(error).to.be.an.instanceOf(DatabaseError);
    });

    it('should be instanceof BaseError', () => {
      const error = new ForeignKeyError('Foreign key constraint violated');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { table: 'orders', referencedTable: 'users' };
      const error = new ForeignKeyError('Foreign key constraint violated', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new ForeignKeyError('Foreign key constraint violated');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new ForeignKeyError('Foreign key constraint violated');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ForeignKeyError');
      expect(json).to.have.property('code', 'DB_FOREIGN_KEY');
      expect(json).to.have.property('statusCode', 409);
    });
  });

  describe('TransactionError', () => {
    it('should have correct name', () => {
      const error = new TransactionError('Transaction failed');
      expect(error.name).to.equal('TransactionError');
    });

    it('should have correct code', () => {
      const error = new TransactionError('Transaction failed');
      expect(error.code).to.equal('DB_TRANSACTION_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new TransactionError('Transaction failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new TransactionError('Transaction failed');
      expect(error.message).to.equal('Transaction failed');
    });

    it('should be instanceof DatabaseError', () => {
      const error = new TransactionError('Transaction failed');
      expect(error).to.be.an.instanceOf(DatabaseError);
    });

    it('should be instanceof BaseError', () => {
      const error = new TransactionError('Transaction failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { transactionId: 'tx-123' };
      const error = new TransactionError('Transaction failed', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new TransactionError('Transaction failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new TransactionError('Transaction failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'TransactionError');
      expect(json).to.have.property('code', 'DB_TRANSACTION_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });
});
