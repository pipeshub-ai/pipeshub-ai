import crypto from 'crypto';
import fs from 'fs';
import { hardenedProfile, warnOnce } from '../utils/security-profile';

/**
 * The deployment secret as a key ring (ADR-KEY-01): `SECRET_KEY` is the one
 * every writer uses; `SECRET_KEY_NEXT` (before the switch) or
 * `SECRET_KEY_PREVIOUS` (after it) is a second key readers also accept, so a
 * fleet restarting one replica at a time never meets data it cannot open.
 * Python reads the same variables with the same rules
 * (`app/utils/encryption/secret_key_ring.py`).
 *
 * SEC-D-05 (ADR-KEY-02): a key a writer will use (`SECRET_KEY`,
 * `SECRET_KEY_NEXT`) must look random; both languages agree on
 * `contracts/secret-key/strength-cases.json`. The platform cipher's AES key
 * is HKDF of the secret; the SHA-256 key it replaces is still read, and
 * `rotate-secret-key rotate` moves what it sealed.
 *
 * R38IDENT-FU-8 (ADR-KEY-02 D8): each ring member may come from a file instead
 * (`SECRET_KEY_FILE`, `SECRET_KEY_PREVIOUS_FILE`, `SECRET_KEY_NEXT_FILE`: a
 * Docker secret or a Kubernetes Secret volume), so the root key is not in the
 * container's environment. A named file wins over the variable; one that
 * cannot be read or is empty stops the start with the variable and the path,
 * never the content.
 */
export const SECRET_KEY_ENV = 'SECRET_KEY';
export const SECRET_KEY_PREVIOUS_ENV = 'SECRET_KEY_PREVIOUS';
export const SECRET_KEY_NEXT_ENV = 'SECRET_KEY_NEXT';
/**
 * Which cipher key writers use: `sha256` (the key a release without SEC-D-05 reads) or `hkdf`. Unset follows the
 * security profile (ADR-OPS-07): compatibility writes under `sha256`, so a rollback still reads what was written
 * after the upgrade; hardened writes under `hkdf`. Readers accept both either way.
 */
export const SECRET_KEY_KDF_ENV = 'SECRET_KEY_KDF';
export const FILE_SUFFIX = '_FILE';

export const MIN_SECRET_KEY_LENGTH = 32;
// Shannon entropy of the characters themselves: random hex is about 3.8, random base64 about 5.
const MIN_BITS_PER_CHAR = 3.0;
const PLACEHOLDERS: ReadonlySet<string> = new Set([
  'yourrandomencryptionsecretkey',
  'changeme',
  'secret',
  'secretkey',
  'password',
  'pipeshub',
  'default',
  'example',
  'test',
  'admin',
]);
const PLACEHOLDER_STEMS = ['yourrandomencryptionsecretkey'];
const CIPHER_SALT = 'pipeshub/platform-cipher';
const CIPHER_INFO = 'aes-256-gcm/v2';

export type WeakSecretReason =
  | 'whitespace'
  | 'placeholder'
  | 'short'
  | 'low_entropy'
  | 'pattern'
  | 'encoded_text';

const REASON_TEXT: Record<WeakSecretReason, string> = {
  whitespace: 'starts or ends with a space or line break',
  placeholder: 'is a published placeholder',
  short: `is shorter than ${String(MIN_SECRET_KEY_LENGTH)} characters`,
  low_entropy: 'repeats too few characters to be random',
  pattern: 'is a repeated or sequential pattern',
  encoded_text: 'is readable text encoded as hex or base64, not random bytes',
};

export type SecretKeyRole = 'current' | 'previous' | 'next';

export interface SecretKeyRing {
  /** The raw secret writers use. */
  readonly current: string;
  /** The other key readers accept, if any: never used to write. */
  readonly other: { role: 'previous' | 'next'; secret: string } | null;
}

/** A ring that must not be used: the message names the variables, never a value. */
export class SecretKeyRingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SecretKeyRingError';
  }
}

type Env = Readonly<Record<string, string | undefined>>;

const warnedPaths = new Set<string>();

