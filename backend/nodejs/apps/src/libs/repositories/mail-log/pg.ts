/**
 * PostgreSQL implementation of the mail log (`DOC_STORE=postgres`). The row belongs to the org of the unit it is written in:
 * an entry that names another org, or a call with no org at all, is refused (the caller logs that the audit record was not saved).
 */
import { stripNul } from '../nul';
import { assertOrg, inUnit, newObjectId } from '../pg-util';
import type { MailLogEntry, MailLogRepository } from './types';

export class PgMailLogRepository implements MailLogRepository {
  async record(entry: MailLogEntry): Promise<void> {
    const e = stripNul(entry, 'mail-log', 'entry');
    await inUnit(undefined, { write: true }, async (k) => {
      if (e.orgId !== undefined) assertOrg(k, e.orgId);
      const now = new Date();
      await k.query(
        `/* pipeshub:platform.mail_log_record */ INSERT INTO platform.mail_log
           (org_id, id, created_at, subject, from_address, to_addresses, cc_addresses, email_template_type, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $3)`,
        [
          k.orgId,
          newObjectId(),
          now,
          e.subject ?? null,
          e.from,
          e.to ?? [],
          e.cc ?? [],
          e.emailTemplateType,
        ],
      );
    });
  }
}
