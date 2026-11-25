/**
 * Envelope-encryption fixtures: an HKDF KEK over a fixed test secret, an
 * in-memory data-key store, and a fake KMS for the managed seam.
 */
import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { HkdfKeyEncryptionKey } from '../../../../src/modules/agent-harness/adapters/crypto/hkdf-key-encryption-key';
import { KmsClient } from '../../../../src/modules/agent-harness/adapters/crypto/kms-key-encryption-key';
import { InMemoryDataKeyStore } from '../../../../src/modules/agent-harness/adapters/in-memory-data-key-store';
import { EnvelopeCipher } from '../../../../src/modules/agent-harness/application/envelope-cipher';
import { HarnessLogger } from '../../../../src/modules/agent-harness/application/harness-log';
import {
  OrgDataKeyOptions,
  OrgDataKeys,
} from '../../../../src/modules/agent-harness/application/org-data-keys';
import {
  DataKeyStore,
  KeyEncryptionKey,
} from '../../../../src/modules/agent-harness/ports/data-keys';
import { HmacRunKeyDigest } from '../../../../src/modules/agent-harness/domain/run-key-digest';

export const TEST_KEK_SECRET = Buffer.alloc(32, 7);

/** The run key digest under the test secret, as a deployment derives it from its own. */
export const testRunKeys = (): HmacRunKeyDigest => new HmacRunKeyDigest(() => TEST_KEK_SECRET);

export const testKek = (
  secret: Buffer = TEST_KEK_SECRET,
): HkdfKeyEncryptionKey => new HkdfKeyEncryptionKey(() => secret);

export interface DataKeyRing {
  store: DataKeyStore;
  keys: OrgDataKeys;
  cipher: EnvelopeCipher;
}

export function dataKeyRing(
  options: {
    store?: DataKeyStore;
    kek?: KeyEncryptionKey;
    logger?: HarnessLogger;
    now?: () => number;
    keyOptions?: Partial<OrgDataKeyOptions>;
  } = {},
): DataKeyRing {
  const store = options.store ?? new InMemoryDataKeyStore();
  const keys = new OrgDataKeys({
    store,
    kek: options.kek ?? testKek(),
    logger: options.logger,
    now: options.now,
    limits: options.keyOptions,
  });
  return { store, keys, cipher: new EnvelopeCipher(keys) };
}

let shared: DataKeyRing | null = null;

/** One key ring for a test process, so every service in a test shares its keys. */
export const testKeyRing = (): DataKeyRing => {
  shared ??= dataKeyRing();
  return shared;
};

export const testCipher = (): EnvelopeCipher => testKeyRing().cipher;

/** A KMS that keeps its keys to itself and authenticates the context. */
export class FakeKms implements KmsClient {
  private readonly keys = new Map<string, Buffer>();
  encrypts = 0;
  decrypts = 0;

  constructor(keyIds: readonly string[] = ['kms-key-1']) {
    for (const keyId of keyIds) this.keys.set(keyId, randomBytes(32));
  }

  encrypt(request: {
    keyId: string;
    plaintext: Buffer;
    context: Record<string, string>;
  }): Promise<Buffer> {
    this.encrypts += 1;
    const key = this.key(request.keyId);
    const iv = randomBytes(12);
    const cipher = createCipheriv('aes-256-gcm', key, iv);
    cipher.setAAD(canonicalJsonBytes(request.context));
    const ct = Buffer.concat([
      cipher.update(request.plaintext),
      cipher.final(),
    ]);
    return Promise.resolve(Buffer.concat([iv, cipher.getAuthTag(), ct]));
  }

  decrypt(request: {
    keyId: string;
    ciphertext: Buffer;
    context: Record<string, string>;
  }): Promise<Buffer> {
    this.decrypts += 1;
    try {
      const decipher = createDecipheriv(
        'aes-256-gcm',
        this.key(request.keyId),
        request.ciphertext.subarray(0, 12),
      );
      decipher.setAAD(canonicalJsonBytes(request.context));
      decipher.setAuthTag(request.ciphertext.subarray(12, 28));
      return Promise.resolve(
        Buffer.concat([
          decipher.update(request.ciphertext.subarray(28)),
          decipher.final(),
        ]),
      );
    } catch (error) {
      return Promise.reject(error as Error);
    }
  }

  private key(keyId: string): Buffer {
    const key = this.keys.get(keyId);
    if (key === undefined) throw new Error(`KMS key ${keyId} not found`);
    return key;
  }
}
