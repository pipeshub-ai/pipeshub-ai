import crypto from 'crypto';
import {
  DecryptionError,
  EncryptionError,
  InvalidKeyFormatError,
} from '../errors/encryption.errors';
import { Logger } from '../services/logger.service';
import { cipherRing, secretKeyRing } from './secret-key-ring';

const logger = Logger.getInstance({
  service: 'Encryption Service',
});

export class EncryptionService {
  private static instance: EncryptionService;
  private algorithm: string;
  private secretKey: string;
  /** Every key `decrypt` tries, the writer's first (ADR-KEY-01). */
  private readKeys: readonly string[];

  private constructor(
    algorithm: string,
    secretKey: string,
    readKeys: readonly string[] = [secretKey],
  ) {
    this.algorithm = algorithm;
    this.secretKey = secretKey;
    this.readKeys = readKeys;
  }

  /**
   * The platform cipher. Given the deployment's own key it also reads under
   * the ring's other key (`SECRET_KEY_PREVIOUS` or `SECRET_KEY_NEXT`), so a
   * value written before or after a key switch still opens; it writes only
   * under `secretKey`.
   */
  static getInstance(algorithm: string, secretKey: string): EncryptionService {
    if (!EncryptionService.instance) {
      const keys = platformReadKeys(secretKey);
      // Given the old SHA-256 key of the deployment's secret, it still writes under the HKDF one (SEC-D-05).
      EncryptionService.instance = new EncryptionService(
        algorithm,
        keys[0] as string,
        keys,
      );
    }
    return EncryptionService.instance;
  }

  /**
   * A cipher for one key (a vault data key), apart from the process-wide
   * instance: `getInstance` answers the first key it was ever given.
   */
  static withKey(algorithm: string, secretKey: string): EncryptionService {
    return new EncryptionService(algorithm, secretKey);
  }

  /** A cipher over a ring of keys: the first writes, every one reads (ADR-KEY-01). */
  static withRing(
    algorithm: string,
    keys: readonly [string, ...string[]],
  ): EncryptionService {
    return new EncryptionService(algorithm, keys[0], keys);
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
    return this.decryptDetailed(encryptedText).plaintext;
  }

  /** The plaintext, and whether the writer's key opened it (false: the ring's other key did). */
  public decryptDetailed(encryptedText: string | null | undefined): {
    plaintext: string;
    current: boolean;
  } {
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

      let failure: unknown;
      for (const [index, key] of this.readKeys.entries()) {
        try {
          const decipher = crypto.createDecipheriv(
            this.algorithm,
            Buffer.from(key, 'hex'),
            iv,
          ) as crypto.DecipherGCM;
          // GCM authenticates: a value sealed under another key fails here, never decrypts to garbage.
          decipher.setAuthTag(authTag);
          const decrypted = Buffer.concat([
            decipher.update(encryptedData),
            decipher.final(),
          ]);
          return {
            plaintext: decrypted.toString('utf8'),
            current: index === 0,
          };
        } catch (error) {
          failure = error;
        }
      }
      throw failure;
    } catch (exception: any) {
      throw new DecryptionError(
        'Decryption failed, could be due to different encryption algorithm or secret key',
        exception.message,
      );
    }
  }
}

/**
 * The ring's cipher keys, the writer's first, when `secretKey` is the
 * deployment's own (its HKDF key, or the SHA-256 key a caller from before
 * SEC-D-05 derives); any other key reads alone.
 */
function platformReadKeys(secretKey: string): readonly string[] {
  let ring: string[];
  try {
    ring = cipherRing(secretKeyRing());
  } catch {
    // No usable ring here; the caller's key was not derived from it.
    return [secretKey];
  }
  return ring.slice(0, 2).includes(secretKey) ? ring : [secretKey];
}
