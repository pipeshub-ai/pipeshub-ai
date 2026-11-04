import { createHash } from 'crypto';

/**
 * The SHA-256 (hex) under which an opaque OAuth credential is stored and looked up: access and refresh tokens, authorization codes.
 * These are server-generated random values of 128 bits or more, never chosen by a person, so a fast digest is the right tool
 * (a slow password KDF protects low-entropy secrets and would only cost every request time). The one definition is shared by the
 * token service and the PostgreSQL repository.
 */
export const hashOpaqueToken = (token: string): string =>
  createHash('sha256').update(token).digest('hex');
