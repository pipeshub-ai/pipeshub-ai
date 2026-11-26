/**
 * Journal authority (REPORT 6.1.8, D16): who may claim a run and who may act
 * for a claimed attempt. Pure types and rules; crypto and storage sit behind
 * the application services and ports.
 */

/** Upper bound on a lease a worker may ask for (claim and renew). */
export const MAX_LEASE_DURATION_MS = 5 * 60 * 1000;

/** Attempt tokens outlive the lease by this much, to absorb Node replica clock skew. */
export const ATTEMPT_TOKEN_SLACK_MS = 30 * 1000;

export const MAX_ATTEMPT_TOKEN_LIFETIME_MS =
  MAX_LEASE_DURATION_MS + ATTEMPT_TOKEN_SLACK_MS;

/** Audience of attempt tokens: only the harness commit routes accept them. */
export const ATTEMPT_TOKEN_AUDIENCE = 'pipeshub-agent-harness/attempt';
export const ATTEMPT_TOKEN_ISSUER = 'pipeshub-node/agent-harness';
export const ATTEMPT_TOKEN_ALGORITHM = 'EdDSA';

/**
 * A retired signing key stays verifiable for the longest token it can have
 * signed, plus a replica that had not yet reloaded the keyring when it was
 * retired (KEYRING_CACHE_TTL_MS) and margin.
 */
export const RETIRED_KEY_RETENTION_MS = 15 * 60 * 1000;

/** Signing keys are replaced when this old, on the next load. */
export const ATTEMPT_KEY_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

/** How long a replaced worker credential still claims (workers re-read config per claim). */
export const PREVIOUS_WORKER_CREDENTIAL_GRACE_MS = 15 * 60 * 1000;

/** How long a Node replica trusts its cached credential set or keyring. */
export const AUTHORITY_CACHE_TTL_MS = 30 * 1000;

/**
 * How long past its TTL a replica keeps using its cached credential set or
 * keyring while the KV store cannot be read (REPORT 6.17, Redis down). Equal
 * to the rotation grace: staleness hides only a rotation made meanwhile, and
 * retention of retired keys is still judged on the current time.
 */
export const AUTHORITY_STALE_MAX_MS = 15 * 60 * 1000;

/** Minimum gap between forced reloads, so bad requests cannot hammer the config store. */
export const AUTHORITY_MIN_RELOAD_INTERVAL_MS = 1000;

export interface AuthorityClock {
  now(): number;
}

export const systemAuthorityClock: AuthorityClock = { now: () => Date.now() };

/** The per-deployment secret only harness workers hold. */
export interface WorkerCredentialSet {
  current: string;
  previous: string | null;
  /** Wall-clock ms; the previous credential is refused after this. */
  previousValidUntil: number | null;
  /** Operator rotation id (`ROTATE_SIGNING_SECRETS`) last applied. */
  rotateId: string | null;
}

export interface AttemptSigningKey {
  kid: string;
  /** PKCS#8 PEM. Never leaves the encrypted keyring document. */
  privateKey: string;
  /** SPKI PEM. */
  publicKey: string;
  createdAt: number;
  retiredAt: number | null;
}

export interface AttemptKeyring {
  activeKid: string;
  keys: AttemptSigningKey[];
  rotateId: string | null;
}

/** What verifiers without minting authority read (P2.4 credential service). */
export interface PublishedAttemptKey {
  kid: string;
  alg: typeof ATTEMPT_TOKEN_ALGORITHM;
  publicKey: string;
  retiredAt: number | null;
}

export interface PublishedAttemptKeys {
  issuer: string;
  audience: string;
  keys: PublishedAttemptKey[];
}

/** What an attempt token authorizes: one worker, one attempt, one fence. */
export interface AttemptClaims {
  orgId: string;
  runId: string;
  attemptId: string;
  fence: number;
  workerId: string;
}

export interface IssuedAttemptToken {
  token: string;
  /** Wall-clock ms. */
  expiresAt: number;
}

export function newWorkerCredentialSet(
  credential: string,
  rotateId: string | null,
): WorkerCredentialSet {
  return {
    current: credential,
    previous: null,
    previousValidUntil: null,
    rotateId,
  };
}

export function rotateWorkerCredentialSet(
  set: WorkerCredentialSet,
  next: string,
  now: number,
  rotateId: string | null = set.rotateId,
): WorkerCredentialSet {
  return {
    current: next,
    previous: set.current,
    previousValidUntil: now + PREVIOUS_WORKER_CREDENTIAL_GRACE_MS,
    rotateId,
  };
}

/** The credentials a claim may present at `now`. */
export function acceptedWorkerCredentials(
  set: WorkerCredentialSet,
  now: number,
): string[] {
  const accepted = [set.current];
  if (
    set.previous !== null &&
    set.previousValidUntil !== null &&
    now < set.previousValidUntil
  ) {
    accepted.push(set.previous);
  }
  return accepted;
}

export function newAttemptKeyring(
  key: AttemptSigningKey,
  rotateId: string | null,
): AttemptKeyring {
  return { activeKid: key.kid, keys: [key], rotateId };
}

const withinRetention = (key: AttemptSigningKey, now: number): boolean =>
  key.retiredAt === null || now - key.retiredAt < RETIRED_KEY_RETENTION_MS;

/** New key signs from now on; retired keys verify until their retention ends. */
export function rotateAttemptKeyring(
  ring: AttemptKeyring,
  next: AttemptSigningKey,
  now: number,
  rotateId: string | null = ring.rotateId,
): AttemptKeyring {
  const keys = ring.keys
    .map((key) =>
      key.kid === ring.activeKid && key.retiredAt === null
        ? { ...key, retiredAt: now }
        : key,
    )
    .filter((key) => withinRetention(key, now));
  return { activeKid: next.kid, keys: [...keys, next], rotateId };
}

export function activeAttemptKey(ring: AttemptKeyring): AttemptSigningKey {
  const key = ring.keys.find((candidate) => candidate.kid === ring.activeKid);
  if (key === undefined) {
    throw new Error('Attempt keyring has no active key');
  }
  return key;
}

/** Keys that may still verify a token at `now`. */
export function verifiableAttemptKeys(
  ring: AttemptKeyring,
  now: number,
): AttemptSigningKey[] {
  return ring.keys.filter((key) => withinRetention(key, now));
}

export function publishedAttemptKeys(
  ring: AttemptKeyring,
  issuer: string = ATTEMPT_TOKEN_ISSUER,
  audience: string = ATTEMPT_TOKEN_AUDIENCE,
): PublishedAttemptKeys {
  return {
    issuer,
    audience,
    keys: ring.keys.map(({ kid, publicKey, retiredAt }) => ({
      kid,
      alg: ATTEMPT_TOKEN_ALGORITHM,
      publicKey,
      retiredAt,
    })),
  };
}
