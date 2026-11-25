import { createPrivateKey, createPublicKey, KeyObject } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { z } from 'zod/v3';
import {
  activeAttemptKey,
  ATTEMPT_KEY_MAX_AGE_MS,
  ATTEMPT_TOKEN_ALGORITHM,
  AttemptKeyring,
  AttemptSigningKey,
  AUTHORITY_CACHE_TTL_MS,
  AUTHORITY_MIN_RELOAD_INTERVAL_MS,
  AUTHORITY_STALE_MAX_MS,
  AuthorityClock,
  newAttemptKeyring,
  PublishedAttemptKeys,
  publishedAttemptKeys,
  rotateAttemptKeyring,
  systemAuthorityClock,
  verifiableAttemptKeys,
} from '../domain/authority';
import { HarnessUnauthorizedError } from '../domain/errors';
import { ConfigDocumentStore } from '../ports/config-document-store';
import {
  decodeAttemptToken,
  encodeAttemptToken,
  verifyAttemptSignature,
} from './attempt-token-codec';
import { CachedConfigDocument, updateConfigDocument } from './config-documents';
import { HarnessLogger } from './harness-log';

/** Who signs with a keyring and for whom; published with its public keys. */
export interface KeyringProfile {
  /** Names the keyring document in logs. */
  name: string;
  issuer: string;
  audience: string;
  /** What a token is called in refusal messages ("Attempt token"). */
  label: string;
}

const headerSchema = z
  .object({
    alg: z.literal(ATTEMPT_TOKEN_ALGORITHM),
    typ: z.literal('JWT'),
    kid: z.string().min(1).max(128),
  })
  .strict();

/**
 * One harness-only Ed25519 keyring: provisioning, rotation with an overlap
 * window, compact-JWS signing and signature verification. The private keys
 * live only in the encrypted signing-keys document; the public keys are
 * published separately for verifiers that must not mint. What a token's
 * payload means is the caller's (attempt tokens, gateway tokens).
 */
/** About a megabyte of tokens per key; past it the memo starts again. */
const VERIFIED_TOKENS_PER_KEY = 2048;

export class SigningKeyring {
  private readonly keyring: CachedConfigDocument<AttemptKeyring>;
  private readonly privateKeys = new Map<string, KeyObject>();
  private readonly publicKeys = new Map<string, KeyObject>();
  /**
   * Tokens whose signature a key already checked (PERF-E): a worker sends the
   * same attempt token with every request of its attempt, and Ed25519 gives
   * the same answer for the same bytes and key. The key is still looked up
   * and must still be verifiable on every call; only the signature math is
   * not repeated.
   */
  private readonly verified = new WeakMap<KeyObject, Set<string>>();

  constructor(
    private readonly signingKeys: ConfigDocumentStore<AttemptKeyring>,
    private readonly publishedKeys: ConfigDocumentStore<PublishedAttemptKeys>,
    private readonly profile: KeyringProfile,
    private readonly clock: AuthorityClock = systemAuthorityClock,
    private readonly generateKey: (now: number) => AttemptSigningKey,
    logger?: HarnessLogger,
  ) {
    this.keyring = new CachedConfigDocument(
      signingKeys,
      () => clock.now(),
      AUTHORITY_CACHE_TTL_MS,
      AUTHORITY_MIN_RELOAD_INTERVAL_MS,
      { name: profile.name, maxMs: AUTHORITY_STALE_MAX_MS, logger },
    );
  }

  /** Creates the keyring once; rotates on a new operator id or an aged key. */
  async provision(rotateId: string | null): Promise<void> {
    await this.ensure(rotateId);
  }

  async rotate(): Promise<void> {
    const ring = await updateConfigDocument(this.signingKeys, (current) => {
      const now = this.clock.now();
      return current === null
        ? newAttemptKeyring(this.generateKey(now), null)
        : rotateAttemptKeyring(current, this.generateKey(now), now);
    });
    await this.adopt(ring);
  }

  /** A compact JWS of `payload` under the active key. */
  async sign(payload: Record<string, unknown>): Promise<string> {
    const key = activeAttemptKey(await this.signingRing());
    return encodeAttemptToken(
      { alg: ATTEMPT_TOKEN_ALGORITHM, typ: 'JWT', kid: key.kid },
      payload,
      this.privateKey(key),
    );
  }

