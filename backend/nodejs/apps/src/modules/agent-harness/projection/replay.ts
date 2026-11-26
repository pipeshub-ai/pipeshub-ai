import { JournalEvent } from '../domain/journal';
import { LiveDelta } from './frames';
import { LostMessage, StreamCursor, StreamPosition } from './stream-id';

/**
 * The order a (re)subscription feeds what is already there — the run's
 * journal and the LiveBus's retained deltas — into a `RunProjector`
 * (REPORT 6.2.4 reconnect algorithm).
 *
 * Fresh: the journal in order; an attempt's retained deltas right before the
 * response that closes it, and the deltas of the attempt still streaming at
 * the end. Resuming: exactly what preceded the cursor (the journal before
 * it, then the deltas of the message streaming there), so the projector's
 * silent replay rebuilds the state the cursor was written in; then the rest
 * as for a fresh subscription.
 */

export type ReplayStep = { journal: JournalEvent[] } | { delta: LiveDelta };

export interface ReplayPlan {
  steps: ReplayStep[];
  /** Frames at or before this position were delivered already. */
  resumeAfter: StreamPosition | null;
  /**
   * The cursor's message streamed deltas that are no longer retained (the
   * buffer expired, L-2): the projector ends it before anything else, and
   * the journal event after the cursor is replayed whole (its frames may
   * differ without the deltas), restarting the message under a new id.
   */
  lost: { fence: number; attempt: number; n: number; endSent: boolean } | null;
  /** Messages the client had ended unfinished (from the cursor id). */
  lostMessages: LostMessage[];
}

export interface ReplayOptions {
  /**
   * End the cursor's open message even if its deltas are retained: the client
   * fell behind (L-4), and the restart must be what a reconnect would get.
   */
  forceLost?: boolean;
}

const keyOf = (fence: number, attempt: number): string => `${fence}.${attempt}`;

function responseKeys(events: readonly JournalEvent[]): Map<number, string> {
  const keys = new Map<number, string>();
  let attempt = 0;
  for (const event of events) {
    if (event.type === 'model.request') {
      attempt = Number(event.data?.['modelAttempt']) || attempt;
    } else if (event.type === 'model.response') {
      keys.set(event.seq, keyOf(event.fence ?? 0, attempt));
    }
  }
  return keys;
}

function interleave(
  events: readonly JournalEvent[],
  deltas: Map<string, LiveDelta[]>,
  steps: ReplayStep[],
  keys: Map<number, string>,
): void {
  let page: JournalEvent[] = [];
  for (const event of events) {
    const key = keys.get(event.seq);
    const pending = key !== undefined ? deltas.get(key) : undefined;
    if (pending !== undefined && pending.length > 0) {
      if (page.length > 0) steps.push({ journal: page });
      page = [];
      for (const delta of pending) steps.push({ delta });
      deltas.delete(key as string);
    }
    page.push(event);
  }
  if (page.length > 0) steps.push({ journal: page });
  for (const rest of deltas.values()) {
    for (const delta of rest) steps.push({ delta });
  }
}

export function planReplay(
  runId: string,
  events: readonly JournalEvent[],
  retained: readonly LiveDelta[],
  cursor: StreamCursor | null,
  options: ReplayOptions = {},
): ReplayPlan {
  const byKey = new Map<string, LiveDelta[]>();
  for (const delta of retained) {
    if (delta.runId !== runId) continue;
    const key = keyOf(delta.fence, delta.modelAttempt);
    const list = byKey.get(key) ?? [];
    list.push(delta);
    byKey.set(key, list);
  }
  for (const list of byKey.values()) list.sort((a, b) => a.n - b.n);
  const keys = responseKeys(events);
  const steps: ReplayStep[] = [];
  if (cursor === null) {
    interleave(events, byKey, steps, keys);
    return { steps, resumeAfter: null, lost: null, lostMessages: [] };
  }
  const through = cursor.position.seq;
  const before = events.filter((e) =>
    cursor.kind === 'd' ? e.seq <= through : e.seq < through,
  );
  const after = events.filter((e) =>
    cursor.kind === 'd' ? e.seq > through : e.seq >= through,
  );
  if (before.length > 0) steps.push({ journal: before });
  // Ended before the cursor: their deltas are not the client's any more.
  for (const message of cursor.lostMessages) {
    byKey.delete(keyOf(message.fence, message.attempt));
  }
  let lost: ReplayPlan['lost'] = null;
  const open = cursor.open;
  const alreadyEnded =
    open !== null &&
    cursor.lostMessages.some(
      (m) => m.fence === open.fence && m.attempt === open.attempt,
    );
  // A journal id names the message open before its event; if that event ended it, the client has the end.
  if (open !== null && !alreadyEnded) {
    const key = keyOf(open.fence, open.attempt);
    const list = byKey.get(key) ?? [];
    const upTo = list.filter((d) => d.n <= open.n);
    const contiguous =
      upTo.length === open.n && upTo.every((d, i) => d.n === i + 1);
    if (cursor.abandoned === true || options.forceLost === true) {
      lost = { ...open, endSent: cursor.abandoned === true };
      byKey.delete(key);
    } else if (contiguous) {
      for (const delta of upTo) steps.push({ delta });
      byKey.set(
        key,
        list.filter((d) => d.n > open.n),
      );
    } else {
      lost = { ...open, endSent: false };
      byKey.delete(key);
    }
  }
  interleave(after, byKey, steps, keys);
  const resumeAfter: StreamPosition =
    lost === null
      ? cursor.position
      : {
          seq: cursor.kind === 'd' ? through : through - 1,
          k: Number.MAX_SAFE_INTEGER,
          n: 0,
          m: 0,
        };
  return { steps, resumeAfter, lost, lostMessages: cursor.lostMessages };
}
