import { createPublicKey, verify } from 'crypto';
import {
  RELEASE_KEYS,
  RELEASE_KEY_NOT_AFTER,
  REVOKED_KEYS_SIGNATURE_TEXT,
  REVOKED_KEYS_TEXT,
} from './vendor-rules-release';

/**
 * Which release keys may vouch for something now (R41SUPPLY-FU-6,
 * ADR-SEC-06 D1): each key's not-after, and the signed list of revoked keys.
 * Python's `harness_core/supply_chain/release_keys.py` is the same rule; both
 * pass `contracts/release-keys/revocation-vectors.json`.
 *
 * A list (`pipeshub.revoked-keys/1`) only narrows trust: it applies only when
 * newer than the one in force, at most a day ahead of the clock, and keeping
 * every key that one revoked; its signer must be in force under the list in
 * force, and may revoke itself.
 */

export const REVOCATIONS_SCHEMA = 'pipeshub.revoked-keys/1';
const FUTURE_SKEW_MS = 24 * 3600 * 1000;
const MAX_REVOKED = 256;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const KEY_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;

export type RevocationReason =
  | 'missing'
  | 'unsigned'
  | 'unknown_key'
  | 'bad_signature'
  | 'expired_key'
  | 'revoked_signer'
  | 'malformed'
  | 'stale'
  | 'future'
  | 'drops_revocation';

export class RevocationRefused extends Error {
  constructor(
    readonly reason: RevocationReason,
    message: string,
  ) {
    super(message);
    this.name = 'RevocationRefused';
  }
}

export interface ReleaseKey {
  /** Raw Ed25519 public key, base64. */
  readonly publicKey: string;
  /** Unix ms from which the key vouches for nothing. */
  readonly notAfter: number;
}

export interface Revocations {
  /** Unix ms the list in force was issued; null before any. */
  readonly issued: number | null;
  readonly revoked: ReadonlySet<string>;
}

export const NO_REVOCATIONS: Revocations = Object.freeze({
  issued: null,
  revoked: new Set<string>(),
});

/** `YYYY-MM-DDTHH:MM:SSZ` only, the one spelling both languages read the same; else null. */
export function parseInstant(value: unknown): number | null {
  if (typeof value !== 'string' || !INSTANT.test(value)) return null;
  const ms = Date.parse(value);
  // Date.parse rolls 2027-02-30 over to March; Python refuses it, so this does too.
  if (
    Number.isNaN(ms) ||
    new Date(ms).toISOString().replace('.000Z', 'Z') !== value
  ) {
    return null;
  }
  return ms;
}

export function formatInstant(ms: number): string {
  return new Date(ms).toISOString().replace('.000Z', 'Z');
}

const base64url = (raw: Buffer): string =>
  raw
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

/** Whether `sig` is the raw Ed25519 key `publicKey`'s (base64) signature of `document`'s UTF-8 bytes. */
export function ed25519Verifies(
  document: string,
  publicKey: string,
  sig: Buffer,
): boolean {
  try {
    const key = createPublicKey({
      key: {
        kty: 'OKP',
        crv: 'Ed25519',
        x: base64url(Buffer.from(publicKey, 'base64')),
      },
      format: 'jwk',
    });
    return verify(null, Buffer.from(document, 'utf8'), key, sig);
  } catch {
    return false;
  }
}

/** The key id and raw signature of a detached signature document, or null when it is not one. */
export function parseDetachedSignature(
  text: string,
): { keyId: string; sig: Buffer } | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof raw !== 'object' || raw === null) return null;
  const { alg, keyId, sig } = raw as Record<string, unknown>;
  if (
    alg !== 'Ed25519' ||
    typeof keyId !== 'string' ||
    typeof sig !== 'string' ||
    !/^[A-Za-z0-9+/]+={0,2}$/.test(sig)
  ) {
    return null;
  }
  return { keyId, sig: Buffer.from(sig, 'base64') };
}

export const keyUsable = (key: ReleaseKey, now: number): boolean =>
  now < key.notAfter;

/** Key id -> raw public key (base64), for the keys neither revoked nor past their not-after at `now`. */
export function usableKeys(
  keys: Readonly<Record<string, ReleaseKey>>,
  revocations: Revocations,
  now: number,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [keyId, key] of Object.entries(keys)) {
    if (!revocations.revoked.has(keyId) && keyUsable(key, now)) {
      out[keyId] = key.publicKey;
    }
  }
  return out;
}

