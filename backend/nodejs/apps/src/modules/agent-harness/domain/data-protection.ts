import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { canonicalJsonBytes } from '../contracts/canonical-json';
import { SealedContentError } from './errors';

/**
 * Envelope encryption of journal content (REPORT 6.16): a random data key
 * (DEK) per org and version seals content with AES-256-GCM; the DEK is stored
 * only wrapped by the key-encryption key (KEK). Readers refuse a sealed
 * format version they do not know, so a mixed fleet fails closed.
 */
export const SEALED_FORMAT_VERSION = 1;
export const SEAL_ALGORITHM = 'aes-256-gcm';
export const DATA_KEY_BYTES = 32;
export const SEAL_IV_BYTES = 12;
export const SEAL_TAG_BYTES = 16;

/** A shredded key stops being usable on every replica within this bound. */
export const DATA_KEY_CACHE_TTL_MS = 5 * 60 * 1000;
export const DATA_KEY_CACHE_MAX_ORGS = 1_000;
/** New writes move to a fresh data key after this age; older versions stay readable. */
export const DATA_KEY_ROTATE_AFTER_MS = 30 * 24 * 60 * 60 * 1000;
/**
 * AES-GCM with random 96-bit IVs stays within NIST's collision bound up to
 * 2^32 seals per key; rotating at 2^28 keeps a wide margin.
 */
export const DATA_KEY_ROTATE_AFTER_SEALS = 2 ** 28;
/**
 * Seals a replica reserves per counter update. Reserved seals count whether
 * used or not, so a crash only over-counts and the bound stays conservative.
 */
export const DATA_KEY_SEAL_RESERVATION = 1_024;

/** What a ciphertext is for; bound into its AAD so it cannot be reused elsewhere. */
export type SealPurpose =
  | 'journal.data'
  | 'journal.payload'
  | 'content'
  /** A session workspace's file list (P3.2). */
  | 'workspace.manifest'
  /** A session workspace's data key, wrapped by the org key (D33). */
  | 'workspace.key'
  /** A run's typed query in its run document and receipts (LCFIX). */
  | 'run.input';

/** Envelope fields a ciphertext is bound to (all known before the append). */
export type SealContext = Readonly<Record<string, string | number>>;

export interface DataKeyRef {
  kid: string;
  version: number;
}

export interface DataKey extends DataKeyRef {
  orgId: string;
  key: Buffer;
  createdAt: number;
}

/** A data key as stored: only its KEK-wrapped form. */
export interface WrappedDataKey extends DataKeyRef {
  orgId: string;
  kekId: string;
  wrapped: string;
  createdAt: number;
}

/** Inline sealed content as stored on an event, in place of `data`. */
export interface SealedContent {
  /** Sealed format version. */
  v: number;
  kid: string;
  /** Data key version. */
  kv: number;
  iv: string;
  tag: string;
  ct: string;
}

export function sealAad(
  purpose: SealPurpose,
  orgId: string,
  key: DataKeyRef,
  context: SealContext,
): Buffer {
  return canonicalJsonBytes({
    v: SEALED_FORMAT_VERSION,
    purpose,
    orgId,
    kid: key.kid,
    kv: key.version,
    context,
  });
}

interface Sealed {
  iv: Buffer;
  tag: Buffer;
  ct: Buffer;
}

function encrypt(key: Buffer, plaintext: Buffer, aad: Buffer): Sealed {
  const iv = randomBytes(SEAL_IV_BYTES);
  const cipher = createCipheriv(SEAL_ALGORITHM, key, iv);
  cipher.setAAD(aad);
  const ct = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return { iv, tag: cipher.getAuthTag(), ct };
}

function decrypt(key: Buffer, sealed: Sealed, aad: Buffer): Buffer {
  if (
    sealed.iv.length !== SEAL_IV_BYTES ||
    sealed.tag.length !== SEAL_TAG_BYTES
  ) {
    throw new SealedContentError('corrupt', 'Sealed content is malformed');
  }
  try {
    const decipher = createDecipheriv(SEAL_ALGORITHM, key, sealed.iv);
    decipher.setAAD(aad);
    decipher.setAuthTag(sealed.tag);
    return Buffer.concat([decipher.update(sealed.ct), decipher.final()]);
  } catch {
    throw new SealedContentError(
      'corrupt',
      'Sealed content failed authentication',
    );
  }
}

