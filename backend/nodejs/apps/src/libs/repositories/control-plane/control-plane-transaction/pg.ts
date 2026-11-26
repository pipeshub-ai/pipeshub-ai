import { inBoundOrg } from '../../agent-harness/durable-transaction/bound-org';
import {
  DeadlockDetected,
  LockNotAvailable,
  SerializationFailure,
  withUow,
} from '../../../db';
import type { ConfigChanged } from '../../../../modules/control-plane/domain/config-change';
import { ControlPlaneUnavailableError } from '../../../../modules/control-plane/domain/errors';
import type {
  ChangeOutbox,
  ConfigAuditLog,
} from '../../../../modules/control-plane/ports/config-audit-log';
import {
  ControlPlaneLog,
  silentControlPlaneLog,
} from '../../../../modules/control-plane/ports/log';
import type {
  Committed,
  ControlPlaneTransaction,
  WriteContext,
  Work,
} from '../../../../modules/control-plane/ports/transaction';
import {
  committedChange,
  concurrentlyChanged,
  DEFAULT_RETRY,
  isRetryable,
  logCommitted,
  RetryPolicy,
  StagedCpTx,
  stageAndApply,
  unaudited,
} from '../../../../modules/control-plane/adapters/staged-transaction';
import { PgTx, mapPgStoreError } from '../../pg';
import { assertOrg } from '../../pg-util';
import { durableCommits } from '../../agent-harness/durable-transaction/commits';
import { rowRegion } from '../../agent-harness/pg-documents/pg';

export interface PgControlPlaneTransactionOptions {
  audit: ConfigAuditLog;
  outbox?: ChangeOutbox;
  log?: ControlPlaneLog;
  retry?: RetryPolicy;
}

/** Another writer held a row past the lock timeout, or the database chose this unit to undo: plan the write again. */
const contended = (error: unknown): boolean =>
  error instanceof LockNotAvailable ||
  error instanceof SerializationFailure ||
  error instanceof DeadlockDetected;

/** The runtime re-ran a unit whose effects had already reached their stores. */
class EffectsAlreadyApplied extends Error {}

/**
 * The control-plane write on PostgreSQL: one unit of work as the org. The org's head row is claimed first
 * (`INSERT ... ON CONFLICT DO UPDATE`, its lock held to commit), so a second write of the org waits there and then
 * commits at the next revision; another org never waits. Stores, the audit chain and the outbox write through
 * `CpTx.session`, main's `Tx` for this unit, and the external effects run last, after the audit entry is staged.
 */
export class PgControlPlaneTransaction implements ControlPlaneTransaction {
  private readonly log: ControlPlaneLog;
  private readonly retry: RetryPolicy;

  constructor(private readonly options: PgControlPlaneTransactionOptions) {
    this.log = options.log ?? silentControlPlaneLog;
    this.retry = options.retry ?? DEFAULT_RETRY;
  }

  async run<T>(ctx: WriteContext, work: Work<T>): Promise<Committed<T>> {
    for (let attempt = 1; ; attempt += 1) {
      const state: {
        tx: StagedCpTx | null;
        changed: ConfigChanged | null;
        unit: object | null;
      } = { tx: null, changed: null, unit: null };
      try {
        const committed = await this.asOrg(ctx.orgId, () =>
          withUow({ write: true }, async (k) => {
            if (state.tx?.appliedAny === true)
              throw new EffectsAlreadyApplied();
            assertOrg(k, ctx.orgId);
            if (state.unit !== null) durableCommits.discard(state.unit);
            state.unit = k;
            durableCommits.begin(k);
            const { rows } = await k.query<{ revision: string }>(
              `/* pipeshub:harness.cp_org_head_claim */ INSERT INTO harness.cp_org_heads AS h (org_id, revision, updated_at, region)
                 VALUES ($1, 1, now(), $2)
               ON CONFLICT (org_id) DO UPDATE SET revision = h.revision + 1, updated_at = now()
               RETURNING revision::text`,
              [ctx.orgId, rowRegion()],
            );
            const revision = Number(rows[0]?.revision);
            const tx = new StagedCpTx(
              ctx.orgId,
              revision,
              new PgTx(k),
              (write) => write(),
            );
            state.tx = tx;
            state.changed = null;
            const { result, change } = await work(tx);
            const changed = committedChange(ctx, revision, change);
            state.changed = changed;
            await stageAndApply(
              tx,
              changed,
              this.options.audit,
              this.options.outbox,
            );
            return { result, headRevision: revision };
          }),
        );
        if (state.changed !== null) logCommitted(this.log, state.changed);
        if (state.unit !== null) await durableCommits.committed(state.unit);
        if (state.tx !== null) await this.options.outbox?.committed(state.tx);
        return committed;
      } catch (error) {
        if (state.tx?.appliedAny === true) {
          throw unaudited(
            this.log,
            state.changed,
            ctx.orgId,
            state.tx.headRevision,
            error,
          );
        }
        const again =
          isRetryable(error) ||
          contended(error) ||
          (error instanceof ControlPlaneUnavailableError &&
            contended(error.cause));
        if (!again) throw mapPgStoreError(error);
        if (attempt >= this.retry.attempts) throw concurrentlyChanged(error);
        await this.retry.pause(attempt);
      } finally {
        if (state.unit !== null) durableCommits.discard(state.unit);
      }
    }
  }

  /** A write runs as its org, the one its caller bound (a request's, or a job's from its own enumeration). */
  private asOrg<R>(orgId: string, fn: () => Promise<R>): Promise<R> {
    return inBoundOrg(orgId, fn);
  }
}
