/**
 * Work that covers everything that happened before it started, run one at a
 * time per key: a request made while a run is under way cannot count on that
 * run (it may have read before the requester's write), so it gets the next
 * one, and everyone who asks before that one starts shares it.
 */
export class CoalescedWork {
  private readonly running = new Map<string, Promise<void>>();
  private readonly next = new Map<string, Promise<void>>();

  constructor(private readonly work: (key: string) => Promise<void>) {}

  /** Settles when a run that started after this call has ended, as that run did. */
  request(key: string): Promise<void> {
    const queued = this.next.get(key);
    if (queued !== undefined) return queued;
    const start = async (): Promise<void> => {
      this.next.delete(key);
      this.running.set(key, turn);
      try {
        await this.work(key);
      } finally {
        this.running.delete(key);
      }
    };
    const turn: Promise<void> = (
      this.running.get(key) ?? Promise.resolve()
    ).then(start, start);
    this.next.set(key, turn);
    return turn;
  }

  /** Settles when no run is under way or queued, whatever their outcome. */
  async idle(): Promise<void> {
    while (this.running.size + this.next.size > 0) {
      await Promise.allSettled([
        ...this.running.values(),
        ...this.next.values(),
      ]);
    }
  }
}
