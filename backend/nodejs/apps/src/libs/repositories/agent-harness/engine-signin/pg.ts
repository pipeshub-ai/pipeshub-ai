import type { StoredEngineSignIn } from '../../../../modules/agent-harness/domain/engine-signin';
import type { EngineSignInStore } from '../../../../modules/agent-harness/ports/engine-signins';
import { getUserRepository } from '../../user';
import { inBoundOrgUnit } from '../pg-documents/ambient';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import { decodeDoc, docParam, rowRegion } from '../pg-documents/pg';

const userSubject = (userId: string): string => `u:${userId}`;
const ORG_SUBJECT = 'org';
const REASON = 'engine sign-ins';

/**
 * SIGNIN's records on PostgreSQL (`harness.engine_signins`): one row per person and one for the org's switch, apart
 * from the user row, so no user listing ever carries a person's Claude email. Whether the person is a live member and
 * a service account is main's user repository's answer.
 */
export class PgEngineSignIns implements EngineSignInStore {
  async read(
    orgId: string,
    userId: string,
  ): Promise<{ stored: StoredEngineSignIn; serviceAccount: boolean } | null> {
    const [user, doc] = await Promise.all([
      inBoundOrgUnit(orgId, () => getUserRepository().getLive(orgId, userId)),
      this.doc(orgId, userSubject(userId)),
    ]);
    if (user === null) return null;
    const stored: StoredEngineSignIn = { enabled: doc?.enabled === true };
    if (doc?.email != null) stored.email = doc.email;
    if (doc?.plan != null) stored.plan = doc.plan;
    if (doc?.signedInAt != null) stored.signedInAt = doc.signedInAt;
    if (doc?.disconnectedAt != null) stored.disconnectedAt = doc.disconnectedAt;
    return { stored, serviceAccount: user.kind === 'service' };
  }

  async write(
    orgId: string,
    userId: string,
    next: StoredEngineSignIn,
  ): Promise<void> {
    // A replacement: a field the next state lacks (the email after a disconnect) is gone.
    await this.put(orgId, userSubject(userId), userId, { ...next });
  }

  signedInSince(orgId: string, since: number): Promise<string[]> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ user_id: string }>(
          `/* pipeshub:harness.engine_signins_since */ SELECT user_id FROM harness.engine_signins
            WHERE org_id = $1 AND user_id IS NOT NULL AND enabled AND signed_in_at >= $2
            ORDER BY user_id`,
          [orgId, since],
        );
        return rows.map((row) => row.user_id);
      },
    );
  }

  async orgAllows(orgId: string): Promise<boolean> {
    return (await this.doc(orgId, ORG_SUBJECT))?.claude === true;
  }

  async setOrgAllows(orgId: string, allowed: boolean): Promise<void> {
    await this.put(orgId, ORG_SUBJECT, null, { claude: allowed });
  }

  async eraseUser(orgId: string, userId: string): Promise<void> {
    await this.remove(orgId, userSubject(userId));
  }

  async eraseOrg(orgId: string): Promise<void> {
    await this.remove(orgId, null);
  }

  private doc(
    orgId: string,
    subject: string,
  ): Promise<(StoredEngineSignIn & { claude?: boolean }) | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.engine_signins_get */ SELECT doc FROM harness.engine_signins
            WHERE org_id = $1 AND subject = $2`,
          [orgId, subject],
        );
        return rows[0] === undefined ? null : decodeDoc(rows[0].doc);
      },
    );
  }

  private async put(
    orgId: string,
    subject: string,
    userId: string | null,
    doc: Record<string, unknown>,
  ): Promise<void> {
    await orgOrJobsUnit(undefined, orgId, REASON, { write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.engine_signins_put */ INSERT INTO harness.engine_signins (org_id, subject, user_id, doc, region)
         VALUES ($1, $2, $3, $4::jsonb, $5)
         ON CONFLICT (org_id, subject) DO UPDATE SET doc = EXCLUDED.doc`,
        [orgId, subject, userId, docParam(doc), rowRegion()],
      ),
    );
  }

  private async remove(orgId: string, subject: string | null): Promise<void> {
    await orgOrJobsUnit(undefined, orgId, REASON, { write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.engine_signins_erase */ DELETE FROM harness.engine_signins
          WHERE org_id = $1 AND ($2::text IS NULL OR subject = $2)`,
        [orgId, subject],
      ),
    );
  }
}
