import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import {
  EncryptionError,
  KeyGenerationError,
  DecryptionError,
  EncryptionKeyError,
  InvalidKeyFormatError,
  AlgorithmError,
  InvalidInputError,
  PaddingError,
  KeyExpirationError,
} from '../../../src/libs/errors/encryption.errors';

describe('Encryption Errors', () => {
  describe('EncryptionError', () => {
    it('should have correct name', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 500);
      expect(error.name).to.equal('EncryptionError');
    });

    it('should have correct code with ENCRYPTION_ prefix', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 500);
      expect(error.code).to.equal('ENCRYPTION_CUSTOM');
    });

    it('should have correct statusCode', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 400);
      expect(error.statusCode).to.equal(400);
    });

    it('should default statusCode to 500', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 500);
      expect(error.message).to.equal('Encryption failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 500);
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 500);
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { algorithm: 'AES-256' };
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 500, metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 500);
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new EncryptionError('CUSTOM', 'Encryption failed', 500);
      const json = error.toJSON();
      expect(json).to.have.property('name', 'EncryptionError');
      expect(json).to.have.property('code', 'ENCRYPTION_CUSTOM');
      expect(json).to.have.property('statusCode', 500);
      expect(json).to.have.property('message', 'Encryption failed');
    });
  });

  describe('KeyGenerationError', () => {
    it('should have correct name', () => {
      const error = new KeyGenerationError('Key generation failed');
      expect(error.name).to.equal('KeyGenerationError');
    });

    it('should have correct code', () => {
      const error = new KeyGenerationError('Key generation failed');
      expect(error.code).to.equal('ENCRYPTION_KEY_GENERATION_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new KeyGenerationError('Key generation failed');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new KeyGenerationError('Key generation failed');
      expect(error.message).to.equal('Key generation failed');
    });

    it('should be instanceof EncryptionError', () => {
      const error = new KeyGenerationError('Key generation failed');
      expect(error).to.be.an.instanceOf(EncryptionError);
    });

    it('should be instanceof BaseError', () => {
      const error = new KeyGenerationError('Key generation failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { keySize: 256 };
      const error = new KeyGenerationError('Key generation failed', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new KeyGenerationError('Key generation failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new KeyGenerationError('Key generation failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'KeyGenerationError');
      expect(json).to.have.property('code', 'ENCRYPTION_KEY_GENERATION_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('DecryptionError', () => {
    it('should have correct name', () => {
      const error = new DecryptionError('Decryption failed');
      expect(error.name).to.equal('DecryptionError');
    });

    it('should have correct code', () => {
      const error = new DecryptionError('Decryption failed');
      expect(error.code).to.equal('ENCRYPTION_DECRYPTION_ERROR');
    });

    it('should have correct statusCode of 400', () => {
      const error = new DecryptionError('Decryption failed');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new DecryptionError('Decryption failed');
      expect(error.message).to.equal('Decryption failed');
    });

    it('should be instanceof EncryptionError', () => {
      const error = new DecryptionError('Decryption failed');
      expect(error).to.be.an.instanceOf(EncryptionError);
    });

    it('should be instanceof BaseError', () => {
      const error = new DecryptionError('Decryption failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { reason: 'wrong key' };
      const error = new DecryptionError('Decryption failed', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new DecryptionError('Decryption failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new DecryptionError('Decryption failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'DecryptionError');
      expect(json).to.have.property('code', 'ENCRYPTION_DECRYPTION_ERROR');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('EncryptionKeyError', () => {
    it('should have correct name', () => {
      const error = new EncryptionKeyError('Invalid key');
      expect(error.name).to.equal('EncryptionKeyError');
    });

    it('should have correct code', () => {
      const error = new EncryptionKeyError('Invalid key');
      expect(error.code).to.equal('ENCRYPTION_KEY_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new EncryptionKeyError('Invalid key');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new EncryptionKeyError('Invalid key');
      expect(error.message).to.equal('Invalid key');
    });

    it('should be instanceof EncryptionError', () => {
      const error = new EncryptionKeyError('Invalid key');
      expect(error).to.be.an.instanceOf(EncryptionError);
    });

    it('should be instanceof BaseError', () => {
      const error = new EncryptionKeyError('Invalid key');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { keyId: 'key-abc' };
      const error = new EncryptionKeyError('Invalid key', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new EncryptionKeyError('Invalid key');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new EncryptionKeyError('Invalid key');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'EncryptionKeyError');
      expect(json).to.have.property('code', 'ENCRYPTION_KEY_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('InvalidKeyFormatError', () => {
    it('should have correct name', () => {
      const error = new InvalidKeyFormatError('Key format is invalid');
      expect(error.name).to.equal('InvalidKeyFormatError');
    });

    it('should have correct code', () => {
      const error = new InvalidKeyFormatError('Key format is invalid');
      expect(error.code).to.equal('ENCRYPTION_INVALID_KEY_FORMAT');
    });

    it('should have correct statusCode of 400', () => {
      const error = new InvalidKeyFormatError('Key format is invalid');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new InvalidKeyFormatError('Key format is invalid');
      expect(error.message).to.equal('Key format is invalid');
    });

    it('should be instanceof EncryptionError', () => {
      const error = new InvalidKeyFormatError('Key format is invalid');
      expect(error).to.be.an.instanceOf(EncryptionError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidKeyFormatError('Key format is invalid');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { expectedFormat: 'PEM', receivedFormat: 'DER' };
      const error = new InvalidKeyFormatError('Key format is invalid', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InvalidKeyFormatError('Key format is invalid');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InvalidKeyFormatError('Key format is invalid');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InvalidKeyFormatError');
      expect(json).to.have.property('code', 'ENCRYPTION_INVALID_KEY_FORMAT');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('AlgorithmError', () => {
    it('should have correct name', () => {
      const error = new AlgorithmError('Unsupported algorithm');
      expect(error.name).to.equal('AlgorithmError');
    });

    it('should have correct code', () => {
      const error = new AlgorithmError('Unsupported algorithm');
      expect(error.code).to.equal('ENCRYPTION_ALGORITHM_ERROR');
    });

    it('should have correct statusCode of 500', () => {
      const error = new AlgorithmError('Unsupported algorithm');
      expect(error.statusCode).to.equal(500);
    });

    it('should preserve error message', () => {
      const error = new AlgorithmError('Unsupported algorithm');
      expect(error.message).to.equal('Unsupported algorithm');
    });

    it('should be instanceof EncryptionError', () => {
      const error = new AlgorithmError('Unsupported algorithm');
      expect(error).to.be.an.instanceOf(EncryptionError);
    });

    it('should be instanceof BaseError', () => {
      const error = new AlgorithmError('Unsupported algorithm');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { algorithm: 'ROT13' };
      const error = new AlgorithmError('Unsupported algorithm', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new AlgorithmError('Unsupported algorithm');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new AlgorithmError('Unsupported algorithm');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'AlgorithmError');
      expect(json).to.have.property('code', 'ENCRYPTION_ALGORITHM_ERROR');
      expect(json).to.have.property('statusCode', 500);
    });
  });

  describe('InvalidInputError', () => {
    it('should have correct name', () => {
      const error = new InvalidInputError('Invalid input data');
      expect(error.name).to.equal('InvalidInputError');
    });

    it('should have correct code', () => {
      const error = new InvalidInputError('Invalid input data');
      expect(error.code).to.equal('ENCRYPTION_INVALID_INPUT');
    });

    it('should have correct statusCode of 400', () => {
      const error = new InvalidInputError('Invalid input data');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new InvalidInputError('Invalid input data');
      expect(error.message).to.equal('Invalid input data');
    });

    it('should be instanceof EncryptionError', () => {
      const error = new InvalidInputError('Invalid input data');
      expect(error).to.be.an.instanceOf(EncryptionError);
    });

    it('should be instanceof BaseError', () => {
      const error = new InvalidInputError('Invalid input data');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { inputLength: 0 };
      const error = new InvalidInputError('Invalid input data', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new InvalidInputError('Invalid input data');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new InvalidInputError('Invalid input data');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'InvalidInputError');
      expect(json).to.have.property('code', 'ENCRYPTION_INVALID_INPUT');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('PaddingError', () => {
    it('should have correct name', () => {
      const error = new PaddingError('Padding is invalid');
      expect(error.name).to.equal('PaddingError');
    });

    it('should have correct code', () => {
      const error = new PaddingError('Padding is invalid');
      expect(error.code).to.equal('ENCRYPTION_PADDING_ERROR');
    });

    it('should have correct statusCode of 400', () => {
      const error = new PaddingError('Padding is invalid');
      expect(error.statusCode).to.equal(400);
    });

    it('should preserve error message', () => {
      const error = new PaddingError('Padding is invalid');
      expect(error.message).to.equal('Padding is invalid');
    });

    it('should be instanceof EncryptionError', () => {
      const error = new PaddingError('Padding is invalid');
      expect(error).to.be.an.instanceOf(EncryptionError);
    });

    it('should be instanceof BaseError', () => {
      const error = new PaddingError('Padding is invalid');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { paddingScheme: 'PKCS7' };
      const error = new PaddingError('Padding is invalid', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new PaddingError('Padding is invalid');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new PaddingError('Padding is invalid');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'PaddingError');
      expect(json).to.have.property('code', 'ENCRYPTION_PADDING_ERROR');
      expect(json).to.have.property('statusCode', 400);
    });
  });

  describe('KeyExpirationError', () => {
    it('should have correct name', () => {
      const error = new KeyExpirationError('Key has expired');
      expect(error.name).to.equal('KeyExpirationError');
    });

    it('should have correct code', () => {
      const error = new KeyExpirationError('Key has expired');
      expect(error.code).to.equal('ENCRYPTION_KEY_EXPIRED');
    });

    it('should have correct statusCode of 401', () => {
      const error = new KeyExpirationError('Key has expired');
      expect(error.statusCode).to.equal(401);
    });

    it('should preserve error message', () => {
      const error = new KeyExpirationError('Key has expired');
      expect(error.message).to.equal('Key has expired');
    });

    it('should be instanceof EncryptionError', () => {
      const error = new KeyExpirationError('Key has expired');
      expect(error).to.be.an.instanceOf(EncryptionError);
    });

    it('should be instanceof BaseError', () => {
      const error = new KeyExpirationError('Key has expired');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should accept metadata', () => {
      const metadata = { expiredAt: '2024-01-01T00:00:00Z' };
      const error = new KeyExpirationError('Key has expired', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should have a stack trace', () => {
      const error = new KeyExpirationError('Key has expired');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new KeyExpirationError('Key has expired');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'KeyExpirationError');
      expect(json).to.have.property('code', 'ENCRYPTION_KEY_EXPIRED');
      expect(json).to.have.property('statusCode', 401);
    });
  });
});
