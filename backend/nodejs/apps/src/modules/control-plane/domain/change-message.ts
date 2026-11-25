import type { ChangeEvent } from '../contracts/generated';

/** The broker topic change events travel on (Kafka topic, Redis stream). */
export const CONTROL_PLANE_EVENTS_TOPIC = 'control-plane-events';

export interface ChangeMessage {
  readonly topic: string;
  /** The org: one partition, so one org's events keep their order. */
  readonly key: string;
  /** What the shared outbox delivers in the order written. */
  readonly orderingKey: string;
  /** The envelope as JSON; the broker clients encode this string once more. */
  readonly value: string;
}

/** The `eventType` of the envelope every PipesHub topic uses (`{eventType, payload}`). */
export const CHANGE_EVENT_TYPE = 'cp.changed';

/**
 * A change event as every publisher puts it on the broker (03-interfaces
 * §5.4): the outbox row and a direct publish carry the same bytes, the
 * envelope the Python consumers read, whose payload is the event itself and
 * nothing more (NTF-13).
 */
export function changeEventMessage(event: ChangeEvent): ChangeMessage {
  return {
    topic: CONTROL_PLANE_EVENTS_TOPIC,
    key: event.orgId,
    orderingKey: `cp:${event.orgId}`,
    value: JSON.stringify({ eventType: CHANGE_EVENT_TYPE, payload: event }),
  };
}
