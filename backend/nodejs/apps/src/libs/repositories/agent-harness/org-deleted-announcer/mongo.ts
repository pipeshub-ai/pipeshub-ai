import { OutboxEvent } from '../../outbox/schema/outbox.schema';
import { OrgDeletedAnnouncer } from '../../../../modules/agent-harness/ports/principal-erasure';
import {
  Event,
  EventType,
  OrgDeletedEvent,
} from '../../../../modules/user_management/services/entity_events.service';

/**
 * The platform's `orgDeleted` entity event (CLEANUP-G1), written to the
 * outbox the way the user module writes it, for org erasure steps that
 * announce the deletion from the job rather than at the request: the query
 * service's handler (the graph org, its publications, its MCP data) cannot be
 * undone.
 */
export class OutboxOrgDeletedAnnouncer implements OrgDeletedAnnouncer {
  async orgDeleted(orgId: string): Promise<void> {
    const payload: OrgDeletedEvent = { orgId };
    const event: Event = {
      eventType: EventType.OrgDeletedEvent,
      timestamp: Date.now(),
      payload,
    };
    await OutboxEvent.create([
      {
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
      },
    ]);
  }
}
