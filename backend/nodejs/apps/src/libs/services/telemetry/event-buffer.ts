import { isTelemetryEnabled } from './flag';
import { getRootId } from '../../context/request-context';
import { classifyRequestId } from './client';

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

/**
 * Stamp `client` (sdk, mcp or other) and, for the SDKs, `sdk` (the language)
 * from the request in flight, so usage dashboards can split product events by
 * channel. A caller's own `client` prop wins.
 */
function withClient(
  props: Record<string, unknown> | undefined,
): Record<string, unknown> {
  if (props && 'client' in props) return props;
  const info = classifyRequestId(getRootId());
  return {
    ...props,
    client: info.client,
    ...(info.client === 'sdk' && { sdk: info.sdk }),
  };
}

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
    props: withClient(props),
  });
}