  /**
   * Header, key and signature; the payload's meaning is the caller's to check.
   * `maxLength`: the longest token this kind of payload makes (absent: the codec's).
   */
  async verify(
    token: string | undefined,
    maxLength?: number,
  ): Promise<unknown> {
    const label = this.profile.label;
    if (token === undefined || token.length === 0) {
      throw new HarnessUnauthorizedError('malformed', `${label} required`);
    }
    const decoded = decodeAttemptToken(token, maxLength);
    const header = headerSchema.safeParse(decoded.header);
    if (!header.success) {
      const alg = (decoded.header as { alg?: unknown } | null)?.alg;
      throw alg !== ATTEMPT_TOKEN_ALGORITHM
        ? new HarnessUnauthorizedError(
            'algorithm',
            `${label} algorithm refused`,
          )
        : new HarnessUnauthorizedError(
            'malformed',
            `${label} header is malformed`,
          );
    }
    const key = await this.verificationKey(header.data.kid);
    if (key === null) {
      throw new HarnessUnauthorizedError(
        'unknown_key',
        `${label} signing key is unknown`,
      );
    }
    let seen = this.verified.get(key);
    if (seen?.has(token) !== true) {
      if (!verifyAttemptSignature(decoded, key)) {
        throw new HarnessUnauthorizedError(
          'signature',
          `${label} signature is invalid`,
        );
      }
      if (seen === undefined || seen.size >= VERIFIED_TOKENS_PER_KEY) {
        seen = new Set();
        this.verified.set(key, seen);
      }
      seen.add(token);
    }
    return decoded.payload;
  }

  private async ensure(rotateId: string | null): Promise<AttemptKeyring> {
    const ring = await updateConfigDocument(this.signingKeys, (current) => {
      const now = this.clock.now();
      if (current === null) {
        return newAttemptKeyring(this.generateKey(now), rotateId);
      }
      if (rotateId !== null && current.rotateId !== rotateId) {
        return rotateAttemptKeyring(
          current,
          this.generateKey(now),
          now,
          rotateId,
        );
      }
      if (now - activeAttemptKey(current).createdAt >= ATTEMPT_KEY_MAX_AGE_MS) {
        return rotateAttemptKeyring(current, this.generateKey(now), now);
      }
      return null;
    });
    await this.adopt(ring);
    return ring;
  }

  private async signingRing(): Promise<AttemptKeyring> {
    const ring = await this.keyring.get();
    if (
      ring !== null &&
      this.clock.now() - activeAttemptKey(ring).createdAt <
        ATTEMPT_KEY_MAX_AGE_MS
    ) {
      return ring;
    }
    return this.ensure(null);
  }

  private async verificationKey(kid: string): Promise<KeyObject | null> {
    const find = (
      ring: AttemptKeyring | null,
    ): AttemptSigningKey | undefined =>
      ring === null
        ? undefined
        : verifiableAttemptKeys(ring, this.clock.now()).find(
            (key) => key.kid === kid,
          );
    let key = find(await this.keyring.get());
    if (key === undefined) {
      // A replica that rotated since this one cached the keyring.
      const refreshed = await this.keyring.refresh();
      key = refreshed === null ? undefined : find(refreshed.value);
    }
    return key === undefined ? null : this.publicKey(key);
  }

  private async adopt(ring: AttemptKeyring): Promise<void> {
    this.keyring.remember(ring);
    const published = publishedAttemptKeys(
      ring,
      this.profile.issuer,
      this.profile.audience,
    );
    await updateConfigDocument(this.publishedKeys, (current) =>
      isDeepStrictEqual(current, published) ? null : published,
    );
  }

  private privateKey(key: AttemptSigningKey): KeyObject {
    let object = this.privateKeys.get(key.kid);
    if (object === undefined) {
      object = createPrivateKey(key.privateKey);
      this.privateKeys.set(key.kid, object);
    }
    return object;
  }

  private publicKey(key: AttemptSigningKey): KeyObject {
    let object = this.publicKeys.get(key.kid);
    if (object === undefined) {
      object = createPublicKey(key.publicKey);
      this.publicKeys.set(key.kid, object);
    }
    return object;
  }
}
