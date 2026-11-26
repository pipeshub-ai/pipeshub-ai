/**
 * SSE ids of the durable stream (REPORT 6.2.4, L-1, L-13). An id names the
 * frame's position and what a replay needs to reach it again:
 *
 * - `j:<seq>.<k>/<fence>.<attempt>.<n>` — the k-th frame projected from
 *   journal event `seq`; the message streaming just before that event was
 *   `fence.attempt` with `n` deltas consumed (`0.0.0`: none).
 * - `d:<seq>.<k>.<n>.<m>/<fence>.<attempt>` — the m-th frame of LiveBus delta
 *   `n` of message `fence.attempt`, consumed after the k-th frame of event
 *   `seq`.
 *
 * - A `j:` or `d:` id ends with the messages ended unfinished so far, which
 *   restart under new ids: `~<fence>.<attempt>` while that restart has not
 *   been sent (a resume still marks it, `replacesMessageId`), `-<fence>.<attempt>`
 *   after. A resume restores them, so it projects what the client would have.
 * - `a:<id>` — the end of a message whose deltas were lost (L-2, L-4), sent
 *   right after the frame `<id>`. Resuming from it replays as resuming from
 *   `<id>` with that message lost, minus the end the client already has.
 *
 * The owner's stream also carries what its sub-agents ask (UX3), read from
 * their own journals. Those cards have no position here, so the id says what
 * the client holds of them instead (QA6-F12):
 *
 * - any id ends with `^<spawnSeq>.<requestSeq>` per sub-agent card the client
 *   holds open: the child spawned by journal event `spawnSeq` asked at
 *   `requestSeq` of its own journal. A resume says only what changed since.
 * - a frame that states such a card (asked, or decided) is the `n`-th since the
 *   positional frame before it and adds `+<n>` to that frame's id: it resumes
 *   from the same position.
 *
 * Positions order by `(seq, k, n, m)` with `n = m = 0` for journal frames, and
 * state frames by `+n` after their position: the order frames were written in.
 */

export interface StreamPosition {
  seq: number;
  k: number;
  n: number;
  m: number;
}

export interface OpenAt {
  fence: number;
  attempt: number;
  n: number;
}

export interface StreamCursor {
  kind: 'j' | 'd';
  position: StreamPosition;
  /** The message streaming at the cursor, which a replay must re-consume. */
  open: OpenAt | null;
  /** `a:` ids: the client already has the end of the lost `open` message. */
  abandoned?: true;
  /** Messages ended unfinished before the cursor; `pending`: restart not sent yet. */
  lostMessages: LostMessage[];
  /** Sub-agent cards the client holds open. */
  held: HeldCard[];
  /** State frames the client has since the cursor's position. */
  stated: number;
}

/** A sub-agent's open card: the parent's `child.spawned` seq and the child's own request seq. */
export interface HeldCard {
  spawnSeq: number;
  requestSeq: number;
}

export interface LostMessage {
  fence: number;
  attempt: number;
  pending: boolean;
}

const LOST = '((?:[~-]\\d+\\.\\d+)*)';
const JOURNAL_ID = new RegExp(
  `^j:(\\d+)\\.(\\d+)\\/(\\d+)\\.(\\d+)\\.(\\d+)${LOST}$`,
);
const DELTA_ID = new RegExp(
  `^d:(\\d+)\\.(\\d+)\\.(\\d+)\\.(\\d+)\\/(\\d+)\\.(\\d+)${LOST}$`,
);
const LOST_SEGMENT = /([~-])(\d+)\.(\d+)/g;
const CHILD_STATE = /((?:\^\d+\.\d+)*)(?:\+(\d+))?$/;
const HELD_SEGMENT = /\^(\d+)\.(\d+)/g;
export const MAX_STREAM_ID_LENGTH = 512;

export function compareStreamPositions(
  a: StreamPosition,
  b: StreamPosition,
): number {
  return a.seq - b.seq || a.k - b.k || a.n - b.n || a.m - b.m;
}

export function journalFrameId(
  seq: number,
  k: number,
  open: OpenAt | null,
): string {
  const at = open ?? { fence: 0, attempt: 0, n: 0 };
  return `j:${seq}.${k}/${at.fence}.${at.attempt}.${at.n}`;
}

