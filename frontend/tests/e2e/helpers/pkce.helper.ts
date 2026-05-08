import crypto from 'crypto';

/**
 * RFC 7636 PKCE pair for public OAuth clients (same shape the MCP stack uses with S256).
 */
export function generatePkceS256(): { codeVerifier: string; codeChallenge: string } {
  const codeVerifier = crypto.randomBytes(32).toString('base64url');
  const codeChallenge = crypto
    .createHash('sha256')
    .update(codeVerifier)
    .digest('base64url');
  return { codeVerifier, codeChallenge };
}
