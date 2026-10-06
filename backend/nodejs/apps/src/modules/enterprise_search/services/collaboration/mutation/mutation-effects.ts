import { ClientSession } from 'mongoose';
import {
  AuditEventInput,
  IAuditWriter,
} from '../../../../../libs/audit/audit.writer';
import { Logger } from '../../../../../libs/services/logger.service';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ICollaborationNotifier } from '../notify/collaboration-notifier';
import { CollaborationEvent } from '../notify/events';

const defaultLogger = Logger.getInstance({ service: 'MutationEffects' });

/**
 * The seam every collaboration mutation emits through, after its repository write:
 * audit first, then the events. With a session both are part of the transaction and
 * a failure aborts it; without one the change already happened, so a failure is logged.
 */
export class MutationEffects {
  constructor(
    private readonly audit: IAuditWriter,
    private readonly notifier: ICollaborationNotifier,
    private readonly logger: Pick<Logger, 'error'> = defaultLogger,
  ) {}

  recordAudit(
    event: AuditEventInput,
    session: ClientSession | undefined,
  ): Promise<void> {
    return this.audit.record(event, session ? { session } : {});
  }

  async publish(
    events: readonly CollaborationEvent[],
    session: ClientSession | undefined,
    identity?: CallerIdentity,
  ): Promise<void> {
    if (events.length === 0) {
      return;
    }
    try {
      await this.notifier.publish(events, {
        ...(identity && { identity }),
        ...(session && { session }),
      });
    } catch (error) {
      if (session) {
        throw error;
      }
      this.logger.error('Failed to publish collaboration events', {
        types: events.map((e) => e.type),
        sessionId: events[0]?.sessionId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
}
