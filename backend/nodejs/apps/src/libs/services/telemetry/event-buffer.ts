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

  size(): number {
    return this.events.length;
  }
}

export const eventBuffer = new EventBuffer();

export function recordEvent(
  event: string,
  props?: Record<string, unknown>,
): void {
  eventBuffer.enqueue({ event, timestamp: new Date().toISOString(), props });
}
