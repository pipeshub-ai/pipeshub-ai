import {
  createHash,
  generateKeyPairSync,
  KeyObject,
  sign,
  verify,
} from 'node:crypto';
import { AttemptSigningKey } from '../domain/authority';
import { HarnessUnauthorizedError } from '../domain/errors';

/**
 * Compact JWS (RFC 7515) over Ed25519 with Node's built-in crypto:
 * jsonwebtoken has no EdDSA, and the harness needs no other algorithm, so the
 * header is checked for exactly one `alg` rather than negotiated.
 */

const MAX_TOKEN_LENGTH = 4096;
const BASE64URL = /^[A-Za-z0-9_-]+$/;

export interface DecodedAttemptToken {
  header: unknown;
  payload: unknown;
  signingInput: string;
  signature: Buffer;
}

const encodeSegment = (value: unknown): string =>
  Buffer.from(JSON.stringify(value), 'utf8').toString('base64url');

export function encodeAttemptToken(
  header: Record<string, unknown>,
  payload: Record<string, unknown>,
  privateKey: KeyObject,
): string {
  const signingInput = `${encodeSegment(header)}.${encodeSegment(payload)}`;
  const signature = sign(null, Buffer.from(signingInput, 'ascii'), privateKey);
  return `${signingInput}.${signature.toString('base64url')}`;
}

export function decodeAttemptToken(
  token: string,
  maxLength: number = MAX_TOKEN_LENGTH,
): DecodedAttemptToken {
  const malformed = (): HarnessUnauthorizedError =>
    new HarnessUnauthorizedError('malformed', 'Attempt token is malformed');
  if (token.length > maxLength) throw malformed();
  const segments = token.split('.');
  if (
    segments.length !== 3 ||
    !segments.every((part) => BASE64URL.test(part))
  ) {
    throw malformed();
  }
  const [header, payload, signature] = segments as [string, string, string];
  try {
    return {
      header: JSON.parse(Buffer.from(header, 'base64url').toString('utf8')),
      payload: JSON.parse(Buffer.from(payload, 'base64url').toString('utf8')),
      signingInput: `${header}.${payload}`,
      signature: Buffer.from(signature, 'base64url'),
    };
  } catch {
    throw malformed();
  }
}

export function verifyAttemptSignature(
  token: DecodedAttemptToken,
  publicKey: KeyObject,
): boolean {
  return verify(
    null,
    Buffer.from(token.signingInput, 'ascii'),
    publicKey,
    token.signature,
  );
}

/** `kid` is derived from the public key, so it names the key it belongs to. */
export function signingKeyId(publicKey: KeyObject, prefix: string): string {
  const spki = publicKey.export({ type: 'spki', format: 'der' });
  return `${prefix}${createHash('sha256').update(spki).digest('base64url').slice(0, 22)}`;
}

/** A new Ed25519 signing key whose `kid` starts with `prefix` (which keyring it is). */
export function generateSigningKey(
  now: number,
  prefix: string,
): AttemptSigningKey {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  return {
    kid: signingKeyId(publicKey, prefix),
    privateKey: privateKey.export({ type: 'pkcs8', format: 'pem' }),
    publicKey: publicKey.export({ type: 'spki', format: 'pem' }),
    createdAt: now,
    retiredAt: null,
  };
}

export const generateAttemptSigningKey = (now: number): AttemptSigningKey =>
  generateSigningKey(now, 'hak_');
