import { bindOrgContext } from '../../../libs/context/orgContext';
import { carries, CHAT_LIVE_CHANNEL, LiveDelta } from '../projection/frames';
import {
  LiveBus,
  LiveBusSubscription,
  LiveChannel,
  LiveFrame,
  RetainedDeltas,
} from '../ports/live-bus';
import { LiveBusEraser } from '../ports/session-erasure';

interface RunBuffer<F> {
  deltas: F[];
  /** Position of `deltas[0]` among everything the run published. */
  first: number;
}

interface Follower<F> {
  /** Position of the next delta to deliver. */
  next: number;
  deliver: (deltas: F[]) => void;
}

/**
 * In-process LiveBus: tests and single-process deployments. A cursor is a
 * position in the run's publish order, so following from `read`'s cursor
 * also delivers what was published between the read and the follow, as the
 * Redis stream's entry ids do.
 */
export class InMemoryLiveBus<F extends LiveFrame = LiveDelta>
  implements LiveBus<F>, LiveBusEraser
{
  private readonly runs = new Map<string, RunBuffer<F>>();
  /** Survives a lapsed buffer, so cursors taken before it stay meaningful. */
  private readonly published = new Map<string, number>();
  private readonly followers = new Map<string, Set<Follower<F>>>();
  private readonly channel: LiveChannel<F>;
  failing: Error | null = null;

  /** Without `channel`: the chat's token deltas, which is what `F` defaults to. */
  constructor(
    private readonly maxlen = 8_000,
    channel?: LiveChannel<F>,
  ) {
    this.channel =
      channel ??
      (CHAT_LIVE_CHANNEL as LiveChannel<LiveFrame> as LiveChannel<F>);
  }

  /** A kind the channel does not carry is never stored, as the Redis reader never decodes it. */
  publish(delta: F): void {
    if (!carries(this.channel, delta.kind)) return;
    const position = this.published.get(delta.runId) ?? 0;
    this.published.set(delta.runId, position + 1);
    const buffer = this.runs.get(delta.runId) ?? {
      deltas: [],
      first: position,
    };
    buffer.deltas.push(delta);
    const over = buffer.deltas.length - this.maxlen;
    if (over > 0) {
      buffer.deltas.splice(0, over);
      buffer.first += over;
    }
    this.runs.set(delta.runId, buffer);
    for (const follower of this.followers.get(delta.runId) ?? []) {
      this.drain(delta.runId, follower);
    }
  }

  erase(runIds: readonly string[]): Promise<number> {
    let erased = 0;
    for (const runId of runIds) {
      if (this.runs.delete(runId)) erased += 1;
      this.published.delete(runId);
    }
    return Promise.resolve(erased);
  }

  /** The buffer expired (TTL, trimming). */
  expire(runId: string): void {
    this.runs.delete(runId);
  }

  read(runId: string): Promise<RetainedDeltas<F>> {
    if (this.failing !== null) return Promise.reject(this.failing);
    const buffer = this.runs.get(runId);
    return Promise.resolve({
      deltas: [...(buffer?.deltas ?? [])],
      cursor: String(this.published.get(runId) ?? 0),
    });
  }

  follow(
    runId: string,
    cursor: string,
    onDeltas: (deltas: F[]) => void,
    onError: (error: unknown) => void,
  ): LiveBusSubscription {
    // Told in the context it was registered in (the viewer's request), never in the poller's or publisher's.
    const told = bindOrgContext(onDeltas);
    const failed = bindOrgContext(onError);
    if (this.failing !== null) {
      const failure = this.failing;
      queueMicrotask(() => {
        failed(failure);
      });
      return { close: () => undefined };
    }
    const from = Number(cursor);
    const follower: Follower<F> = {
      next: Number.isInteger(from) && from > 0 ? from : 0,
      deliver: told,
    };
    const set = this.followers.get(runId) ?? new Set();
    set.add(follower);
    this.followers.set(runId, set);
    // The backlog arrives after follow returns, as a stream reader's first read does.
    queueMicrotask(() => {
      if (set.has(follower)) this.drain(runId, follower);
    });
    return { close: () => set.delete(follower) };
  }

  private drain(runId: string, follower: Follower<F>): void {
    const buffer = this.runs.get(runId);
    if (buffer === undefined) return;
    const start = Math.max(follower.next, buffer.first);
    const end = buffer.first + buffer.deltas.length;
    if (start >= end) return;
    follower.next = end;
    follower.deliver(
      buffer.deltas.slice(start - buffer.first, end - buffer.first),
    );
  }
}
