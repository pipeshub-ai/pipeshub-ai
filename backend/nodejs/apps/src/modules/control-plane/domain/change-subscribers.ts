import type { ChangeEvent } from '../contracts/generated';

type Handler = (event: ChangeEvent) => void;

/**
 * This process's holders of control-plane state (the snapshot cache). An
 * event reaches them from the broker and, for a write this process made,
 * right after its commit; handlers are idempotent by revision, so both
 * arriving is one application. A handler that throws is reported and the
 * others still get the event.
 */
export class ChangeSubscribers {
  private readonly handlers = new Set<Handler>();

  constructor(
    private readonly failed: (
      event: ChangeEvent,
      error: unknown,
    ) => void = () => undefined,
  ) {}

  subscribe(handler: Handler): () => void {
    const own: Handler = (event) => {
      handler(event);
    };
    this.handlers.add(own);
    return () => {
      this.handlers.delete(own);
    };
  }

  deliver(event: ChangeEvent): void {
    for (const handler of [...this.handlers]) {
      try {
        handler(structuredClone(event));
      } catch (error) {
        this.failed(event, error);
      }
    }
  }
}
