import {
  createCipheriv,
  createDecipheriv,
  createHmac,
  hkdfSync,
  randomBytes,
} from 'node:crypto';
import { canonicalJsonBytes } from '../../contracts/canonical-json';
import {
  DATA_KEY_BYTES,
  SEAL_ALGORITHM,
  SEAL_IV_BYTES,
  SEAL_TAG_BYTES,
} from '../../domain/data-protection';
import { SealedContentError } from '../../domain/errors';
import {
  DataKeyContext,
  KeyEncryptionKey,
  KeyEncryptionKeyRing,
} from '../../ports/data-keys';
import { getHashedSecretKeyRing } from '../../../configuration_manager/config/config';

// Domain separation: the configuration cipher uses the same secret directly
// as its AES key; the KEK is an independent HKDF output of it.
const KEK_SALT = Buffer.from('pipeshub/agent-harness/kek', 'utf8');
const KEK_INFO = Buffer.from('journal-data-key-wrap/v1', 'utf8');

const wrapAad = (context: DataKeyContext): Buffer =>
  canonicalJsonBytes({
    purpose: 'data-key',
    orgId: context.orgId,
    kid: context.kid,
    kv: context.version,
  });

/**
 * Self-hosted KEK: HKDF-SHA256 of the deployment secret (SECRET_KEY, already
 * required by the platform), so no operator secret is added. Derived on first
 * use; the secret is read through the configuration the platform loads.
 */
export class HkdfKeyEncryptionKey implements KeyEncryptionKey {
  private derived: { key: Buffer; id: string } | null = null;

  constructor(private readonly secret: () => Buffer) {}

  get id(): string {
    return this.material().id;
  }

  wrap(dataKey: Buffer, context: DataKeyContext): Promise<string> {
    const iv = randomBytes(SEAL_IV_BYTES);
    const cipher = createCipheriv(SEAL_ALGORITHM, this.material().key, iv);
    cipher.setAAD(wrapAad(context));
    const ct = Buffer.concat([cipher.update(dataKey), cipher.final()]);
    return Promise.resolve(
      Buffer.concat([iv, cipher.getAuthTag(), ct]).toString('base64'),
    );
  }

  unwrap(wrapped: string, context: DataKeyContext): Promise<Buffer> {
    const bytes = Buffer.from(wrapped, 'base64');
    const ctAt = SEAL_IV_BYTES + SEAL_TAG_BYTES;
    try {
      if (bytes.length !== ctAt + DATA_KEY_BYTES) throw new Error('length');
      const decipher = createDecipheriv(
        SEAL_ALGORITHM,
        this.material().key,
        bytes.subarray(0, SEAL_IV_BYTES),
      );
      decipher.setAAD(wrapAad(context));
      decipher.setAuthTag(bytes.subarray(SEAL_IV_BYTES, ctAt));
      return Promise.resolve(
        Buffer.concat([
          decipher.update(bytes.subarray(ctAt)),
          decipher.final(),
        ]),
      );
    } catch {
      return Promise.reject(
        new SealedContentError(
          'unwrap_failed',
          'The data key does not unwrap with this key-encryption key',
        ),
      );
    }
  }

  private material(): { key: Buffer; id: string } {
    if (this.derived === null) {
      const key = Buffer.from(
        hkdfSync('sha256', this.secret(), KEK_SALT, KEK_INFO, DATA_KEY_BYTES),
      );
      // A fingerprint, not the key: tells two KEKs apart without revealing either.
      const id = `hkdf:${createHmac('sha256', key).update('kek-id').digest('hex').slice(0, 16)}`;
      this.derived = { key, id };
    }
    return this.derived;
  }
}

/**
 * KEKs as a ring (ADR-KEY-01): new wraps under the first member, a stored
 * wrap opened by the member its `kekId` names. A wrap under a key the ring
 * does not hold fails closed, naming what to check, never a key.
 */
export class RingKeyEncryptionKey implements KeyEncryptionKeyRing {
  private resolved: readonly KeyEncryptionKey[] | null = null;

  constructor(private readonly load: () => readonly KeyEncryptionKey[]) {}

  get id(): string {
    return this.current().id;
  }

  get ids(): readonly string[] {
    return this.members().map((member) => member.id);
  }

  wrap(dataKey: Buffer, context: DataKeyContext): Promise<string> {
    return this.current().wrap(dataKey, context);
  }

  unwrap(
    wrapped: string,
    context: DataKeyContext,
    kekId?: string,
  ): Promise<Buffer> {
    const member =
      kekId === undefined
        ? this.current()
        : this.members().find((candidate) => candidate.id === kekId);
    if (member === undefined) {
      return Promise.reject(
        new SealedContentError(
          'unwrap_failed',
          'The data key was wrapped under a key that is neither SECRET_KEY nor SECRET_KEY_PREVIOUS',
        ),
      );
    }
    return member.unwrap(wrapped, context);
  }

  private current(): KeyEncryptionKey {
    const [current] = this.members();
    if (current === undefined) throw new Error('A KEK ring has no key');
    return current;
  }

  private members(): readonly KeyEncryptionKey[] {
    this.resolved ??= this.load();
    return this.resolved;
  }
}

/**
 * The KEK of a self-hosted deployment, from the platform's SECRET_KEY, with
 * the ring's other key (SECRET_KEY_PREVIOUS or SECRET_KEY_NEXT) for unwraps.
 */
export const platformKeyEncryptionKey = (): RingKeyEncryptionKey =>
  new RingKeyEncryptionKey(() =>
    getHashedSecretKeyRing().map(
      (hex) => new HkdfKeyEncryptionKey(() => Buffer.from(hex, 'hex')),
    ),
  );
