import { isTelemetryEnabled } from './flag';

export interface BufferedEvent {
  event: string;
  timestamp: string;
  props?: Record<string, unknown>;
}

const MAX_EVENTS = 5000;

/** Event props that would say whose event it is; dropped before the event can leave the install. */
export const IDENTIFYING_PROP = /email|domain|full_?name|org_?name/i;

class EventBuffer {
  private events: BufferedEvent[] = [];

  enqueue(event: BufferedEvent): void {
    if (this.events.length >= MAX_EVENTS) return;
    this.events.push(event);
  }

  drain(): BufferedEvent[] {
    const drained = this.events;
    this.events = [];
    return drained;
  }

  size(): number {
    return this.events.length;
  }
}

export const eventBuffer = new EventBuffer();

export function recordEvent(
  event: string,
  props?: Record<string, unknown>,
): void {
  if (!isTelemetryEnabled()) {
    return;
  }
  eventBuffer.enqueue({
    event,
    timestamp: new Date().toISOString(),
    props:
      props &&
      Object.fromEntries(
        Object.entries(props).filter(([key]) => !IDENTIFYING_PROP.test(key)),
      ),
  });
}
