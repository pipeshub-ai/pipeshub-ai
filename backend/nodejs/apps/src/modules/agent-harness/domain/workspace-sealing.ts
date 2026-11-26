/**
 * The worker's sealed object format (D33, `harness_core.workspace.sealing`),
 * so Node can copy a session's objects under another session's key (SS3).
 * Byte-for-byte the Python construction: sub-keys by one HKDF-Expand block of
 * the session key, a synthetic nonce, AES-256-GCM with the scope, key id and
 * content hash as associated data. The shared vector
 * `contracts/harness/vectors/workspace-sealing.json` holds both sides to it.
 */
import { createCipheriv, createDecipheriv, createHmac } from 'node:crypto';

export const SEALED_MAGIC = Buffer.from('HWS1', 'ascii');
const NONCE_BYTES = 12;
const TAG_BYTES = 16;
export const SEALED_CONTENT_HASH_PREFIX = 'hmac-sha256:';
const SEALED_REF_PREFIX = 'sealed';

export interface SealingKey {
  keyId: string;
  key: Buffer;
}

/** Where an object lives: the org and the session's workspace partition (`ws:{sessionId}`). */
export interface SealingScope {
  tenantId: string;
  partition: string;
}

/** An object in the store under the seal: what the sealed id carries. */
export interface InnerObjectRef {
  objectId: string;
  contentHash: string;
  sizeBytes: number;
}

export type UnreadableReason =
  'malformed' | 'other_key' | 'authentication' | 'size' | 'not_sealed';

export class ObjectUnreadableError extends Error {
  constructor(readonly reason: UnreadableReason) {
    super(`Sealed object is unreadable (${reason})`);
    this.name = 'ObjectUnreadableError';
  }
}

const subkey = (key: SealingKey, label: string): Buffer =>
  createHmac('sha256', key.key)
    .update(Buffer.concat([Buffer.from(label, 'ascii'), Buffer.from([1])]))
    .digest();

export const sealedContentHash = (key: SealingKey, plaintext: Buffer): string =>
  SEALED_CONTENT_HASH_PREFIX +
  createHmac('sha256', subkey(key, 'content-hash'))
    .update(plaintext)
    .digest('hex');

const aad = (
  scope: SealingScope,
  key: SealingKey,
  contentHash: string,
): Buffer =>
  Buffer.from(
    [scope.tenantId, scope.partition, key.keyId, contentHash].join('\u0000'),
    'utf8',
  );

export interface Sealed {
  /** What the inner store keeps. */
  stored: Buffer;
  contentHash: string;
  sizeBytes: number;
}

export function sealObject(
  scope: SealingScope,
  key: SealingKey,
  plaintext: Buffer,
): Sealed {
  const contentHash = sealedContentHash(key, plaintext);
  const nonce = createHmac('sha256', subkey(key, 'nonce'))
    .update(plaintext)
    .digest()
    .subarray(0, NONCE_BYTES);
  const cipher = createCipheriv('aes-256-gcm', subkey(key, 'encrypt'), nonce);
  cipher.setAAD(aad(scope, key, contentHash));
  const body = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const kid = Buffer.from(key.keyId, 'utf8');
  return {
    stored: Buffer.concat([
      SEALED_MAGIC,
      Buffer.from([kid.length]),
      kid,
      nonce,
      body,
      cipher.getAuthTag(),
    ]),
    contentHash,
    sizeBytes: plaintext.length,
  };
}

export function openObject(
  scope: SealingScope,
  key: SealingKey,
  stored: Buffer,
  ref: { contentHash: string; sizeBytes: number },
): Buffer {
  const header = SEALED_MAGIC.length + 1;
  if (
    stored.length < header ||
    !stored.subarray(0, SEALED_MAGIC.length).equals(SEALED_MAGIC)
  ) {
    throw new ObjectUnreadableError('malformed');
  }
  const kidEnd = header + stored.readUInt8(header - 1);
  if (stored.length < kidEnd + NONCE_BYTES + TAG_BYTES) {
    throw new ObjectUnreadableError('malformed');
  }
  if (!stored.subarray(header, kidEnd).equals(Buffer.from(key.keyId, 'utf8'))) {
    throw new ObjectUnreadableError('other_key');
  }
  const nonce = stored.subarray(kidEnd, kidEnd + NONCE_BYTES);
  const body = stored.subarray(kidEnd + NONCE_BYTES, stored.length - TAG_BYTES);
  const decipher = createDecipheriv(
    'aes-256-gcm',
    subkey(key, 'encrypt'),
    nonce,
  );
  decipher.setAAD(aad(scope, key, ref.contentHash));
  decipher.setAuthTag(stored.subarray(stored.length - TAG_BYTES));
  let plaintext: Buffer;
  try {
    plaintext = Buffer.concat([decipher.update(body), decipher.final()]);
  } catch {
    throw new ObjectUnreadableError('authentication');
  }
  if (plaintext.length !== ref.sizeBytes) {
    throw new ObjectUnreadableError('size');
  }
  return plaintext;
}

/** The inner ref goes last: its id is opaque and may contain `/`. */
export const encodeSealedId = (inner: InnerObjectRef): string =>
  `${SEALED_REF_PREFIX}/${String(inner.sizeBytes)}/${inner.contentHash}/${inner.objectId}`;

export function decodeSealedId(objectId: string): InnerObjectRef {
  const [prefix, size, contentHash, ...inner] = objectId.split('/');
  if (
    prefix !== SEALED_REF_PREFIX ||
    size === undefined ||
    !/^\d+$/.test(size) ||
    contentHash === undefined ||
    inner.length === 0
  ) {
    throw new ObjectUnreadableError('not_sealed');
  }
  return {
    sizeBytes: Number(size),
    contentHash,
    objectId: inner.join('/'),
  };
}
