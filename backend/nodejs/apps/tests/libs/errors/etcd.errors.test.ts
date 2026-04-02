import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import {
  ETCDError,
  KeyNotFoundError,
  KeyAlreadyExistsError,
  InvalidDataFormatError,
  SchemaValidationFailedError,
  EtcdConnectionError,
  EtcdOperationNotSupportedError,
  EtcdTimeoutError,
  EtcdPermissionError,
} from '../../../src/libs/errors/etcd.errors';

describe('ETCD Errors', () => {
  describe('ETCDError', () => {
    it('should have correct name', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed', 400);
      expect(error.name).to.equal('ETCDError');
    });

    it('should have correct code with ETCD_ prefix', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed', 400);
      expect(error.code).to.equal('ETCD_CUSTOM');
    });

    it('should have correct statusCode', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed', 502);
      expect(error.statusCode).to.equal(502);
    });

    it('should default statusCode to 400', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed', 400);
      expect(error.message).to.equal('ETCD failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed', 400);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed', 400);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { key: '/config/app' };
      const error = new ETCDError('CUSTOM', 'ETCD failed', 400, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed', 400);
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new ETCDError('CUSTOM', 'ETCD failed', 400);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ETCDError');
      expect(json).to.have.property('code', 'ETCD_CUSTOM');
      expect(json).to.have.property('statusCode', 400);
      expect(json).to.have.property('message', 'ETCD failed');
    });
  });

  describe('KeyNotFoundError', () => {
    it('should have correct name', () => {
      const error = new KeyNotFoundError('Key not found');
      expect(error.name).to.equal('KeyNotFoundError');
    });

    it('should have correct code', () => {
      const error = new KeyNotFoundError('Key not found');
      expect(error.code).to.equal('ETCD_KEY_NOT_FOUND');
    });

    it('should have correct statusCode of 400', () => {
      const error = new KeyNotFoundError('Key not found');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new KeyNotFoundError('Key not found');
      expect(error.message).to.equal('Key not found');
    });

    it('should be instanceof ETCDError', () => {
      const error = new KeyNotFoundError('Key not found');
      expect(error).to.be.an.instanceOf(ETCDError);
    });

    it('should be instanceof BaseError', () => {
      const error = new KeyNotFoundError('Key not found');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { key: '/missing/key' };
      const error = new KeyNotFoundError('Key not found', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new KeyNotFoundError('Key not found');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new KeyNotFoundError('Key not found');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'KeyNotFoundError');
      expect(json).to.have.property('code', 'ETCD_KEY_NOT_FOUND');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('KeyAlreadyExistsError', () => {
    it('should have correct name', () => {
      const error = new KeyAlreadyExistsError('Key already exists');
      expect(error.name).to.equal('KeyAlreadyExistsError');
    });

    it('should have correct code', () => {
      const error = new KeyAlreadyExistsError('Key already exists');
      expect(error.code).to.equal('ETCD_KEY_ALREADY_EXISTS');
    });

    it('should have correct statusCode of 400', () => {
      const error = new KeyAlreadyExistsError('Key already exists');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new KeyAlreadyExistsError('Key already exists');
      expect(error.message).to.equal('Key already exists');
    });

    it('should be instanceof ETCDError', () => {
      const error = new KeyAlreadyExistsError('Key already exists');
      expect(error).to.be.an.instanceOf(ETCDError);
    });

    it('should be instanceof BaseError', () => {
      const error = new KeyAlreadyExistsError('Key already exists');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { key: '/existing/key' };
      const error = new KeyAlreadyExistsError('Key already exists', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new KeyAlreadyExistsError('Key already exists');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new KeyAlreadyExistsError('Key already exists');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'KeyAlreadyExistsError');
      expect(json).to.have.property('code', 'ETCD_KEY_ALREADY_EXISTS');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('InvalidDataFormatError', () => {
    it('should have correct name', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.name).to.equal('InvalidDataFormatError');
    });

    it('should have correct code', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.code).to.equal('ETCD_INVALID_FORMAT');
    });

    it('should have correct statusCode of 400', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.message).to.equal('Invalid data format');
    });

    it('should be instanceof ETCDError', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error).to.be.an.instanceOf(ETCDError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { expectedFormat: 'JSON' };
      const error = new InvalidDataFormatError('Invalid data format', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InvalidDataFormatError');
      expect(json).to.have.property('code', 'ETCD_INVALID_FORMAT');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('SchemaValidationFailedError', () => {
    it('should have correct name', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error.name).to.equal('SchemaValidationFailedError');
    });

    it('should have correct code', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error.code).to.equal('ETCD_SCHEMA_VALIDATION_FAILED');
    });

    it('should have correct statusCode of 400', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error.message).to.equal('Schema validation failed');
    });

    it('should be instanceof ETCDError', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error).to.be.an.instanceOf(ETCDError);
    });

    it('should be instanceof BaseError', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { schema: 'config-v1', errors: ['missing field'] };
      const error = new SchemaValidationFailedError('Schema validation failed', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'SchemaValidationFailedError');
      expect(json).to.have.property('code', 'ETCD_SCHEMA_VALIDATION_FAILED');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('EtcdConnectionError', () => {
    it('should have correct name', () => {
      const error = new EtcdConnectionError('Cannot connect to etcd');
      expect(error.name).to.equal('EtcdConnectionError');
    });

    it('should have correct code', () => {
      const error = new EtcdConnectionError('Cannot connect to etcd');
      expect(error.code).to.equal('ETCD_CONNECTION_ERROR');
    });

    it('should have correct statusCode of 502', () => {
      const error = new EtcdConnectionError('Cannot connect to etcd');
      expect(error.statusCode).to.equal(502);
    });

    it('should preserve error message', () => {
      const error = new EtcdConnectionError('Cannot connect to etcd');
      expect(error.message).to.equal('Cannot connect to etcd');
    });

    it('should be instanceof ETCDError', () => {
      const error = new EtcdConnectionError('Cannot connect to etcd');
      expect(error).to.be.an.instanceOf(ETCDError);
    });

    it('should be instanceof BaseError', () => {
      const error = new EtcdConnectionError('Cannot connect to etcd');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { host: 'localhost:2379' };
      const error = new EtcdConnectionError('Cannot connect to etcd', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new EtcdConnectionError('Cannot connect to etcd');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new EtcdConnectionError('Cannot connect to etcd');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'EtcdConnectionError');
      expect(json).to.have.property('code', 'ETCD_CONNECTION_ERROR');
      expect(json).to.have.property('statusCode', 502);
    });
  });

  describe('EtcdOperationNotSupportedError', () => {
    it('should have correct name', () => {
      const error = new EtcdOperationNotSupportedError('Operation not supported');
      expect(error.name).to.equal('EtcdOperationNotSupportedError');
    });

    it('should have correct code', () => {
      const error = new EtcdOperationNotSupportedError('Operation not supported');
      expect(error.code).to.equal('ETCD_OPERATION_NOT_SUPPORTED');
    });

    it('should have correct statusCode of 400', () => {
      const error = new EtcdOperationNotSupportedError('Operation not supported');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new EtcdOperationNotSupportedError('Operation not supported');
      expect(error.message).to.equal('Operation not supported');
    });

    it('should be instanceof ETCDError', () => {
      const error = new EtcdOperationNotSupportedError('Operation not supported');
      expect(error).to.be.an.instanceOf(ETCDError);
    });

    it('should be instanceof BaseError', () => {
      const error = new EtcdOperationNotSupportedError('Operation not supported');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { operation: 'watch' };
      const error = new EtcdOperationNotSupportedError('Operation not supported', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new EtcdOperationNotSupportedError('Operation not supported');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new EtcdOperationNotSupportedError('Operation not supported');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'EtcdOperationNotSupportedError');
      expect(json).to.have.property('code', 'ETCD_OPERATION_NOT_SUPPORTED');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('EtcdTimeoutError', () => {
    it('should have correct name', () => {
      const error = new EtcdTimeoutError('ETCD operation timed out');
      expect(error.name).to.equal('EtcdTimeoutError');
    });

    it('should have correct code', () => {
      const error = new EtcdTimeoutError('ETCD operation timed out');
      expect(error.code).to.equal('ETCD_TIMEOUT_ERROR');
    });

    it('should have correct statusCode of 504', () => {
      const error = new EtcdTimeoutError('ETCD operation timed out');
      expect(error.statusCode).to.equal(504);
    });

    it('should preserve error message', () => {
      const error = new EtcdTimeoutError('ETCD operation timed out');
      expect(error.message).to.equal('ETCD operation timed out');
    });

    it('should be instanceof ETCDError', () => {
      const error = new EtcdTimeoutError('ETCD operation timed out');
      expect(error).to.be.an.instanceOf(ETCDError);
    });

    it('should be instanceof BaseError', () => {
      const error = new EtcdTimeoutError('ETCD operation timed out');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { timeoutMs: 5000 };
      const error = new EtcdTimeoutError('ETCD operation timed out', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new EtcdTimeoutError('ETCD operation timed out');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new EtcdTimeoutError('ETCD operation timed out');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'EtcdTimeoutError');
      expect(json).to.have.property('code', 'ETCD_TIMEOUT_ERROR');
      expect(json).to.have.property('statusCode', 504);
    });
  });

  describe('EtcdPermissionError', () => {
    it('should have correct name', () => {
      const error = new EtcdPermissionError('Permission denied');
      expect(error.name).to.equal('EtcdPermissionError');
    });

    it('should have correct code', () => {
      const error = new EtcdPermissionError('Permission denied');
      expect(error.code).to.equal('ETCD_PERMISSION_ERROR');
    });

    it('should have correct statusCode of 401', () => {
      const error = new EtcdPermissionError('Permission denied');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new EtcdPermissionError('Permission denied');
      expect(error.message).to.equal('Permission denied');
    });

    it('should be instanceof ETCDError', () => {
      const error = new EtcdPermissionError('Permission denied');
      expect(error).to.be.an.instanceOf(ETCDError);
    });

    it('should be instanceof BaseError', () => {
      const error = new EtcdPermissionError('Permission denied');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { user: 'readonly-user', requiredRole: 'admin' };
      const error = new EtcdPermissionError('Permission denied', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new EtcdPermissionError('Permission denied');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new EtcdPermissionError('Permission denied');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'EtcdPermissionError');
      expect(json).to.have.property('code', 'ETCD_PERMISSION_ERROR');
      expect(json).to.have.property('statusCode', 401);
    });
  });
});
