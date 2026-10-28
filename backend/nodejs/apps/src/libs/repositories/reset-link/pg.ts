/**
 * PostgreSQL implementation of the reset-link repository (`DOC_STORE=postgres`). Claiming is an insert that does nothing on a
 * duplicate and reports whether it wrote the row: of two simultaneous requests exactly one gets `true`. The link names its
 * organisation (the signed link carries it), so the claim runs in that organisation's unit of work.
 */
import { assertOrg, inUnit } from '../pg-util';
import type { ResetLinkRepository } from './types';

export class PgResetLinkRepository implements ResetLinkRepository {
  async claim(input: {
    linkHash: string;
    userId: string;
    orgId: string;
    expiresAt: Date;
  }): Promise<boolean> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, input.orgId);
      const now = new Date();
      const { rows } = await k.query(
        `/* pipeshub:identity.reset_link_claim */ INSERT INTO identity.used_reset_links (org_id, link_hash, user_id, expires_at, created_at, updated_at)
          VALUES ($1, $2, $3, $4, $5, $5) ON CONFLICT (org_id, link_hash) DO NOTHING RETURNING link_hash`,
        [k.orgId, input.linkHash, input.userId, input.expiresAt, now],
      );
      return rows.length > 0;
    });
  }

  async release(linkHash: string): Promise<void> {
    await inUnit(undefined, { write: true }, (k) =>
      k.query(
        '/* pipeshub:identity.reset_link_release */ DELETE FROM identity.used_reset_links l WHERE l.org_id = $1 AND l.link_hash = $2',
        [k.orgId, linkHash],
      ),
    );
  }
}