/** One ring member: the file `<name>_FILE` names (one trailing line break dropped), else `<name>`, else `''`. */
export function ringSecret(name: string, env: Env = process.env): string {
  const fileEnv = `${name}${FILE_SUFFIX}`;
  const path = (env[fileEnv] ?? '').trim();
  if (path === '') return env[name] ?? '';
  let secret: string;
  let mode: number;
  try {
    const fd = fs.openSync(path, 'r');
    try {
      mode = fs.fstatSync(fd).mode;
      secret = fs.readFileSync(fd, 'utf8');
    } finally {
      fs.closeSync(fd);
    }
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code ?? 'error';
    throw new SecretKeyRingError(
      `${fileEnv} names ${path}, which cannot be read (${code})`,
    );
  }
  // A file written by `echo` or an editor ends with a line break; any other whitespace is the key's and is refused.
  if (secret.endsWith('\n')) {
    secret = secret.slice(0, -1);
    if (secret.endsWith('\r')) secret = secret.slice(0, -1);
  }
  if (secret === '') {
    throw new SecretKeyRingError(`${fileEnv} names ${path}, which is empty`);
  }
  if ((mode & 0o004) !== 0 && !warnedPaths.has(path)) {
    // Not refused: Kubernetes and Docker set a secret's mode themselves. Every account on the host can read it.
    warnedPaths.add(path);
    process.emitWarning(
      `${fileEnv} names ${path}, which every account can read: make it mode 0400`,
    );
  }
  return secret;
}

/**
 * Whether this process was given `SECRET_KEY`, by value or by file (a file
 * that cannot be read still counts: reading the ring then says why).
 */
export function secretKeyConfigured(env: Env = process.env): boolean {
  return (
    (env[SECRET_KEY_ENV] ?? '') !== '' ||
    (env[`${SECRET_KEY_ENV}${FILE_SUFFIX}`] ?? '').trim() !== ''
  );
}

/** Reads and checks the ring; every misconfiguration fails closed with the reason. */
export function secretKeyRing(env: Env = process.env): SecretKeyRing {
  const current = ringSecret(SECRET_KEY_ENV, env);
  if (!current) {
    throw new SecretKeyRingError('SECRET_KEY environment variable is required');
  }
  const previous = ringSecret(SECRET_KEY_PREVIOUS_ENV, env);
  const next = ringSecret(SECRET_KEY_NEXT_ENV, env);
  requireStrong(SECRET_KEY_ENV, current, env);
  if (next) requireStrong(SECRET_KEY_NEXT_ENV, next, env);
  if (previous && next) {
    throw new SecretKeyRingError(
      'SECRET_KEY_PREVIOUS and SECRET_KEY_NEXT are both set: set SECRET_KEY_NEXT before the switch, SECRET_KEY_PREVIOUS after it, never both',
    );
  }
  const other = previous
    ? { role: 'previous' as const, secret: previous }
    : next
      ? { role: 'next' as const, secret: next }
      : null;
  if (other !== null && sameSecret(other.secret, current)) {
    const name =
      other.role === 'previous' ? SECRET_KEY_PREVIOUS_ENV : SECRET_KEY_NEXT_ENV;
    throw new SecretKeyRingError(
      `${name} is the same as SECRET_KEY: a rotation needs a new key`,
    );
  }
  return { current, other };
}

/**
 * SHA-256 of a secret, hex: the KEK's and run-key digests' input keying
 * material (each runs its own HKDF over it), and the platform cipher's key
 * before SEC-D-05, still read.
 */
export const hashedSecret = (secret: string): string =>
  crypto.createHash('sha256').update(secret).digest('hex');

/** Every secret's SHA-256, the writer's first (the KEK's and run-key digests' input). */
export const hashedRing = (ring: SecretKeyRing): string[] => [
  hashedSecret(ring.current),
  ...(ring.other !== null ? [hashedSecret(ring.other.secret)] : []),
];

/** The platform cipher's AES-256 key for one secret, hex: HKDF-SHA256 under the cipher's own salt and label. */
export const platformCipherKey = (secret: string): string =>
  Buffer.from(
    crypto.hkdfSync(
      'sha256',
      Buffer.from(secret, 'utf8'),
      Buffer.from(CIPHER_SALT, 'utf8'),
      Buffer.from(CIPHER_INFO, 'utf8'),
      32,
    ),
  ).toString('hex');

/**
 * Every AES key the platform cipher reads with, the writer's first: per
 * secret its HKDF key, then the SHA-256 key it replaces (SEC-D-05).
 */
export function cipherRing(
  ring: SecretKeyRing,
  env: Env = process.env,
): string[] {
  const secrets = [
    ring.current,
    ...(ring.other !== null ? [ring.other.secret] : []),
  ];
  const keys = secrets.flatMap((secret) => [
    platformCipherKey(secret),
    hashedSecret(secret),
  ]);
  if (writerKdf(env) === 'sha256') {
    return [keys[1] as string, keys[0] as string, ...keys.slice(2)];
  }
  return keys;
}

