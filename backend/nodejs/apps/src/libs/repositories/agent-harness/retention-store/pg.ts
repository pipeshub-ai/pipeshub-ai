import { TERMINAL_RUN_STATUSES } from '../../../../modules/agent-harness/domain/execution';
import type { RetentionStore } from '../../../../modules/agent-harness/ports/session-retention';
import { jobsUnit } from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';

const REASON = 'harness retention';
const TERMINAL = [...TERMINAL_RUN_STATUSES];

/**
 * T5 on PostgreSQL, reference-aware (I8), across every org as the jobs role: nothing a run that can still write may
 * read is collected. A run is either terminal, which can append nothing more, or absent, which only an aborted
 * admission leaves. Each pass is one statement that picks its rows and deletes them, re-checking `referenced` and the
 * writer's state under the row locks, so an append that marks a payload meanwhile keeps it.
 */
export class PgRetentionStore implements RetentionStore {
  constructor(private readonly time: PgTime = pgServerTime) {}

  now(): Promise<number> {
    return jobsUnit(REASON, { write: false }, (k) => this.time.now(k));
  }

  collectPayloads(before: number, limit: number): Promise<number> {
    return jobsUnit(REASON, { write: true }, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.retention_collect_payloads */ DELETE FROM harness.journal_payloads p
          WHERE (p.org_id, p.id) IN (
            SELECT c.org_id, c.id FROM harness.journal_payloads c
             WHERE c.referenced = false AND c.created_at < to_timestamp($1::double precision / 1000)
             ORDER BY c.created_at LIMIT $2 FOR UPDATE SKIP LOCKED)
            AND p.referenced = false
            AND NOT EXISTS (SELECT 1 FROM harness.runs r
              WHERE r.org_id = p.org_id AND r.run_id = p.owner_run_id AND NOT (r.status = ANY($3::text[])))`,
        [before, limit, TERMINAL],
      );
      return rowCount ?? 0;
    });
  }

  expireCommandReceipts(before: number, limit: number): Promise<number> {
    return jobsUnit(REASON, { write: true }, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.retention_command_receipts */ DELETE FROM harness.command_receipts c
          WHERE (c.org_id, c.run_id, c.command_id) IN (
            SELECT o.org_id, o.run_id, o.command_id FROM harness.command_receipts o
             WHERE o.created_at < $1 ORDER BY o.created_at LIMIT $2 FOR UPDATE SKIP LOCKED)
            AND NOT EXISTS (SELECT 1 FROM harness.runs r
              WHERE r.org_id = c.org_id AND r.run_id = c.run_id AND NOT (r.status = ANY($3::text[])))`,
        [before, limit, TERMINAL],
      );
      return rowCount ?? 0;
    });
  }

  expireAdmissionReceipts(before: number, limit: number): Promise<number> {
    return jobsUnit(REASON, { write: true }, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.retention_admission_receipts */ DELETE FROM harness.admission_receipts a
          WHERE (a.org_id, a.scope_key) IN (
            SELECT o.org_id, o.scope_key FROM harness.admission_receipts o
             WHERE o.created_at < $1 ORDER BY o.created_at LIMIT $2 FOR UPDATE SKIP LOCKED)`,
        [before, limit],
      );
      return rowCount ?? 0;
    });
  }

  harnessOrgs(): Promise<string[]> {
    return jobsUnit(REASON, { write: false }, async (k) => {
      const { rows } = await k.query<{ org_id: string }>(
        `/* pipeshub:harness.retention_harness_orgs */ SELECT DISTINCT org_id FROM harness.data_keys ORDER BY org_id`,
      );
      return rows.map((row) => row.org_id);
    });
  }
}
