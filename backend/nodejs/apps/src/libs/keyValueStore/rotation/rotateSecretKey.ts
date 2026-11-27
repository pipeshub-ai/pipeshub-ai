import { EncryptionService } from '../../encryptor/encryptor';
import {
  cipherRing,
  weakSecretMessage,
  weakSecretReason,
} from '../../encryptor/secret-key-ring';
import { isPlaceholderSecretKey } from '../../../modules/configuration_manager/config/config';
import type { DistributedKeyValueStore } from '../keyValueStore';

type RotationStore = Pick<
  DistributedKeyValueStore<string>,
  'getAllKeys' | 'getKey' | 'compareAndSet'
> & {
  /** Optional: providers that can tell a key has an expiry (Redis, etcd, Postgres). */
  hasExpiry?: (key: string) => Promise<boolean>;
};

export interface RotationResult {
  /** Values re-encrypted (or that would be, on a dry run). */
  rotated: number;
  /** Values with nothing in the encrypted format, left alone. */
  plaintext: number;
  /** Key names (never values) holding an encrypted-format value that decrypts with neither secret. Left as they are. */
  failed: string[];
  /** Values already on the new secret (a re-run after a partial run). */
  alreadyRotated: number;
  /** Key names whose value changed, or whose write was not applied, so they were not rewritten. */
  conflicts: string[];
  /** Key names whose write threw an error. */
  writeErrors: string[];
  /** Key names holding a non-string value (Redis WRONGTYPE); left alone. */
  skipped: string[];
  /** Key names with an expiry. A rewrite would drop the expiry, so they are not touched. */
  expiring: string[];
}

// iv(12 bytes):ciphertext:authTag(16 bytes), all hex, as written by EncryptionService.
const ENCRYPTED_FORMAT = /^[0-9a-f]{24}:[0-9a-f]*:[0-9a-f]{32}$/i;
// Every JSON string literal, escapes included, scanned left to right so a match never starts at an
// escaped quote. Each one is decoded and rewritten in place only when the whole string is in the
// encrypted format (a parse and re-serialise of the value would change numbers past 2^53 and other formatting).
const JSON_STRING = /"(?:[^"\\]|\\.)*"/g;

// The app's own keys for one secret: it reads what either SECRET_KEY_KDF sealed and writes under the configured one.
// Built directly because EncryptionService.getInstance is a singleton and cannot hold two secrets.
const cipherFor = (secret: string): EncryptionService =>
  EncryptionService.withRing(
    'aes-256-gcm',
    cipherRing({ current: secret, other: null }) as [string, ...string[]],
  );

/** Some writers store the encrypted string JSON-quoted; return the bare string and whether it was quoted. */
function unwrap(raw: string): { text: string; quoted: boolean } {
  if (raw.startsWith('"')) {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (typeof parsed === 'string') return { text: parsed, quoted: true };
    } catch {
      /* not JSON: use as is */
    }
  }
  return { text: raw, quoted: false };
}

function tryDecrypt(c: EncryptionService, text: string): string | null {
  try {
    return c.decrypt(text);
  } catch {
    return null;
  }
}

type Tally = { rotated: number; already: number; failed: number };

/** Re-encrypt one value in the encrypted format; one that decrypts with neither secret is returned unchanged. */
function rotateOne(
  value: string,
  oldC: EncryptionService,
  newC: EncryptionService,
  t: Tally,
): string {
  const plain = tryDecrypt(oldC, value);
  if (plain !== null) {
    t.rotated++;
    return newC.encrypt(plain);
  }
  if (tryDecrypt(newC, value) !== null) t.already++;
  else t.failed++;
  return value;
}

/**
 * A whole value in the encrypted format, or every encrypted string inside a JSON value (at any
 * depth). Everything else in the text is kept byte for byte.
 */
