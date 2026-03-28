import { expect } from 'chai';
import { BaseError, ErrorMetadata } from '../../../src/libs/errors/base.error';

// BaseError is abstract, so we need a concrete subclass to test it
class ConcreteError extends BaseError {
  constructor(
    code: string,
    message: string,
    statusCode: number,
    metadata?: ErrorMetadata,
  ) {
    super(code, message, statusCode, metadata);
  }
}

describe('BaseError', () => {
  describe('constructor', () => {
    it('should set the error message', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error.message).to.equal('Something went wrong');
    });

    it('should set the code property', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error.code).to.equal('TEST_CODE');
    });

    it('should set the statusCode property', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 404);
      expect(error.statusCode).to.equal(404);
    });

    it('should set the name to the constructor name', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error.name).to.equal('ConcreteError');
    });

    it('should set the timestamp', () => {
      const before = new Date();
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      const after = new Date();
      expect(error.timestamp).to.be.an.instanceOf(Date);
      expect(error.timestamp.getTime()).to.be.at.least(before.getTime());
      expect(error.timestamp.getTime()).to.be.at.most(after.getTime());
    });

    it('should have a stack trace', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error.stack).to.be.a('string');
      expect(error.stack).to.include('Something went wrong');
    });

    it('should set metadata when provided', () => {
      const metadata = { userId: '123', action: 'delete' };
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should leave metadata undefined when not provided', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error.metadata).to.be.undefined;
    });

    it('should be an instance of Error', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should be an instance of BaseError', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error).to.be.an.instanceOf(BaseError);
    });
  });

  describe('toJSON', () => {
    it('should serialize to JSON without stack trace by default', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'ConcreteError');
      expect(json).to.have.property('code', 'TEST_CODE');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Something went wrong');
      expect(json).to.have.property('timestamp');
      expect(json).to.not.have.property('stack');
    });

    it('should include stack trace when includeStack is true', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      const json = error.toJSON(true);
      expect(json).to.have.property('stack');
      expect(json.stack).to.be.a('string');
    });

    it('should not include stack trace when includeStack is false', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      const json = error.toJSON(false);
      expect(json).to.not.have.property('stack');
    });

    it('should include metadata in JSON output', () => {
      const metadata = { detail: 'extra info' };
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500, metadata);
      const json = error.toJSON();
      expect(json).to.have.property('metadata');
      expect(json.metadata).to.deep.equal(metadata);
    });

    it('should include undefined metadata when none provided', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      const json = error.toJSON();
      expect(json).to.have.property('metadata');
      expect(json.metadata).to.be.undefined;
    });
  });

  describe('readonly properties', () => {
    it('should have readonly code', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error.code).to.equal('TEST_CODE');
    });

    it('should have readonly statusCode', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 404);
      expect(error.statusCode).to.equal(404);
    });

    it('should have readonly timestamp', () => {
      const error = new ConcreteError('TEST_CODE', 'Something went wrong', 500);
      expect(error.timestamp).to.be.an.instanceOf(Date);
    });
  });
});
