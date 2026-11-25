import { RunReadyNotice, RunReadyNotifier } from '../ports/run-ready';

/** Records what was announced, in order, and tells any listener. */
export class InMemoryRunReadyNotifier implements RunReadyNotifier {
  readonly notices: RunReadyNotice[] = [];
  private readonly listeners = new Set<(notice: RunReadyNotice) => void>();

  notify(notices: readonly RunReadyNotice[]): Promise<void> {
    // The executor turns a listener's throw into a rejection, as an async method would.
    return new Promise<void>((resolve) => {
      this.announce(notices);
      resolve();
    });
  }

  private announce(notices: readonly RunReadyNotice[]): void {
    for (const notice of notices) {
      // Ids and the transition only, as Redis publishes them: a listener never sees what the caller passed beyond.
      const announced = {
        orgId: notice.orgId,
        runId: notice.runId,
        kind: notice.kind,
      };
      this.notices.push(announced);
      for (const listener of this.listeners) listener(announced);
    }
  }

  listen(listener: (notice: RunReadyNotice) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
}
