import crypto from "crypto";
import { inject, injectable } from "inversify";
import {
  DecryptionError,
  EncryptionError,
  InvalidInputError,
} from "../errors/encryption.errors";

@injectable()
export class EncryptionService {
  private readonly algorithm = "aes-256-gcm";
  private readonly keyBuffer: Buffer;
  private readonly keyLength = 32; // 256 bits
  private readonly ivLength = 12;
  private readonly saltLength = 16;

  constructor(@inject("ENCRYPTION_KEY") key: string) {
    if (!key) {
      throw new InvalidInputError("Encryption key not provided");
    }
    this.keyBuffer = this.deriveKey(key);
  }

  private deriveKey(key: string): Buffer {
    const salt = crypto.randomBytes(this.saltLength);
    return crypto.pbkdf2Sync(key, salt, 100000, this.keyLength, "sha256");
  }

  encrypt(data: string): {
    encryptedData: string;
    iv: string;
    tag: string;
  } {
    try {
      if (!data) {
        throw new InvalidInputError("Data to encrypt cannot be empty");
      }

      const iv = crypto.randomBytes(this.ivLength);
      const cipher = crypto.createCipheriv(this.algorithm, this.keyBuffer, iv);

      let encrypted = cipher.update(data, "utf8", "base64");
      encrypted += cipher.final("base64");

      return {
        encryptedData: encrypted,
        iv: iv.toString("base64"),
        tag: cipher.getAuthTag().toString("base64"),
      };
    } catch (error) {
      if (error instanceof EncryptionError) {
        throw error;
      }
      throw new EncryptionError(
        "ENCRYPTION_FAILED",
        error instanceof Error ? error.message : "Unknown error"
      );
    }
  }

  decrypt(encryptedData: string, iv: string, tag: string): string {
    try {
      if (!encryptedData || !iv || !tag) {
        throw new InvalidInputError("Missing required decryption parameters");
      }

      const decipher = crypto.createDecipheriv(
        this.algorithm,
        this.keyBuffer,
        Buffer.from(iv, "base64")
      );

      decipher.setAuthTag(Buffer.from(tag, "base64"));

      let decrypted = decipher.update(encryptedData, "base64", "utf8");
      decrypted += decipher.final("utf8");

      return decrypted;
    } catch (error) {
      if (error instanceof EncryptionError) {
        throw error;
      }
      throw new DecryptionError(
        error instanceof Error ? error.message : "Unknown error"
      );
    }
  }
}
