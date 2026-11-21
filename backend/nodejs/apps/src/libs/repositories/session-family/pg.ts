/**
 * PostgreSQL implementation of the session families (`DOC_STORE=postgres`). The organisation comes from the verified
 * refresh token; the row policy holds every call to it. Rotation and revocation are single conditional updates, so of
 * two racing refreshes exactly one moves the family.
 */
import { inOrgUnit } from '../oauth-app/pg-org';
import { stripNul } from '../strip-nul';
import type {
  NewSessionFamily,
  SessionFamily,
  SessionFamilyRepository,
} from './types';

interface Row {
  org_id: string;
  family_id: string;
  user_id: string;
  current_token_id: string;
  previous_token_id: string | null;
  rotated_at: Date | null;
  expires_at: Date;
  revoked_at: Date | null;
  revoked_reason: string | null;
}

const plain = (r: Row): SessionFamily => ({
  orgId: r.org_id,
  familyId: r.family_id,
  userId: r.user_id,
  currentTokenId: r.current_token_id,
  previousTokenId: r.previous_token_id,
  rotatedAt: r.rotated_at === null ? null : new Date(r.rotated_at),
  expiresAt: new Date(r.expires_at),
  revokedAt: r.revoked_at === null ? null : new Date(r.revoked_at),
  revokedReason: r.revoked_reason,
});

export class PgSessionFamilyRepository implements SessionFamilyRepository {
  async start(family: NewSessionFamily): Promise<boolean> {
    const f = stripNul(family, 'session-family', 'row');
    return inOrgUnit(f.orgId, undefined, true, async (k) => {
      const now = new Date();
      const { rows } = await k.query(
        `/* pipeshub:identity.session_family_start */ INSERT INTO identity.session_families
           (org_id, family_id, user_id, current_token_id, previous_token_id, rotated_at, expires_at, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
           ON CONFLICT (org_id, family_id) DO NOTHING RETURNING family_id`,
        [
          k.orgId,
          f.familyId,
          f.userId,
          f.currentTokenId,
          f.previousTokenId,
          f.rotatedAt,
          f.expiresAt,
          now,
        ],
      );
      return rows.length > 0;
    });
  }

  async get(orgId: string, familyId: string): Promise<SessionFamily | null> {
    const [o, id] = stripNul([orgId, familyId], 'session-family', 'key');
    return inOrgUnit(o, undefined, false, async (k) => {
      const { rows } = await k.query<Row>(
        '/* pipeshub:identity.session_family_get */ SELECT * FROM identity.session_families WHERE org_id = $1 AND family_id = $2',
        [k.orgId, id],
      );
      return rows[0] ? plain(rows[0]) : null;
    });
  }

  async rotate(
    orgId: string,
    familyId: string,
    fromTokenId: string,
    toTokenId: string,
    at: Date,
  ): Promise<boolean> {
    const [o, id, from, to] = stripNul(
      [orgId, familyId, fromTokenId, toTokenId],
      'session-family',
      'key',
    );
    return inOrgUnit(o, undefined, true, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:identity.session_family_rotate */ UPDATE identity.session_families f
           SET current_token_id = $4, previous_token_id = $3, rotated_at = $5, updated_at = $5, version = f.version + 1
           WHERE f.org_id = $1 AND f.family_id = $2 AND f.current_token_id = $3 AND f.revoked_at IS NULL`,
        [k.orgId, id, from, to, at],
      );
      return (rowCount ?? 0) === 1;
    });
  }

  async revoke(
    orgId: string,
    familyId: string,
    reason: string,
    at: Date,
  ): Promise<boolean> {
    const [o, id, why] = stripNul(
      [orgId, familyId, reason],
      'session-family',
      'key',
    );
    return inOrgUnit(o, undefined, true, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:identity.session_family_revoke */ UPDATE identity.session_families f
           SET revoked_at = $3, revoked_reason = $4, updated_at = $3, version = f.version + 1
           WHERE f.org_id = $1 AND f.family_id = $2 AND f.revoked_at IS NULL`,
        [k.orgId, id, at, why],
      );
      return (rowCount ?? 0) === 1;
    });
  }
}
