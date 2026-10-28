/**
 * PostgreSQL implementation of the delegation repository (`DOC_STORE=postgres`): the allow-list of subjects a service account may
 * act as, and the log of exchange attempts. Every statement runs in the caller's org unit; a row of another org is invisible.
 * A grant that already exists comes back as it is stored (`created: false`), settled by `ON CONFLICT` so no transaction aborts.
 */
import { ValidationError } from '../../errors/validation.error';
import { stripNul } from '../nul';
import { assertOrg, inUnit, newObjectId, oidOf } from '../pg-util';
import type {
  DelegationEventDto,
  DelegationOutcome,
  DelegationRepository,
  DelegationRowDto,
  NewDelegationEvent,
} from './types';

interface RowRec {
  id: string;
  org_id: string;
  service_account_id: string;
  subject_user_id: string;
  granted_by: string;
  reason: string | null;
  created_at: Date;
  updated_at: Date;
}
interface EventRec {
  id: string;
  org_id: string;
  service_account_id: string;
  subject_user_id: string | null;
  requested_subject: string;
  outcome: DelegationOutcome;
  refused_reason: string | null;
  acted_by: string | null;
  note: string | null;
  scopes: string[];
  access_token_id: string | null;
  jti: string | null;
  expires_at: Date | null;
  created_at: Date;
}

const REASON_MAX = 280;

const ROW_COLS =
  'id, org_id, service_account_id, subject_user_id, granted_by, reason, created_at, updated_at';
const EVENT_COLS =
  'id, org_id, service_account_id, subject_user_id, requested_subject, outcome, refused_reason, acted_by, note, scopes, access_token_id, jti, expires_at, created_at';

const rowDto = (r: RowRec): DelegationRowDto => ({
  _id: r.id,
  orgId: r.org_id,
  serviceAccountId: r.service_account_id,
  subjectUserId: r.subject_user_id,
  grantedBy: r.granted_by,
  ...(r.reason !== null && { reason: r.reason }),
  createdAt: r.created_at,
  updatedAt: r.updated_at,
});

const eventDto = (r: EventRec): DelegationEventDto => ({
  _id: r.id,
  orgId: r.org_id,
  serviceAccountId: r.service_account_id,
  ...(r.subject_user_id !== null && { subjectUserId: r.subject_user_id }),
  requestedSubject: r.requested_subject,
  outcome: r.outcome,
  ...(r.refused_reason !== null && { refusedReason: r.refused_reason }),
  ...(r.acted_by !== null && { actedBy: r.acted_by }),
  ...(r.note !== null && { note: r.note }),
  scopes: r.scopes,
  ...(r.access_token_id !== null && { accessTokenId: r.access_token_id }),
  ...(r.jti !== null && { jti: r.jti }),
  ...(r.expires_at !== null && { expiresAt: r.expires_at }),
  createdAt: r.created_at,
});

/** An id that is not well formed matches nothing (a lookup) or is refused (a write). */
const idOrNone = (v: string): string => oidOf(v) ?? '';