function rotateText(
  text: string,
  oldC: EncryptionService,
  newC: EncryptionService,
  t: Tally,
): string {
  if (ENCRYPTED_FORMAT.test(text)) return rotateOne(text, oldC, newC, t);
  if (!/^\s*[[{]/.test(text)) return text;
  return text.replace(JSON_STRING, (token) => {
    let v: unknown;
    try {
      v = JSON.parse(token);
    } catch {
      return token;
    }
    return typeof v === 'string' && ENCRYPTED_FORMAT.test(v)
      ? `"${rotateOne(v, oldC, newC, t)}"`
      : token;
  });
}

/**
 * Re-encrypt every encrypted value in the store from `oldSecret` to `newSecret`. That is
 * a whole value in the encrypted format, or an encrypted string inside a plain JSON value
 * (for example the `s3` field of the storage setting).
 * Pass 1 reads and decrypts everything and aborts (no writes) if any read fails other than a
 * WRONGTYPE, or if any encrypted-format value decrypts with neither secret: that can be a live
 * secret under a key that is not `oldSecret`. With `skipUnreadable`, the operator has checked
 * those keys (a stale value, or plain text that only looks encrypted) and they are left as they
 * are. Pass 2 writes with compare-and-set.
 */
export async function rotateEncryptedValues(
  store: RotationStore,
  oldSecret: string,
  newSecret: string,
  opts: { dryRun: boolean; skipUnreadable?: boolean },
): Promise<RotationResult> {
  if (!oldSecret || !newSecret || oldSecret === newSecret) {
    throw new Error('Old and new secrets must both be set and differ');
  }
  if (isPlaceholderSecretKey(newSecret)) {
    throw new Error(
      'The new secret is a placeholder, shorter than 16 characters or too repetitive',
    );
  }
  // Whatever profile this tool runs under: the services on the hardened profile refuse to start with such a key.
  const weak = weakSecretReason(newSecret);
  if (weak !== null) {
    throw new Error(weakSecretMessage('NEW_SECRET_KEY', weak));
  }
  const oldCipher = cipherFor(oldSecret);
  const newCipher = cipherFor(newSecret);
  const result: RotationResult = {
    rotated: 0,
    plaintext: 0,
    failed: [],
    alreadyRotated: 0,
    conflicts: [],
    writeErrors: [],
    skipped: [],
    expiring: [],
  };
  const pending: { key: string; raw: string; next: string }[] = [];

  for (const key of await store.getAllKeys()) {
    let raw: string | null;
    try {
      raw = await store.getKey(key);
    } catch (e) {
      // Only a key of another Redis type is safe to skip. Any other error (a dropped
      // connection) could hide a real encrypted value, so stop before writing anything.
      if (e instanceof Error && e.message.includes('WRONGTYPE')) {
        result.skipped.push(key);
        continue;
      }
      // eslint-disable-next-line preserve-caught-error -- report the key and message only, never the store's error object
      throw new Error(
        `Could not read key ${key}: ${e instanceof Error ? e.message : 'unknown error'}`,
      );
    }
    if (raw === null) continue; // deleted since listing
    if (typeof raw !== 'string') {
      result.skipped.push(key);
      continue;
    }
    const { text, quoted } = unwrap(raw);
    const t: Tally = { rotated: 0, already: 0, failed: 0 };
    const next = rotateText(text, oldCipher, newCipher, t);

    // A value with any unreadable ciphertext is never written, not even its readable
    // fields: a partly moved value would break the earlier-key recovery run.
    if (t.failed > 0) {
      result.failed.push(key);
      continue;
    }
    if (t.rotated > 0) {
      if (store.hasExpiry && (await store.hasExpiry(key))) {
        result.expiring.push(key);
        continue;
      }
      pending.push({ key, raw, next: quoted ? JSON.stringify(next) : next });
    } else if (t.already > 0) result.alreadyRotated++;
    else result.plaintext++;
  }

  if (result.failed.length > 0 && !opts.skipUnreadable) return result;
  if (opts.dryRun) {
    result.rotated = pending.length;
    return result;
  }

  for (const { key, raw, next } of pending) {
    try {
      if (await store.compareAndSet(key, raw, next)) result.rotated++;
      else result.conflicts.push(key);
    } catch {
      result.writeErrors.push(key);
    }
  }
  return result;
}
