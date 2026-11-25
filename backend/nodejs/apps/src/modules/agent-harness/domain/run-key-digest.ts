import { createHmac, hkdfSync, timingSafeEqual } from 'node:crypto';
import { canonicalJsonBytes } from '../contracts/canonical-json';

const PREFIX = 'hk1:';
/** A stored key in digest form; Mongo queries match it too. */
export const RUN_KEY_DIGEST_PATTERN = /^hk1:[0-9a-f]{64}$/;
const SALT = Buffer.from('pipeshub/agent-harness/run-key', 'utf8');
const INFO = Buffer.from('run-idempotency-key/v1', 'utf8');
const KEY_BYTES = 32;

/**
 * What a run document stores for its idempotency key (QA15-F3): a caller may
 * send any text as the key, and the run outlives its session's erasure as a
 * tombstone. Nothing reads the stored value back (admission deduplicates by
 * its receipt, a child by its id), so a one-way digest loses nothing.
 */
export interface RunKeyDigest {
  digest(orgId: string, idempotencyKey: string): string;
  /**
   * Whether `stored` is this key's digest under any key of the ring (ADR-KEY-01):
   * a run admitted before a key switch still matches during the grace window.
   */
  matches(orgId: string, idempotencyKey: string, stored: string): boolean;
}

/** Whether a stored key is already a digest; a run an older build wrote holds the caller's key. */
export const isRunKeyDigest = (stored: string): boolean =>
  RUN_KEY_DIGEST_PATTERN.test(stored);

/**
 * Keyed, so a guessable key cannot be confirmed from a tombstone without the
 * secret; the HKDF output keeps it independent of the secret's other uses.
 */
export class HmacRunKeyDigest implements RunKeyDigest {
  private keys: Buffer[] | null = null;

  /** `secret` writes; `others` (the ring's other key, read lazily) only match. */
  constructor(
    private readonly secret: () => Buffer,
    private readonly others: () => readonly Buffer[] = () => [],
  ) {}

  digest(orgId: string, idempotencyKey: string): string {
    return this.digestUnder(this.ring()[0] as Buffer, orgId, idempotencyKey);
  }

  matches(orgId: string, idempotencyKey: string, stored: string): boolean {
    const want = Buffer.from(stored, 'utf8');
    let found = false;
    // Every key is tried, so the time taken does not say which one matched.
    for (const key of this.ring()) {
      const have = Buffer.from(
        this.digestUnder(key, orgId, idempotencyKey),
        'utf8',
      );
      if (have.length === want.length && timingSafeEqual(have, want))
        found = true;
    }
    return found;
  }

  private ring(): Buffer[] {
    this.keys ??= [this.secret(), ...this.others()].map((secret) =>
      Buffer.from(hkdfSync('sha256', secret, SALT, INFO, KEY_BYTES)),
    );
    return this.keys;
  }

  private digestUnder(
    key: Buffer,
    orgId: string,
    idempotencyKey: string,
  ): string {
    return (
      PREFIX +
      createHmac('sha256', key)
        .update(canonicalJsonBytes([orgId, idempotencyKey]))
        .digest('hex')
    );
  }
}