export class PgDelegationRepository implements DelegationRepository {
  async isAllowed(
    orgId: string,
    serviceAccountId: string,
    subjectUserId: string,
  ): Promise<boolean> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rowCount } = await k.query(
        '/* pipeshub:oauth.delegation_allowed */ SELECT 1 FROM oauth.service_account_delegations WHERE org_id = $1 AND service_account_id = $2 AND subject_user_id = $3',
        [orgId, idOrNone(serviceAccountId), idOrNone(subjectUserId)],
      );
      return (rowCount ?? 0) > 0;
    });
  }

  async countFor(orgId: string, serviceAccountId: string): Promise<number> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ n: string }>(
        '/* pipeshub:oauth.delegation_count */ SELECT count(*) AS n FROM oauth.service_account_delegations WHERE org_id = $1 AND service_account_id = $2',
        [orgId, idOrNone(serviceAccountId)],
      );
      return Number(rows[0]?.n ?? 0);
    });
  }

  async grant(input: {
    orgId: string;
    serviceAccountId: string;
    subjectUserId: string;
    grantedBy: string;
    reason?: string;
  }): Promise<{ row: DelegationRowDto; created: boolean }> {
    const i = stripNul(input, 'delegation', 'grant');
    // the document model trimmed the reason and refused one past 280 characters
    const reason = i.reason?.trim();
    if (reason !== undefined && reason.length > REASON_MAX)
      throw new ValidationError('reason is longer than 280 characters', []);
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, i.orgId);
      const now = new Date();
      const inserted = await k.query<RowRec>(
        `/* pipeshub:oauth.delegation_grant */ INSERT INTO oauth.service_account_delegations (org_id, id, service_account_id, subject_user_id, granted_by, reason, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
           ON CONFLICT (org_id, service_account_id, subject_user_id) DO NOTHING
           RETURNING ${ROW_COLS}`,
        [
          i.orgId,
          newObjectId(),
          i.serviceAccountId,
          i.subjectUserId,
          i.grantedBy,
          reason ?? null,
          now,
        ],
      );
      if (inserted.rows[0])
        return { row: rowDto(inserted.rows[0]), created: true };
      const { rows } = await k.query<RowRec>(
        `/* pipeshub:oauth.delegation_grant_existing */ SELECT ${ROW_COLS} FROM oauth.service_account_delegations WHERE org_id = $1 AND service_account_id = $2 AND subject_user_id = $3`,
        [i.orgId, i.serviceAccountId, i.subjectUserId],
      );
      if (!rows[0]) throw new Error('delegation grant: the row vanished');
      return { row: rowDto(rows[0]), created: false };
    });
  }

  async listFor(
    orgId: string,
    serviceAccountId: string,
  ): Promise<DelegationRowDto[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<RowRec>(
        `/* pipeshub:oauth.delegation_list */ SELECT ${ROW_COLS} FROM oauth.service_account_delegations WHERE org_id = $1 AND service_account_id = $2 ORDER BY created_at DESC, id DESC`,
        [orgId, idOrNone(serviceAccountId)],
      );
      return rows.map(rowDto);
    });
  }

  async revoke(
    orgId: string,
    serviceAccountId: string,
    subjectUserId: string,
  ): Promise<boolean> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rowCount } = await k.query(
        '/* pipeshub:oauth.delegation_revoke */ DELETE FROM oauth.service_account_delegations WHERE org_id = $1 AND service_account_id = $2 AND subject_user_id = $3',
        [orgId, idOrNone(serviceAccountId), idOrNone(subjectUserId)],
      );
      return (rowCount ?? 0) > 0;
    });
  }

  async revokeAllFor(orgId: string, serviceAccountId: string): Promise<number> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const { rowCount } = await k.query(
        '/* pipeshub:oauth.delegation_revoke_all */ DELETE FROM oauth.service_account_delegations WHERE org_id = $1 AND service_account_id = $2',
        [orgId, idOrNone(serviceAccountId)],
      );
      return rowCount ?? 0;
    });
  }

  async recordEvent(event: NewDelegationEvent): Promise<void> {
    const e = stripNul(event, 'delegation', 'event');
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, e.orgId);
      const now = new Date();
      await k.query(
        `/* pipeshub:oauth.delegation_event */ INSERT INTO oauth.delegation_events (org_id, id, service_account_id, subject_user_id, requested_subject, outcome, refused_reason, acted_by, note, scopes, access_token_id, jti, expires_at, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $14)`,
        [
          e.orgId,
          newObjectId(),
          e.serviceAccountId,
          e.subjectUserId ?? null,
          e.requestedSubject.slice(0, 320),
          e.outcome,
          e.refusedReason ?? null,
          e.actedBy ?? null,
          e.note ?? null,
          e.scopes ?? [],
          e.accessTokenId ?? null,
          e.jti ?? null,
          e.expiresAt ?? null,
          now,
        ],
      );
    });
  }

  async listEvents(
    orgId: string,
    serviceAccountId: string | undefined,
    limit: number,
  ): Promise<DelegationEventDto[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<EventRec>(
        `/* pipeshub:oauth.delegation_events_list */ SELECT ${EVENT_COLS} FROM oauth.delegation_events
           WHERE org_id = $1 AND ($2::text IS NULL OR service_account_id = $2)
           ORDER BY created_at DESC, id DESC LIMIT $3`,
        [
          orgId,
          serviceAccountId === undefined ? null : idOrNone(serviceAccountId),
          limit,
        ],
      );
      return rows.map(eventDto);
    });
  }
}
