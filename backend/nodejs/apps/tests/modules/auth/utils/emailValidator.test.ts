import 'reflect-metadata';
import { expect } from 'chai';
import emailValidator from '../../../../src/modules/auth/utils/emailValidator';

describe('emailValidator', () => {
  describe('validator', () => {
    it('should return true for a standard valid email', () => {
      expect(emailValidator.validator('test@example.com')).to.be.true;
    });

    it('should return true for email with subdomain', () => {
      expect(emailValidator.validator('user@mail.example.com')).to.be.true;
    });

    it('should return true for email with dots in local part', () => {
      expect(emailValidator.validator('first.last@example.com')).to.be.true;
    });

    it('should return true for email with plus addressing', () => {
      expect(emailValidator.validator('user+tag@example.com')).to.be.true;
    });

    it('should return false for email without @ symbol', () => {
      expect(emailValidator.validator('userexample.com')).to.be.false;
    });

    it('should return false for email without domain', () => {
      expect(emailValidator.validator('user@')).to.be.false;
    });

    it('should return false for email without TLD', () => {
      expect(emailValidator.validator('user@example')).to.be.false;
    });

    it('should return false for empty string', () => {
      expect(emailValidator.validator('')).to.be.false;
    });

    it('should return false for email with spaces', () => {
      expect(emailValidator.validator('user @example.com')).to.be.false;
    });

    it('should return false for just @ sign', () => {
      expect(emailValidator.validator('@')).to.be.false;
    });
  });

  describe('message', () => {
    it('should return an error message containing the invalid email', () => {
      const result = emailValidator.message({ value: 'invalid-email' });
      expect(result).to.equal('invalid-email is not a valid email!');
    });

    it('should return an error message for empty value', () => {
      const result = emailValidator.message({ value: '' });
      expect(result).to.equal(' is not a valid email!');
    });
  });
});
