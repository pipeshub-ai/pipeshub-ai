import 'reflect-metadata';
import { expect } from 'chai';
import { EncryptionService } from '../../../src/libs/services/encryption.service';
import {
  DecryptionError,
  InvalidInputError,
} from '../../../src/libs/errors/encryption.errors';

describe('EncryptionService', () => {
  let service: EncryptionService;
  const testKey = 'test-encryption-key-for-unit-tests';

  beforeEach(() => {
    service = new EncryptionService(testKey);
  });

  describe('constructor', () => {
    it('should create an instance with a valid key', () => {
      expect(service).to.be.instanceOf(EncryptionService);
    });

    it('should throw InvalidInputError when key is empty', () => {
      expect(() => new EncryptionService('')).to.throw(InvalidInputError, 'Encryption key not provided');
    });
  });

  describe('encrypt', () => {
    it('should encrypt a string and return encryptedData, iv, and tag', () => {
      const result = service.encrypt('hello world');
      expect(result).to.have.property('encryptedData').that.is.a('string');
      expect(result).to.have.property('iv').that.is.a('string');
      expect(result).to.have.property('tag').that.is.a('string');
      expect(result.encryptedData).to.not.equal('hello world');
    });

    it('should produce different ciphertext for the same plaintext (random IV)', () => {
      const result1 = service.encrypt('same text');
      const result2 = service.encrypt('same text');
      expect(result1.encryptedData).to.not.equal(result2.encryptedData);
      expect(result1.iv).to.not.equal(result2.iv);
    });

    it('should throw InvalidInputError when data is empty', () => {
      expect(() => service.encrypt('')).to.throw(InvalidInputError, 'Data to encrypt cannot be empty');
    });

    it('should encrypt long strings', () => {
      const longString = 'a'.repeat(10000);
      const result = service.encrypt(longString);
      expect(result.encryptedData).to.be.a('string');
      expect(result.encryptedData.length).to.be.greaterThan(0);
    });

    it('should encrypt strings with special characters', () => {
      const specialChars = '!@#$%^&*()_+-=[]{}|;:,.<>?/~`\n\t\r';
      const result = service.encrypt(specialChars);
      expect(result.encryptedData).to.be.a('string');
    });

    it('should encrypt unicode strings', () => {
      const unicode = 'Hello \u4e16\u754c \ud83c\udf0d \u00e9\u00e8\u00ea';
      const result = service.encrypt(unicode);
      expect(result.encryptedData).to.be.a('string');
    });

    it('should wrap non-EncryptionError exceptions in EncryptionError', () => {
      // Stub crypto.randomBytes to force an unexpected error inside encrypt
      const crypto = require('crypto');
      const originalRandomBytes = crypto.randomBytes;
      crypto.randomBytes = () => { throw new Error('RNG failure'); };
      try {
        expect(() => service.encrypt('test data')).to.throw('RNG failure');
      } finally {
        crypto.randomBytes = originalRandomBytes;
      }
    });
  });

  describe('decrypt', () => {
    it('should decrypt an encrypted string back to original', () => {
      const plaintext = 'hello world';
      const encrypted = service.encrypt(plaintext);
      const decrypted = service.decrypt(encrypted.encryptedData, encrypted.iv, encrypted.tag);
      expect(decrypted).to.equal(plaintext);
    });

    it('should decrypt long strings correctly', () => {
      const longString = 'a'.repeat(10000);
      const encrypted = service.encrypt(longString);
      const decrypted = service.decrypt(encrypted.encryptedData, encrypted.iv, encrypted.tag);
      expect(decrypted).to.equal(longString);
    });

    it('should decrypt unicode strings correctly', () => {
      const unicode = 'Hello \u4e16\u754c \ud83c\udf0d';
      const encrypted = service.encrypt(unicode);
      const decrypted = service.decrypt(encrypted.encryptedData, encrypted.iv, encrypted.tag);
      expect(decrypted).to.equal(unicode);
    });

    it('should throw InvalidInputError when encryptedData is empty', () => {
      expect(() => service.decrypt('', 'iv', 'tag')).to.throw(InvalidInputError, 'Missing required decryption parameters');
    });

    it('should throw InvalidInputError when iv is empty', () => {
      expect(() => service.decrypt('data', '', 'tag')).to.throw(InvalidInputError, 'Missing required decryption parameters');
    });

    it('should throw InvalidInputError when tag is empty', () => {
      expect(() => service.decrypt('data', 'iv', '')).to.throw(InvalidInputError, 'Missing required decryption parameters');
    });

    it('should throw DecryptionError when encryptedData is tampered', () => {
      const encrypted = service.encrypt('hello');
      expect(() => service.decrypt('tampered' + encrypted.encryptedData, encrypted.iv, encrypted.tag)).to.throw(DecryptionError);
    });

    it('should throw DecryptionError when iv is wrong', () => {
      const encrypted = service.encrypt('hello');
      // Use a valid base64 string but wrong IV
      const wrongIv = Buffer.from('wrongivvalue!').toString('base64');
      expect(() => service.decrypt(encrypted.encryptedData, wrongIv, encrypted.tag)).to.throw(DecryptionError);
    });

    it('should throw DecryptionError when tag is wrong', () => {
      const encrypted = service.encrypt('hello');
      const wrongTag = Buffer.from('wrongtagvalue!!!').toString('base64');
      expect(() => service.decrypt(encrypted.encryptedData, encrypted.iv, wrongTag)).to.throw(DecryptionError);
    });
  });

  describe('encrypt-decrypt roundtrip', () => {
    it('should handle various data types serialized as strings', () => {
      const testCases = [
        'simple string',
        JSON.stringify({ key: 'value', nested: { a: 1 } }),
        JSON.stringify([1, 2, 3]),
        '12345',
        'true',
        'null',
      ];

      for (const testCase of testCases) {
        const encrypted = service.encrypt(testCase);
        const decrypted = service.decrypt(encrypted.encryptedData, encrypted.iv, encrypted.tag);
        expect(decrypted).to.equal(testCase);
      }
    });
  });

  describe('different keys', () => {
    it('should not decrypt data encrypted with a different key', () => {
      const service1 = new EncryptionService('key-one-for-encryption-tests!!!');
      const service2 = new EncryptionService('key-two-for-encryption-tests!!!');
      const encrypted = service1.encrypt('secret data');
      expect(() => service2.decrypt(encrypted.encryptedData, encrypted.iv, encrypted.tag)).to.throw(DecryptionError);
    });
  });
});