export function deltaFrameId(
  position: StreamPosition,
  fence: number,
  attempt: number,
): string {
  return `d:${position.seq}.${position.k}.${position.n}.${position.m}/${fence}.${attempt}`;
}

/** The id suffix naming the messages ended unfinished so far (sorted, so ids are stable). */
export function lostSuffix(lost: readonly LostMessage[]): string {
  return [...lost]
    .sort((a, b) => a.fence - b.fence || a.attempt - b.attempt)
    .map((m) => `${m.pending ? '~' : '-'}${m.fence}.${m.attempt}`)
    .join('');
}

function lostOf(suffix: string | undefined): LostMessage[] {
  return [...(suffix ?? '').matchAll(LOST_SEGMENT)].map((m) => ({
    fence: Number(m[2]),
    attempt: Number(m[3]),
    pending: m[1] === '~',
  }));
}

export function abandonFrameId(sentAfter: string): string {
  return `a:${sentAfter}`;
}

const sameCard = (a: HeldCard, b: HeldCard): boolean =>
  a.spawnSeq === b.spawnSeq && a.requestSeq === b.requestSeq;

/** `id` without what it says of sub-agent cards: the positional id a replay is placed by. */
export function positionalId(id: string): string {
  return id.replace(CHILD_STATE, '');
}

/**
 * `id` (positional) naming the sub-agent cards the client holds, and, for a
 * frame that states one, its place after that position (sorted, so ids are stable).
 */
export function withChildState(
  id: string,
  held: readonly HeldCard[],
  stated = 0,
): string {
  const cards = held
    .filter((card, index) => held.findIndex((c) => sameCard(c, card)) === index)
    .sort((a, b) => a.spawnSeq - b.spawnSeq || a.requestSeq - b.requestSeq)
    .map((card) => `^${card.spawnSeq}.${card.requestSeq}`)
    .join('');
  return `${positionalId(id)}${cards}${stated > 0 ? `+${stated}` : ''}`;
}

/** `null` for an id this stream never wrote (the client then replays from the start). */
export function parseStreamId(id: string | undefined): StreamCursor | null {
  if (id === undefined || id.length === 0 || id.length > MAX_STREAM_ID_LENGTH) {
    return null;
  }
  const state = CHILD_STATE.exec(id);
  const held = [...(state?.[1] ?? '').matchAll(HELD_SEGMENT)].map((m) => ({
    spawnSeq: Number(m[1]),
    requestSeq: Number(m[2]),
  }));
  const stated = Number(state?.[2] ?? 0);
  if (
    !held.every(
      (card) =>
        Number.isSafeInteger(card.spawnSeq) &&
        Number.isSafeInteger(card.requestSeq),
    ) ||
    !Number.isSafeInteger(stated)
  ) {
    return null;
  }
  const cursor = parsePositional(positionalId(id));
  return cursor === null ? null : { ...cursor, held, stated };
}

function parsePositional(
  id: string,
): Omit<StreamCursor, 'held' | 'stated'> | null {
  if (id.startsWith('a:')) {
    const inner = parsePositional(id.slice(2));
    if (inner === null || inner.open === null || inner.abandoned) return null;
    return { ...inner, abandoned: true };
  }
  const journal = JOURNAL_ID.exec(id);
  if (journal !== null) {
    const [seq, k, fence, attempt, n] = journal.slice(1, 6).map(Number) as [
      number,
      number,
      number,
      number,
      number,
    ];
    return {
      kind: 'j',
      position: { seq, k, n: 0, m: 0 },
      open: n > 0 ? { fence, attempt, n } : null,
      lostMessages: lostOf(journal[6]),
    };
  }
  const delta = DELTA_ID.exec(id);
  if (delta !== null) {
    const [seq, k, n, m, fence, attempt] = delta.slice(1, 7).map(Number) as [
      number,
      number,
      number,
      number,
      number,
      number,
    ];
    return {
      kind: 'd',
      position: { seq, k, n, m },
      open: { fence, attempt, n },
      lostMessages: lostOf(delta[7]),
    };
  }
  return null;
}
