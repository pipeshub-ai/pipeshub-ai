import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import { ValidationError } from '../../../src/libs/errors/validation.error';
import { ValidationErrorDetail } from '../../../src/libs/types/validation.types';

describe('Validation Errors', () => {
  describe('ValidationError', () => {
    const sampleErrors: ValidationErrorDetail[] = [
      { field: 'email', message: 'Invalid email format', code: 'INVALID_FORMAT' },
      { field: 'name', message: 'Name is required', value: '', code: 'REQUIRED' },
    ];

    it('should have correct name', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error.name).to.equal('ValidationError');
    });

    it('should have correct code', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error.code).to.equal('VALIDATION_ERROR');
    });

    it('should have correct statusCode of 400', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error.message).to.equal('Validation failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should store validation errors on the errors property', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error.errors).to.deep.equal(sampleErrors);
    });

    it('should store errors in metadata as well', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error.metadata).to.deep.equal({ errors: sampleErrors });
    });

    it('should handle empty errors array', () => {
      const error = new ValidationError('Validation failed', []);
      expect(error.errors).to.deep.equal([]);
    });

    it('should handle single error', () => {
      const singleError: ValidationErrorDetail[] = [
        { field: 'password', message: 'Too short', code: 'MIN_LENGTH' },
      ];
      const error = new ValidationError('Validation failed', singleError);
      expect(error.errors).to.have.lengthOf(1);
      expect(error.errors[0]!.field).to.equal('password');
    });

    it('should have a stack trace', () => {
      const error = new ValidationError('Validation failed', sampleErrors);
      expect(error.stack).to.be.a('string');
    });

    describe('toJSON', () => {
      it('should serialize to JSON with errors included', () => {
        const error = new ValidationError('Validation failed', sampleErrors);
        const json = error.toJSON();
        expect(json).to.have.property('name', 'ValidationError');
        expect(json).to.have.property('code', 'VALIDATION_ERROR');
        expect(json).to.have.property('statusCode', 400);
        expect(json).to.have.property('message', 'Validation failed');
        expect(json).to.have.property('errors');
        expect(json.errors).to.deep.equal(sampleErrors);
      });

      it('should include base JSON properties', () => {
        const error = new ValidationError('Validation failed', sampleErrors);
        const json = error.toJSON();
        expect(json).to.have.property('timestamp');
        expect(json).to.have.property('metadata');
      });

      it('should not include stack trace by default', () => {
        const error = new ValidationError('Validation failed', sampleErrors);
        const json = error.toJSON();
        expect(json).to.not.have.property('stack');
      });

      it('should preserve validation error details including optional value', () => {
        const errorsWithValue: ValidationErrorDetail[] = [
          { field: 'age', message: 'Must be a number', value: 'abc', code: 'INVALID_TYPE' },
        ];
        const error = new ValidationError('Validation failed', errorsWithValue);
        const json = error.toJSON();
        expect(json.errors[0]).to.have.property('value', 'abc');
      });
    });
  });
});
