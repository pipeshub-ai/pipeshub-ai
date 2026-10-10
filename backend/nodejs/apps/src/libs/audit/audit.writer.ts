import { ClientSession } from 'mongoose';
import { Logger } from '../services/logger.service';
import { AuditEvent, IAuditEvent } from './audit-event.schema';

export interface AuditEventInput
  extends Omit<IAuditEvent, 'createdAt' | 'schemaVersion' | 'after'> {
  after?: Record<string, unknown>;
  /** The session's `aclVersion` after the write; stored inside `after`. */
  aclVersion?: number;
}

export interface IAuditWriter {
  /**
   * Standalone: awaited, a failure is logged and swallowed because the audited
   * write already happened. With a session the row is part of the caller's
   * transaction, so a failure must propagate and abort it.
   */
  record(e: AuditEventInput, opts?: { session?: ClientSession }): Promise<void>;
}

const defaultLogger = Logger.getInstance({ service: 'AuditWriter' });

export class MongoAuditWriter implements IAuditWriter {
  constructor(private readonly logger: Pick<Logger, 'error'> = defaultLogger) {}

  async record(
    e: AuditEventInput,
    opts: { session?: ClientSession } = {},
  ): Promise<void> {
    const { aclVersion, after, ...rest } = e;
    const doc = {
      ...rest,
      ...((after !== undefined || aclVersion !== undefined) && {
        after: {
          ...after,
          ...(aclVersion !== undefined && { aclVersion }),
        },
      }),
    };
    try {
      await AuditEvent.create(
        [doc],
        opts.session ? { session: opts.session } : {},
      );
    } catch (error) {
      if (opts.session) {
        throw error;
      }
      this.logger.error('Failed to write audit event', {
        action: e.action,
        targetType: e.targetType,
        targetId: e.targetId,
        orgId: e.orgId.toString(),
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }
}
