import type { FollowUpHandler } from './types';

/** Handlers for the follow-ups the identity importers return. */
/**
 * `setval` needs more than the jobs or node login hold on the sequence, so the migrate login does it, as the schema owner. Never lowers
 * the sequence (a restart after the application already created organisations must not hand out a number twice) and stays above every
 * `org-<n>` slug held now.
 */
export const SEED_ORG_SLUG_SEQ = `SELECT setval('identity.org_slug_seq',
  GREATEST($1::bigint + 1, 1001,
           (SELECT COALESCE(max(substring(slug from '[0-9]+$')::bigint), 0) + 1 FROM identity.orgs WHERE slug ~ '^org-[0-9]+$'),
           (SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END FROM identity.org_slug_seq)), false)`;

export const IDENTITY_FOLLOW_UPS: Record<string, FollowUpHandler> = {
  seed_org_slug_seq: async (rt, f) => {
    const c = await rt.migratePool.connect();
    try {
      await c.query('BEGIN');
      await c.query('SET LOCAL ROLE pipeshub_owner');
      await c.query(SEED_ORG_SLUG_SEQ, [Number(f.seq)]);
      await c.query('COMMIT');
    } catch (e) {
      await c.query('ROLLBACK').catch(() => undefined);
      throw e;
    } finally {
      c.release();
    }
  },
};
