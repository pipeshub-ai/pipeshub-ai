import type { OrgDeletedAnnouncer } from '../../../../modules/agent-harness/ports/principal-erasure';
import {
  Event,
  EventType,
  OrgDeletedEvent,
} from '../../../../modules/user_management/services/entity_events.service';
import { getOutboxRepository } from '../../outbox';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';

/**
 * The platform's `orgDeleted` entity event (CLEANUP-G1) on PostgreSQL: appended through main's outbox repository as
 * the deleted org (the row is the org's), as the user module writes it, for org erasure steps that announce the
 * deletion from the job.
 */
export class PgOrgDeletedAnnouncer implements OrgDeletedAnnouncer {
  async orgDeleted(orgId: string): Promise<void> {
    const payload: OrgDeletedEvent = { orgId };
    const event: Event = {
      eventType: EventType.OrgDeletedEvent,
      timestamp: Date.now(),
      payload,
    };
    await inBoundOrgUnit(orgId, () =>
      getOutboxRepository().append({
        topic: 'entity-events',
        key: event.eventType,
        orderingKey: `org:${orgId}`,
        value: JSON.stringify(event),
        headers: {
          eventType: event.eventType,
          timestamp: event.timestamp.toString(),
        },
        status: 'pending',
        attempts: 0,
        nextAttemptAt: new Date(),
      }),
    );
  }
}