export type WriterKdf = 'sha256' | 'hkdf';

/** The key derivation writers use: `SECRET_KEY_KDF` when it names one, else the security profile's. */
export function writerKdf(env: Env = process.env): WriterKdf {
  const explicit = (env[SECRET_KEY_KDF_ENV] ?? '').trim().toLowerCase();
  if (explicit === 'sha256' || explicit === 'hkdf') return explicit;
  return hardenedProfile(env) ? 'hkdf' : 'sha256';
}

/** Why `secret` cannot be a key anything is written under, or null. Never logs or returns the value. */
export function weakSecretReason(secret: string): WeakSecretReason | null {
  if (secret !== secret.trim()) return 'whitespace';
  const folded = secret.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (
    PLACEHOLDERS.has(folded) ||
    PLACEHOLDER_STEMS.some((stem) => folded.includes(stem))
  ) {
    return 'placeholder';
  }
  if (Array.from(secret).length < MIN_SECRET_KEY_LENGTH) return 'short';
  if (bitsPerChar(Array.from(secret)) < MIN_BITS_PER_CHAR) return 'low_entropy';
  if (periodic(secret) || sequential(secret)) return 'pattern';
  const decoded = decodedBytes(secret);
  if (decoded !== null && bitsPerChar([...decoded]) < MIN_BITS_PER_CHAR) {
    return 'low_entropy';
  }
  if (
    decoded !== null &&
    decoded.every((byte) => byte >= 0x20 && byte < 0x7f)
  ) {
    return 'encoded_text';
  }
  return null;
}

export function weakSecretMessage(
  name: string,
  reason: WeakSecretReason,
): string {
  return (
    `${name} ${REASON_TEXT[reason]}. It encrypts every stored credential, so it must be random: generate one ` +
    'with `openssl rand -base64 32` (install.sh generates one for you). To replace a key already in use, follow ' +
    '"Rotating SECRET_KEY" in the runbooks: the new key goes in SECRET_KEY, the old one in SECRET_KEY_PREVIOUS.'
  );
}

function requireStrong(name: string, secret: string, env: Env): void {
  const reason = weakSecretReason(secret);
  if (reason === null) return;
  if (hardenedProfile(env)) {
    throw new SecretKeyRingError(weakSecretMessage(name, reason));
  }
  // Compatibility (ADR-OPS-07): a release without SEC-D-05 started with this key, so an upgrade must too.
  warnOnce(
    `${weakSecretMessage(name, reason)} Accepted because PIPESHUB_HARDENED is not set; the hardened profile refuses it.`,
  );
}

function bitsPerChar(symbols: readonly (string | number)[]): number {
  const counts = new Map<string | number, number>();
  for (const symbol of symbols)
    counts.set(symbol, (counts.get(symbol) ?? 0) + 1);
  const total = symbols.length;
  let bits = 0;
  for (const count of counts.values()) {
    const p = count / total;
    bits -= p * Math.log2(p);
  }
  return bits;
}

function periodic(text: string): boolean {
  for (let size = 1; size <= Math.floor(text.length / 2); size += 1) {
    if (
      text.length % size === 0 &&
      text === text.slice(0, size).repeat(text.length / size)
    ) {
      return true;
    }
  }
  return false;
}

function sequential(text: string): boolean {
  let unit = 0;
  for (let i = 1; i < text.length; i += 1) {
    const step = text.charCodeAt(i) - text.charCodeAt(i - 1);
    if (step === 1 || step === -1) unit += 1;
  }
  return unit * 2 > text.length - 1;
}

/** The bytes `text` encodes as hex or base64 (16 or more), else null. */
function decodedBytes(text: string): number[] | null {
  if (/^(?:[0-9a-fA-F]{2}){16,}$/.test(text)) {
    return [...Buffer.from(text, 'hex')];
  }
  if (/^[A-Za-z0-9+/_-]{22,}={0,2}$/.test(text)) {
    const raw = Buffer.from(
      text.replace(/=+$/, '').replace(/-/g, '+').replace(/_/g, '/'),
      'base64',
    );
    return raw.length >= 16 ? [...raw] : null;
  }
  return null;
}

function sameSecret(left: string, right: string): boolean {
  // Compared by digest so the comparison neither leaks length nor depends on content.
  return crypto.timingSafeEqual(
    crypto.createHash('sha256').update(left).digest(),
    crypto.createHash('sha256').update(right).digest(),
  );
}
