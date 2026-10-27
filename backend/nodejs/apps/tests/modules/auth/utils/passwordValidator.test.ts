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

  describe('the bcrypt 72-byte limit', () => {
    // bcrypt hashes only the first 72 bytes and silently ignores the rest,
    // so anything longer would be stored truncated and match every input
    // that shares its first 72 bytes.
    it('accepts exactly 72 bytes', () => {
      expect(passwordValidator('Aa1!' + 'x'.repeat(68))).to.be.true;
    });

    it('rejects 73 bytes even when every complexity rule is met', () => {
      expect(passwordValidator('Aa1!' + 'x'.repeat(69))).to.be.false;
    });

    it('rejects a password whose required characters all sit past byte 72', () => {
      // The regex would pass this; bcrypt would hash only the 72 lowercase
      // letters in front of it.
      expect(passwordValidator('a'.repeat(72) + 'A1!')).to.be.false;
    });

    it('counts bytes, not characters', () => {
      // 20 four-byte characters are 80 bytes.
      expect(passwordValidator('Aa1!' + '\u{1F600}'.repeat(20))).to.be.false;
    });
  });
});
