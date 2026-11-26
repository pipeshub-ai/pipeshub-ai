import { LiveDelta } from '../projection/frames';

/** One frame of a per-run live channel (`contracts/harness/vectors/livebus-frames.json` encoding). */
export interface LiveFrame<K extends string = string> {
  runId: string;
  fence: number;
  modelAttempt: number;
  n: number;
  kind: K;
  data: Record<string, unknown>;
}

/**
 * A per-run live channel: the chat's token deltas, or the activity panel's
 * command output (UX5). Each has its own capped buffer, so output never
 * evicts the deltas a reconnecting chat needs, and a reader skips any kind
 * its channel does not carry.
 */
export interface LiveChannel<F extends LiveFrame> {
  /** `harness:<name>:<runId>` and `harness:<name>-notify:<runId>` on Redis. */
  readonly name: string;
  /** The kinds of `F`: decoding checks a frame's kind against these before typing it `F`. */
  readonly kinds: ReadonlySet<F['kind']>;
}

export interface LiveBusSubscription {
  close(): void;
}

export interface RetainedDeltas<F extends LiveFrame = LiveDelta> {
  deltas: F[];
  /** Where to follow from: frames after these. */
  cursor: string;
}

/**
 * The reading side of the LiveBus (REPORT 6.2.4, D3): the per-run, capped,
 * expiring buffer the worker publishes token deltas (and, on its own
 * channel, command output) to. Best effort: a failure here degrades a stream
 * to journal-only, never fails it (L-5). Adapters: Redis Streams
 * (`adapters/redis/redis-live-bus.ts`) and in-memory.
 */
export interface LiveBus<F extends LiveFrame = LiveDelta> {
  read(runId: string): Promise<RetainedDeltas<F>>;
  /** Frames published after `cursor`, in order, until closed. */
  follow(
    runId: string,
    cursor: string,
    onDeltas: (deltas: F[]) => void,
    onError: (error: unknown) => void,
  ): LiveBusSubscription;
}
