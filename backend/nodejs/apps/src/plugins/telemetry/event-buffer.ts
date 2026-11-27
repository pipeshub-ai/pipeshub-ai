import { isTelemetryEnabled } from './flag';

export interface BufferedEvent {
  event: string;
  timestamp: string;
  props?: Record<string, unknown>;
}

const MAX_EVENTS = 5000;

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

  /** Remove and return up to `max` events, oldest first. */
  take(max: number): BufferedEvent[] {
    return this.events.splice(0, Math.max(0, max));
  }

  /** Put events back at the front, to be sent before newer ones; the newest are dropped past the cap. */
  putBack(events: BufferedEvent[]): void {
    this.events = [...events, ...this.events].slice(0, MAX_EVENTS);
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
  eventBuffer.enqueue({ event, timestamp: new Date().toISOString(), props });
}
