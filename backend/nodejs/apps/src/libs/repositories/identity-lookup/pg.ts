/**
 * PostgreSQL implementation of the lookups made before an org is known. An install has one organisation: a call with none bound
 * runs as it (see ../oauth-app/pg-org.ts), and a key of another org finds nothing. An extension that serves several organisations
 * replaces this with a lookup that finds the owner of a key.
 */
import { createHash } from 'crypto';
import { goodKey, inKeyUnit, installOrg } from '../oauth-app/pg-org';
import type { OAuthAppStatus } from '../oauth-app/types';
import type {
  IdentityLookup,
  ResolvedAccessToken,
  ResolvedClient,
  ResolvedRefreshToken,
} from './types';

/** SHA-256 (hex) of a raw secret, or null when it is not a usable string (empty or holding U+0000). */
const sha = (raw: unknown): string | null =>
  typeof raw === 'string' && raw.length > 0 && !raw.includes('\u0000')
    ? createHash('sha256').update(raw).digest('hex')
    : null;

export class PgIdentityLookup implements IdentityLookup {
  async resolveToken(tokenHash: string): Promise<ResolvedAccessToken | null> {
    if (!goodKey(tokenHash)) return null;
    return inKeyUnit<ResolvedAccessToken | null>(
      installOrg,
      false,
      null,
      async (k) => {
        const { rows } = await k.query<{
          id: string;
          user_id: string | null;
          client_id: string;
          scopes: string[];
          expires_at: Date;
          last_used_at: Date | null;
        }>(
          `/* pipeshub:oauth.resolve_token */ SELECT id, user_id, client_id, scopes, expires_at, last_used_at FROM oauth.access_tokens
            WHERE org_id = $1 AND token_hash = $2 AND NOT is_revoked AND expires_at > now() LIMIT 1`,
          [k.orgId, tokenHash],
        );
        const r = rows[0];
        return r
          ? {
              _id: r.id,
              orgId: k.orgId,
              userId: r.user_id ?? undefined,
              clientId: r.client_id,
              scopes: r.scopes,
              expiresAt: r.expires_at,
              lastUsedAt: r.last_used_at ?? undefined,
            }
          : null;
      },
    );
  }

  async resolveRefresh(
    tokenHash: string,
  ): Promise<ResolvedRefreshToken | null> {
    if (!goodKey(tokenHash)) return null;
    return inKeyUnit<ResolvedRefreshToken | null>(
      installOrg,
      false,
      null,
      async (k) => {
        const { rows } = await k.query<{
          id: string;
          user_id: string;
          client_id: string;
          scopes: string[];
          expires_at: Date;
          rotation_count: number;
        }>(
          `/* pipeshub:oauth.resolve_refresh */ SELECT id, user_id, client_id, scopes, expires_at, rotation_count FROM oauth.refresh_tokens
            WHERE org_id = $1 AND token_hash = $2 AND NOT is_revoked AND expires_at > now() LIMIT 1`,
          [k.orgId, tokenHash],
        );
        const r = rows[0];
        return r
          ? {
              _id: r.id,
              orgId: k.orgId,
              userId: r.user_id,
              clientId: r.client_id,
              scopes: r.scopes,
              expiresAt: r.expires_at,
              rotationCount: r.rotation_count,
            }
          : null;
      },
    );
  }

  async resolveCodeOrg(code: string, clientId: string): Promise<string | null> {
    const hash = sha(code);
    if (hash === null || !goodKey(clientId)) return null;
    return inKeyUnit<string | null>(installOrg, false, null, async (k) => {
      // used and expired rows count: replay and expiry are answered from the row
      const { rows } = await k.query(
        '/* pipeshub:oauth.resolve_code */ SELECT 1 FROM oauth.authorization_codes WHERE org_id = $1 AND code_hash = $2 AND client_id = $3 LIMIT 1',
        [k.orgId, hash, clientId],
      );
      return rows.length > 0 ? k.orgId : null;
    });
  }

  /** The raw token is hashed first: a real refresh token is a signed token of about 500 characters, the stored key is its 64-character hash. */
  async resolveRefreshOrg(refreshToken: string): Promise<string | null> {
    const hash = sha(refreshToken);
    if (hash === null) return null;
    // a revoked or expired row counts: a replayed token must run its grant as the org that issued it, to be refused there
    return inKeyUnit<string | null>(installOrg, false, null, async (k) => {
      const { rows } = await k.query(
        '/* pipeshub:oauth.resolve_refresh_org */ SELECT 1 FROM oauth.refresh_tokens WHERE org_id = $1 AND token_hash = $2 LIMIT 1',
        [k.orgId, hash],
      );
      return rows.length > 0 ? k.orgId : null;
    });
  }

  async resolveClient(clientId: string): Promise<ResolvedClient | null> {
    if (!goodKey(clientId)) return null;
    return inKeyUnit<ResolvedClient | null>(
      installOrg,
      false,
      null,
      async (k) => {
        const { rows } = await k.query<{ id: string; status: OAuthAppStatus }>(
          '/* pipeshub:oauth.resolve_client */ SELECT id, status FROM oauth.apps WHERE org_id = $1 AND client_id = $2 AND NOT is_deleted LIMIT 1',
          [k.orgId, clientId],
        );
        const r = rows[0];
        return r ? { orgId: k.orgId, appId: r.id, status: r.status } : null;
      },
    );
  }
}
