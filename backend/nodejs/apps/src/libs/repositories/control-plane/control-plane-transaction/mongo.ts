import mongoose, { ClientSession } from 'mongoose';
import type { TransactionOptions } from 'mongodb';
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
import { CpOrgHeadModel } from '../org-heads/schema/org-heads.schema';

const hasLabel = (error: unknown, label: string): boolean => {
  const labelled = error as {
    hasErrorLabel?: (name: string) => boolean;
    errorLabels?: unknown;
  } | null;
  if (labelled === null || typeof labelled !== 'object') return false;
  return typeof labelled.hasErrorLabel === 'function'
    ? labelled.hasErrorLabel(label)
    : Array.isArray(labelled.errorLabels) &&
        labelled.errorLabels.includes(label);
};

const COMMIT_ATTEMPTS = 3;

async function commit(session: ClientSession): Promise<void> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      await session.commitTransaction();
      return;
    } catch (error) {
      if (
        attempt >= COMMIT_ATTEMPTS ||
        !hasLabel(error, 'UnknownTransactionCommitResult')
      ) {
        throw error;
      }
    }
  }
}

export interface MongoControlPlaneTransactionOptions {
  audit: ConfigAuditLog;
  outbox?: ChangeOutbox;
  /** The deployment's durable transaction options (snapshot reads, majority writes). */
  transaction: TransactionOptions;
  log?: ControlPlaneLog;
  retry?: RetryPolicy;
}

/**
 * Replica-set adapter. The org head is claimed first, so two writes in one
 * org conflict there before either touches an external store; the effects
 * run last, after the audit entry is staged, so an audit failure aborts the
 * write before anything outside Mongo changes.
 */
export class MongoControlPlaneTransaction implements ControlPlaneTransaction {
  private readonly log: ControlPlaneLog;
  private readonly retry: RetryPolicy;

  constructor(private readonly options: MongoControlPlaneTransactionOptions) {
    this.log = options.log ?? silentControlPlaneLog;
    this.retry = options.retry ?? DEFAULT_RETRY;
  }

  async run<T>(ctx: WriteContext, work: Work<T>): Promise<Committed<T>> {
    for (let attempt = 1; ; attempt += 1) {
      const session = await mongoose.startSession();
      let tx: StagedCpTx | null = null;
      let changed: ConfigChanged | null = null;
      try {
        session.startTransaction(this.options.transaction);
        const head = await CpOrgHeadModel.findOneAndUpdate(
          { _id: ctx.orgId },
          { $inc: { revision: 1 }, $set: { updatedAt: new Date() } },
          {
            upsert: true,
            returnDocument: 'after',
            session,
            projection: { revision: 1 },
          },
        ).lean();
        // An upsert returning the new document always yields one; Mongoose 9 types the result as nullable.
        if (!head)
          throw new Error(
            `control-plane head for org ${ctx.orgId} was not written`,
          );
        tx = new StagedCpTx(ctx.orgId, head.revision, session, (write) =>
          write(),
        );
        const { result, change } = await work(tx);
        changed = committedChange(ctx, head.revision, change);
        await stageAndApply(
          tx,
          changed,
          this.options.audit,
          this.options.outbox,
        );
        await commit(session);
        logCommitted(this.log, changed);
        await this.options.outbox?.committed(tx);
        return { result, headRevision: head.revision };
      } catch (error) {
        if (session.inTransaction()) {
          await session.abortTransaction().catch(() => undefined);
        }
        if (tx?.appliedAny === true) {
          throw unaudited(this.log, changed, ctx.orgId, tx.headRevision, error);
        }
        const again =
          isRetryable(error) ||
          hasLabel(error, 'TransientTransactionError') ||
          (error instanceof ControlPlaneUnavailableError &&
            hasLabel(error.cause, 'TransientTransactionError'));
        if (!again) throw error;
        if (attempt >= this.retry.attempts) throw concurrentlyChanged(error);
        await this.retry.pause(attempt);
      } finally {
        await session.endSession();
      }
    }
  }
}