function parseList(document: string): Revocations {
  const malformed = (): RevocationRefused =>
    new RevocationRefused(
      'malformed',
      'the revocation list is not a pipeshub.revoked-keys/1 document',
    );
  let raw: unknown;
  try {
    raw = JSON.parse(document);
  } catch {
    throw new RevocationRefused('malformed', 'the revocation list is not JSON');
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw malformed();
  }
  const body = raw as Record<string, unknown>;
  const fields = Object.keys(body).sort();
  if (fields.join(',') !== 'issued,revoked,schema') throw malformed();
  const issued = parseInstant(body['issued']);
  const revoked = body['revoked'];
  if (
    body['schema'] !== REVOCATIONS_SCHEMA ||
    issued === null ||
    !Array.isArray(revoked)
  ) {
    throw malformed();
  }
  const ids = revoked as unknown[];
  if (
    ids.length > MAX_REVOKED ||
    !ids.every((id) => typeof id === 'string' && KEY_ID.test(id)) ||
    new Set(ids).size !== ids.length
  ) {
    throw new RevocationRefused(
      'malformed',
      "the revocation list's key ids are not distinct release key ids",
    );
  }
  return { issued, revoked: new Set(ids as string[]) };
}

/** The revocations `document` puts in force over `current`; RevocationRefused naming why not. */
export function verifyRevocations(
  document: string | null,
  signature: string | null,
  keys: Readonly<Record<string, ReleaseKey>>,
  current: Revocations,
  now: number,
): Revocations {
  if (document === null) {
    throw new RevocationRefused('missing', 'there is no revocation list');
  }
  if (signature === null) {
    throw new RevocationRefused(
      'unsigned',
      'the revocation list is not signed',
    );
  }
  const parsed = parseDetachedSignature(signature);
  if (parsed === null) {
    throw new RevocationRefused(
      'unsigned',
      'the revocation list signature is not an Ed25519 signature',
    );
  }
  const key = Object.prototype.hasOwnProperty.call(keys, parsed.keyId)
    ? keys[parsed.keyId]
    : undefined;
  if (key === undefined) {
    throw new RevocationRefused(
      'unknown_key',
      'the revocation list is signed by a key this release does not ship',
    );
  }
  if (!keyUsable(key, now)) {
    throw new RevocationRefused(
      'expired_key',
      'the revocation list is signed by a key past its not-after',
    );
  }
  if (current.revoked.has(parsed.keyId)) {
    throw new RevocationRefused(
      'revoked_signer',
      'the revocation list is signed by a key already revoked',
    );
  }
  if (!ed25519Verifies(document, key.publicKey, parsed.sig)) {
    throw new RevocationRefused(
      'bad_signature',
      "the revocation list's signature does not verify",
    );
  }
  const proposed = parseList(document);
  const issued = proposed.issued as number;
  if (current.issued !== null && issued <= current.issued) {
    throw new RevocationRefused(
      'stale',
      'the revocation list is not newer than the one in force',
    );
  }
  if (issued > now + FUTURE_SKEW_MS) {
    throw new RevocationRefused(
      'future',
      "the revocation list is dated more than a day ahead of this host's clock",
    );
  }
  for (const id of current.revoked) {
    if (!proposed.revoked.has(id)) {
      throw new RevocationRefused(
        'drops_revocation',
        'the revocation list drops a key the one in force revokes',
      );
    }
  }
  return proposed;
}

// ---------------------------------------------------------------------------
// The keys in force in this process: RELEASE_KEYS with their not-after, less
// the newest list it has applied. Nothing here reads the environment: the
// keys stay code (OWD-8), and a deployment can only narrow them.

let inForce: Revocations | null = null;

export function releaseKeySet(): Record<string, ReleaseKey> {
  const out: Record<string, ReleaseKey> = {};
  for (const [keyId, publicKey] of Object.entries(RELEASE_KEYS)) {
    const notAfter = parseInstant(RELEASE_KEY_NOT_AFTER[keyId]);
    if (notAfter !== null) out[keyId] = { publicKey, notAfter };
  }
  return out;
}

/** The list in force: the release's own (when it ships one) until a newer one is applied. */
export function revocationsInForce(): Revocations {
  if (inForce === null) {
    try {
      inForce = verifyRevocations(
        REVOKED_KEYS_TEXT,
        REVOKED_KEYS_SIGNATURE_TEXT,
        releaseKeySet(),
        NO_REVOCATIONS,
        Date.now(),
      );
    } catch {
      inForce = NO_REVOCATIONS;
    }
  }
  return inForce;
}

/** Puts `document` in force when it is a newer valid list and answers it; throws RevocationRefused otherwise. */
export function applyRevocations(
  document: string | null,
  signature: string | null,
  now: number = Date.now(),
): Revocations {
  inForce = verifyRevocations(
    document,
    signature,
    releaseKeySet(),
    revocationsInForce(),
    now,
  );
  return inForce;
}

/** Key id -> raw public key (base64): RELEASE_KEYS neither revoked nor past their not-after at `now`. */
export function trustedReleaseKeys(
  now: number = Date.now(),
): Record<string, string> {
  return usableKeys(releaseKeySet(), revocationsInForce(), now);
}

export function resetRevocationsForTests(): void {
  inForce = null;
}
