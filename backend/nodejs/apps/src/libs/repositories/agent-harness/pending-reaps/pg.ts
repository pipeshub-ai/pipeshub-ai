import { pendingReapId } from '../../../../modules/agent-harness/domain/pending-reaps';
import {
  PendingReap,
  PendingReaps,
} from '../../../../modules/agent-harness/ports/sandbox-reaper';
import { decodeDoc, deploymentUnit, docParam } from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';

/** `harness.sandbox_reaps`: refused reaps waiting for the broker (CLEANUP-G6), deployment plumbing. */
export class PgPendingReaps implements PendingReaps {
  constructor(private readonly time: PgTime = pgServerTime) {}

  now(): Promise<number> {
    return deploymentUnit({ write: false }, (k) => this.time.now(k));
  }

  async defer(
    labels: Readonly<Record<string, string>>,
    at: number,
    errorType: string,
  ): Promise<void> {
    await deploymentUnit({ write: true }, async (k) =>
      k.query(
        `/* pipeshub:harness.sandbox_reaps_defer */ INSERT INTO harness.sandbox_reaps
           (id, labels, failures, next_attempt_at, created_at, last_error_type) VALUES ($1, $2::jsonb, 1, $3, $4, $5)
         ON CONFLICT (id) DO NOTHING`,
        [
          pendingReapId(labels),
          docParam({ ...labels }),
          at,
          await this.time.now(k),
          errorType,
        ],
      ),
    );
  }

  due(now: number, limit: number): Promise<PendingReap[]> {
    return deploymentUnit({ write: false }, async (k) => {
      const { rows } = await k.query<{
        id: string;
        labels: unknown;
        failures: number;
      }>(
        `/* pipeshub:harness.sandbox_reaps_due */ SELECT id, labels, failures FROM harness.sandbox_reaps
          WHERE next_attempt_at <= $1 ORDER BY next_attempt_at, id LIMIT $2`,
        [now, limit],
      );
      return rows.map((row) => ({
        id: row.id,
        labels: decodeDoc<Record<string, string>>(row.labels),
        failures: row.failures,
      }));
    });
  }

  async done(id: string): Promise<void> {
    await deploymentUnit({ write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.sandbox_reaps_done */ DELETE FROM harness.sandbox_reaps WHERE id = $1`,
        [id],
      ),
    );
  }

  async later(id: string, at: number, errorType: string): Promise<void> {
    await deploymentUnit({ write: true }, (k) =>
      k.query(
        `/* pipeshub:harness.sandbox_reaps_later */ UPDATE harness.sandbox_reaps
            SET next_attempt_at = $2, last_error_type = $3, failures = failures + 1 WHERE id = $1`,
        [id, at, errorType],
      ),
    );
  }
}
