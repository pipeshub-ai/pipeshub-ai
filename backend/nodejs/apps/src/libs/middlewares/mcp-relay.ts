import { createHash, createSecretKey, KeyObject } from 'node:crypto';
import { Request } from 'express';
import jwt from 'jsonwebtoken';
import { TokenScopes } from '../enums/token-scopes.enum';

/**
 * Marks an API call the /mcp handler makes on behalf of a tool call. Each tool call reaches the
 * REST API over loopback with the caller's own bearer token; this header says "this request is
 * part of an /mcp call already counted and authorised", bound to that token so it cannot be
 * lifted onto another one.
 */
export const MCP_RELAY_HEADER = 'x-pipeshub-mcp-relay';

const RELAY_LIFETIME_SECONDS = 300;

const tokenHash = (bearer: string): string =>
  createHash('sha256').update(bearer).digest('base64url');

const bearerOf = (req: Request): string | undefined => {
  const header = req.headers.authorization;
  return typeof header === 'string' && header.startsWith('Bearer ')
    ? header.slice('Bearer '.length)
    : undefined;
};

export const relayKey = (scopedJwtSecret: string): KeyObject =>
  createSecretKey(Buffer.from(scopedJwtSecret));

/** The header value for API calls made with `bearer` during one /mcp request. */
export function signMcpRelay(key: KeyObject, bearer: string): string {
  return jwt.sign(
    { scopes: [TokenScopes.MCP_RELAY], th: tokenHash(bearer) },
    key,
    { algorithm: 'HS256', expiresIn: RELAY_LIFETIME_SECONDS },
  );
}

/** Whether `req` carries a valid relay header for the bearer token it presents. */
export function isMcpRelay(req: Request, key: KeyObject | null): boolean {
  const header = req.headers[MCP_RELAY_HEADER];
  const bearer = bearerOf(req);
  if (!key || typeof header !== 'string' || bearer === undefined) return false;
  try {
    const claims = jwt.verify(header, key, { algorithms: ['HS256'] });
    if (typeof claims !== 'object') return false;
    const { scopes, th } = claims as { scopes?: unknown; th?: unknown };
    return (
      Array.isArray(scopes) &&
      scopes.includes(TokenScopes.MCP_RELAY) &&
      th === tokenHash(bearer)
    );
  } catch {
    return false;
  }
}
