import crypto from 'crypto';
import {
  DecryptionError,
  EncryptionError,
  InvalidKeyFormatError,
} from '../errors/encryption.errors';
import { Logger } from '../services/logger.service';

const logger = Logger.getInstance({
  service: 'Encryption Service',
});

export class EncryptionService {
  private static instance: EncryptionService;
  private algorithm: string;
  private secretKey: string;

  private constructor(algorithm: string, secretKey: string) {
    this.algorithm = algorithm;
    this.secretKey = secretKey;
  }

  static getInstance(algorithm: string, secretKey: string): EncryptionService {
    if (!EncryptionService.instance) {
      EncryptionService.instance = new EncryptionService(algorithm, secretKey);
    }
    return EncryptionService.instance;
  }

  public encrypt(text: string): string {
    try {
      // Recommended IV length for GCM is 12 bytes
      const iv = crypto.randomBytes(12);
      const cipher = crypto.createCipheriv(
        this.algorithm,
        Buffer.from(this.secretKey, 'hex'),
        iv,
      ) as crypto.CipherGCM;
      const encrypted = Buffer.concat([
        cipher.update(text, 'utf8'),
        cipher.final(),
      ]);
      // Get the authentication tag from GCM
      const authTag = cipher.getAuthTag();

      // Return the encrypted string as "iv:ciphertext:authTag"
      return (
        iv.toString('hex') +
        ':' +
        encrypted.toString('hex') +
        ':' +
        authTag.toString('hex')
      );
    } catch (error: any) {
      logger.error('Encryption failed', error.message);
      throw new EncryptionError('Encryption failed', error.message);
    }
  }

  public decrypt(encryptedText: string | null | undefined): string {
    if (encryptedText === null || encryptedText === undefined) {
      throw new DecryptionError(
        'Decryption failed, could be due to null or undefined encrypted text',
      );
    }

    try {
      // For AES-256-GCM, expect format "iv:ciphertext:authTag"
      const textParts = encryptedText.split(':');
      if (textParts.length !== 3) {
        throw new InvalidKeyFormatError(
          'Invalid encrypted text format; expected format iv:ciphertext:authTag',
        );
      }
      const iv = Buffer.from(textParts[0] as string, 'hex');
      const encryptedData = Buffer.from(textParts[1] as string, 'hex');
      const authTag = Buffer.from(textParts[2] as string, 'hex');

      const decipher = crypto.createDecipheriv(
        this.algorithm,
        Buffer.from(this.secretKey, 'hex'),
        iv,
      ) as crypto.DecipherGCM;
      // Set the authentication tag for GCM mode
      decipher.setAuthTag(authTag);

      const decrypted = Buffer.concat([
        decipher.update(encryptedData),
        decipher.final(),
      ]);
      return decrypted.toString('utf8');
    } catch (exception: any) {
      throw new DecryptionError(
        'Decryption failed, could be due to different encryption algorithm or secret key',
        exception.message,
      );
    }
  }
}
