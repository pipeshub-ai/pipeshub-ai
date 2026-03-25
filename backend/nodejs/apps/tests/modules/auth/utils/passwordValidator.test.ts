import 'reflect-metadata';
import { expect } from 'chai';
import { passwordValidator } from '../../../../src/modules/auth/utils/passwordValidator';

describe('passwordValidator', () => {
  it('should return true for a valid password with all required characters', () => {
    expect(passwordValidator('Abcdef1!')).to.be.true;
  });

  it('should return true for a strong complex password', () => {
    expect(passwordValidator('MyP@ssw0rd#2024')).to.be.true;
  });

  it('should return false for a password shorter than 8 characters', () => {
    expect(passwordValidator('Ab1!xyz')).to.be.false;
  });

  it('should return false for a password without uppercase letters', () => {
    expect(passwordValidator('abcdef1!')).to.be.false;
  });

  it('should return false for a password without lowercase letters', () => {
    expect(passwordValidator('ABCDEF1!')).to.be.false;
  });

  it('should return false for a password without digits', () => {
    expect(passwordValidator('Abcdefg!')).to.be.false;
  });

  it('should return false for a password without special characters', () => {
    expect(passwordValidator('Abcdefg1')).to.be.false;
  });

  it('should return false for an empty string', () => {
    expect(passwordValidator('')).to.be.false;
  });

  it('should return true for a very long valid password', () => {
    expect(passwordValidator('Abcdefg1!aaaaaaaaaaaaaaaaaaaaaa')).to.be.true;
  });

  it('should return true for password with various special characters', () => {
    expect(passwordValidator('Test#123')).to.be.true;
    expect(passwordValidator('Test?123')).to.be.true;
    expect(passwordValidator('Test!123')).to.be.true;
    expect(passwordValidator('Test@123')).to.be.true;
    expect(passwordValidator('Test$123')).to.be.true;
    expect(passwordValidator('Test%123')).to.be.true;
    expect(passwordValidator('Test^123')).to.be.true;
    expect(passwordValidator('Test&123')).to.be.true;
    expect(passwordValidator('Test*123')).to.be.true;
    expect(passwordValidator('Test-123')).to.be.true;
  });

  it('should return false for passwords with only spaces', () => {
    expect(passwordValidator('        ')).to.be.false;
  });
});
