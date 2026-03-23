import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import {
  SerializationError,
  SerializationFailedError,
  DeserializationFailedError,
  InvalidDataFormatError,
  SchemaValidationFailedError,
} from '../../../src/libs/errors/serialization.error';

describe('Serialization Errors', () => {
  describe('SerializationError', () => {
    it('should have correct name', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed', 400);
      expect(error.name).to.equal('SerializationError');
    });

    it('should have correct code with SERIALIZATION_ prefix', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed', 400);
      expect(error.code).to.equal('SERIALIZATION_CUSTOM');
    });

    it('should have correct statusCode', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed', 500);
      expect(error.statusCode).to.equal(500);
    });

    it('should default statusCode to 400', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed', 400);
      expect(error.message).to.equal('Serialization failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed', 400);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed', 400);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { format: 'JSON' };
      const error = new SerializationError('CUSTOM', 'Serialization failed', 400, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed', 400);
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new SerializationError('CUSTOM', 'Serialization failed', 400);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'SerializationError');
      expect(json).to.have.property('code', 'SERIALIZATION_CUSTOM');
      expect(json).to.have.property('statusCode', 400);
      expect(json).to.have.property('message', 'Serialization failed');
    });
  });

  describe('SerializationFailedError', () => {
    it('should have correct name', () => {
      const error = new SerializationFailedError('Failed to serialize');
      expect(error.name).to.equal('SerializationFailedError');
    });

    it('should have correct code', () => {
      const error = new SerializationFailedError('Failed to serialize');
      expect(error.code).to.equal('SERIALIZATION_FAILED');
    });

    it('should have correct statusCode of 500', () => {
      const error = new SerializationFailedError('Failed to serialize');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new SerializationFailedError('Failed to serialize');
      expect(error.message).to.equal('Failed to serialize');
    });

    it('should be instanceof SerializationError', () => {
      const error = new SerializationFailedError('Failed to serialize');
      expect(error).to.be.an.instanceOf(SerializationError);
    });

    it('should be instanceof BaseError', () => {
      const error = new SerializationFailedError('Failed to serialize');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { objectType: 'User' };
      const error = new SerializationFailedError('Failed to serialize', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new SerializationFailedError('Failed to serialize');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new SerializationFailedError('Failed to serialize');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'SerializationFailedError');
      expect(json).to.have.property('code', 'SERIALIZATION_FAILED');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('DeserializationFailedError', () => {
    it('should have correct name', () => {
      const error = new DeserializationFailedError('Failed to deserialize');
      expect(error.name).to.equal('DeserializationFailedError');
    });

    it('should have correct code', () => {
      const error = new DeserializationFailedError('Failed to deserialize');
      expect(error.code).to.equal('SERIALIZATION_DESERIALIZATION_FAILED');
    });

    it('should have correct statusCode of 500', () => {
      const error = new DeserializationFailedError('Failed to deserialize');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new DeserializationFailedError('Failed to deserialize');
      expect(error.message).to.equal('Failed to deserialize');
    });

    it('should be instanceof SerializationError', () => {
      const error = new DeserializationFailedError('Failed to deserialize');
      expect(error).to.be.an.instanceOf(SerializationError);
    });

    it('should be instanceof BaseError', () => {
      const error = new DeserializationFailedError('Failed to deserialize');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { rawData: '{ invalid json' };
      const error = new DeserializationFailedError('Failed to deserialize', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new DeserializationFailedError('Failed to deserialize');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new DeserializationFailedError('Failed to deserialize');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'DeserializationFailedError');
      expect(json).to.have.property('code', 'SERIALIZATION_DESERIALIZATION_FAILED');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('InvalidDataFormatError', () => {
    it('should have correct name', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.name).to.equal('InvalidDataFormatError');
    });

    it('should have correct code', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.code).to.equal('SERIALIZATION_INVALID_FORMAT');
    });

    it('should have correct statusCode of 400', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error.message).to.equal('Invalid data format');
    });

    it('should be instanceof SerializationError', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error).to.be.an.instanceOf(SerializationError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidDataFormatError('Invalid data format');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { expectedFormat: 'JSON', receivedFormat: 'XML' };
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
      expect(json).to.have.property('code', 'SERIALIZATION_INVALID_FORMAT');
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
      expect(error.code).to.equal('SERIALIZATION_SCHEMA_VALIDATION_FAILED');
    });

    it('should have correct statusCode of 400', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error.message).to.equal('Schema validation failed');
    });

    it('should be instanceof SerializationError', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error).to.be.an.instanceOf(SerializationError);
    });

    it('should be instanceof BaseError', () => {
      const error = new SchemaValidationFailedError('Schema validation failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { schema: 'user-v2', validationErrors: ['missing name'] };
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
      expect(json).to.have.property('code', 'SERIALIZATION_SCHEMA_VALIDATION_FAILED');
      expect(json).to.have.property('statusCode', 400);
    });
  });
});