export function sealContent(
  key: DataKey,
  purpose: SealPurpose,
  context: SealContext,
  plaintext: Buffer,
): SealedContent {
  const sealed = encrypt(
    key.key,
    plaintext,
    sealAad(purpose, key.orgId, key, context),
  );
  return {
    v: SEALED_FORMAT_VERSION,
    kid: key.kid,
    kv: key.version,
    iv: sealed.iv.toString('base64'),
    tag: sealed.tag.toString('base64'),
    ct: sealed.ct.toString('base64'),
  };
}

/** Throws `unsupported_version` for a format this build cannot read. */
export function assertSealedVersion(v: unknown): void {
  if (v !== SEALED_FORMAT_VERSION) {
    throw new SealedContentError(
      'unsupported_version',
      `Sealed content format ${JSON.stringify(v)} is not readable here`,
    );
  }
}

export function sealedKeyRef(sealed: SealedContent): DataKeyRef {
  assertSealedVersion(sealed.v);
  if (
    typeof sealed.kid !== 'string' ||
    !Number.isInteger(sealed.kv) ||
    typeof sealed.iv !== 'string' ||
    typeof sealed.tag !== 'string' ||
    typeof sealed.ct !== 'string'
  ) {
    throw new SealedContentError('corrupt', 'Sealed content is malformed');
  }
  return { kid: sealed.kid, version: sealed.kv };
}

export function openContent(
  key: DataKey,
  purpose: SealPurpose,
  context: SealContext,
  sealed: SealedContent,
): Buffer {
  sealedKeyRef(sealed);
  return decrypt(
    key.key,
    {
      iv: Buffer.from(sealed.iv, 'base64'),
      tag: Buffer.from(sealed.tag, 'base64'),
      ct: Buffer.from(sealed.ct, 'base64'),
    },
    sealAad(purpose, key.orgId, key, context),
  );
}

/**
 * Binary form for claim-checked payloads (no base64 on 256 KB+ bodies):
 * magic, format version, key id, key version, IV, tag, ciphertext.
 */
const BLOB_MAGIC = Buffer.from('PHSB', 'ascii');
const BLOB_FIXED = BLOB_MAGIC.length + 1 + 2;

export function sealBlob(
  key: DataKey,
  purpose: SealPurpose,
  context: SealContext,
  plaintext: Buffer,
): Buffer {
  const sealed = encrypt(
    key.key,
    plaintext,
    sealAad(purpose, key.orgId, key, context),
  );
  const kid = Buffer.from(key.kid, 'utf8');
  const header = Buffer.alloc(BLOB_FIXED);
  BLOB_MAGIC.copy(header, 0);
  header.writeUInt8(SEALED_FORMAT_VERSION, BLOB_MAGIC.length);
  header.writeUInt16BE(kid.length, BLOB_MAGIC.length + 1);
  const version = Buffer.alloc(4);
  version.writeUInt32BE(key.version, 0);
  return Buffer.concat([
    header,
    kid,
    version,
    sealed.iv,
    sealed.tag,
    sealed.ct,
  ]);
}

interface DecodedBlob extends Sealed {
  key: DataKeyRef;
}

function decodeBlob(bytes: Buffer): DecodedBlob {
  if (
    bytes.length < BLOB_FIXED ||
    !bytes.subarray(0, BLOB_MAGIC.length).equals(BLOB_MAGIC)
  ) {
    throw new SealedContentError('corrupt', 'Sealed payload is malformed');
  }
  assertSealedVersion(bytes.readUInt8(BLOB_MAGIC.length));
  const kidLength = bytes.readUInt16BE(BLOB_MAGIC.length + 1);
  const ivAt = BLOB_FIXED + kidLength + 4;
  const ctAt = ivAt + SEAL_IV_BYTES + SEAL_TAG_BYTES;
  if (bytes.length < ctAt) {
    throw new SealedContentError('corrupt', 'Sealed payload is truncated');
  }
  return {
    key: {
      kid: bytes.subarray(BLOB_FIXED, BLOB_FIXED + kidLength).toString('utf8'),
      version: bytes.readUInt32BE(BLOB_FIXED + kidLength),
    },
    iv: bytes.subarray(ivAt, ivAt + SEAL_IV_BYTES),
    tag: bytes.subarray(ivAt + SEAL_IV_BYTES, ctAt),
    ct: bytes.subarray(ctAt),
  };
}

export const sealedBlobKeyRef = (bytes: Buffer): DataKeyRef =>
  decodeBlob(bytes).key;

export function openBlob(
  key: DataKey,
  purpose: SealPurpose,
  context: SealContext,
  bytes: Buffer,
): Buffer {
  const decoded = decodeBlob(bytes);
  return decrypt(key.key, decoded, sealAad(purpose, key.orgId, key, context));
}
