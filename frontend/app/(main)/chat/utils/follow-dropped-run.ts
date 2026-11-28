import { AgentsApi } from '@/app/(main)/agents/api';
import { isGoneError } from '@/lib/api/api-error';
import { ChatApi } from '../api';
import { useChatStore } from '../store';
import type { ConversationMessage } from '../types';

/*
 * A run whose stream dropped: the server goes on with it, so nothing says it failed. This asks the server what became
 * of it until it has the answer, and only calls it nothing once the server has had neither run nor answer for a while.
 */

/** The waits between checks: short at first, then as often as a reloaded page asks. */
const CHECK_AFTER_MS = [1_000, 2_000, 3_000];
/** A check with no answer by then says nothing. */
const CHECK_TIMEOUT_MS = 10_000;
/** A server that has had neither the run nor its answer for this long, since the drop or the last sight of the run, has none. */
export const GIVE_UP_AFTER_MS = 20_000;
/** A little over the server's detached-run cap, after which the run is abandoned. */
const MAX_FOLLOW_MS = 35 * 60 * 1000;
const IN_PROGRESS = 'Inprogress';

/** The conversation as its detail route gives it: its newest rows, and what is known of it. */
export type ConversationRead =
  | Awaited<ReturnType<typeof ChatApi.fetchConversation>>
  | Awaited<ReturnType<typeof AgentsApi.fetchAgentConversation>>;

/** What the server has of the run: its answer (or failure), or the run itself still going; `null` is neither. */
export type RunOnServer = 'ended' | 'running' | null;

export interface DroppedRun {
  slotId: string;
  convId: string;
  agentId: string | null;
  /** Aborts when the slot is taken from this run (a Stop that settled it, a new send). */
  signal: AbortSignal;
  /** The slot still shows this run. */
  isCurrent(): boolean;
  /** Reads the run off the conversation's stored rows; `inProgress`: a run is going in the conversation. */
  read(rows: readonly ConversationMessage[], inProgress: boolean): RunOnServer;
  /** A check was made; `null` also when the server could not be asked. */
  onChecked(state: RunOnServer): void;
  /** The run is over: the rows that hold its end, and the read they came with, if they did. */
  onEnded(rows: ConversationMessage[], read?: ConversationRead): void | Promise<void>;
  /** The server has neither the run nor its answer. */
  onNothing(): void;
}

/**
 * What became of a send, by the row it stored (`isOwnRow`) and the answer after it. One run goes at a time in a
 * conversation, so a question after it with no answer between means its own run is gone.
 */
export function sendOnServer(
  rows: readonly ConversationMessage[],
  isOwnRow: (row: ConversationMessage) => boolean,
  inProgress: boolean,
): RunOnServer {
  const at = ownRowIndex(rows, isOwnRow);
  if (at < 0) return null;
  // A message that asked nobody gets no answer.
  if (rows[at].messageType !== 'user_query') return 'ended';
  const next = rows
    .slice(at + 1)
    .find((m) => m.messageType === 'bot_response' || m.messageType === 'error' || m.messageType === 'user_query');
  if (next) return next.messageType === 'user_query' ? null : 'ended';
  return inProgress ? 'running' : null;
}

/** The newest row `isOwnRow` names, -1 when there is none. */
export function ownRowIndex(
  rows: readonly ConversationMessage[],
  isOwnRow: (row: ConversationMessage) => boolean,
): number {
  for (let i = rows.length - 1; i >= 0; i -= 1) if (isOwnRow(rows[i])) return i;
  return -1;
}

/**
 * The rows held with a turn as it was read among them, in the order the read has it: the answer may have come without
 * the question before it. Rows that came after the turn stay after it.
 */
export function withReadRows(
  held: readonly ConversationMessage[],
  read: readonly ConversationMessage[],
): ConversationMessage[] {
  const ids = new Set(read.map((m) => m._id));
  let last = held.length - 1;
  while (last >= 0 && !ids.has(held[last]._id)) last -= 1;
  const older = last < 0 ? held : held.slice(0, last).filter((m) => !ids.has(m._id));
  return [...older, ...read, ...(last < 0 ? [] : held.slice(last + 1))];
}

/**
 * What became of a regeneration of `messageId`: the run writes its answer over that row, under its own id where the
 * server keeps one. `before` is the row as it was; `started`: the server took the request.
 */
export function regenerationOnServer(
  rows: readonly ConversationMessage[],
  run: { messageId: string; runId: string | null; before: ConversationMessage | undefined; started: boolean },
  inProgress: boolean,
): RunOnServer {
  const row = rows.find((m) => m._id === run.messageId);
  if (row?.runId !== undefined && row.runId === run.runId) return 'ended';
  if (inProgress) return 'running';
  if (!row) return null;
  return (run.before ? row.updatedAt !== run.before.updatedAt : run.started) ? 'ended' : null;
}

/** The conversation as a reload reads it; `gone` when it is no longer the reader's, `null` when the server can't be asked. */
export async function readConversation({ convId, agentId }: Pick<DroppedRun, 'convId' | 'agentId'>): Promise<ConversationRead | 'gone' | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), CHECK_TIMEOUT_MS);
  });
  try {
    return await Promise.race([
      agentId
        ? AgentsApi.fetchAgentConversation(agentId, convId, { quiet: true })
        : ChatApi.fetchConversation(convId, undefined, undefined, { quiet: true }),
      late,
    ]);
  } catch (error) {
    return isGoneError(error) ? 'gone' : null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Asks the server for the run until it ends there, with a short backoff, and at once when the window gets focus back or
 * the browser is online again. Rows that reach the slot another way and hold the answer end it too, in the same tick,
 * so the streamed answer and the stored one are never shown together. A server that can't be reached has not said
 * anything. Resolves when the run is settled, or no longer this slot's.
 */
export function followDroppedRun(run: DroppedRun): Promise<void> {
  if (run.signal.aborted || !run.isCurrent()) return Promise.resolve();
  const droppedAt = Date.now();
  let seenAt = droppedAt;
  return new Promise<void>((resolve) => {
    let over = false;
    let asked = 0;
    let checks = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const end = (settle?: () => void | Promise<void>): void => {
      if (over) return;
      over = true;
      clearTimeout(timer);
      unsubscribe();
      window.removeEventListener('online', wake);
      window.removeEventListener('focus', wake);
      document.removeEventListener('visibilitychange', wake);
      run.signal.removeEventListener('abort', superseded);
      // Runs in this tick up to its first await, so what settles the slot does.
      const settled = async (): Promise<void> => {
        try {
          await settle?.();
        } catch (error) {
          // As a stream whose last frame can't be read: the turn fails rather than run forever.
          console.error('[streaming] Failed to settle a dropped run', run.slotId, error);
          run.onNothing();
        }
      };
      void settled().finally(resolve);
    };

    const superseded = (): void => end();

    const checkAfter = (ms: number): void => {
      clearTimeout(timer);
      timer = setTimeout(() => void check(), ms);
    };

    const check = async (): Promise<void> => {
      if (over) return;
      clearTimeout(timer);
      // A check asked for while one is out takes its place: the older one may hang on the connection that went.
      const mine = (asked += 1);
      const answer = await readConversation(run);
      if (over || mine !== asked) return;
      if (!run.isCurrent()) return end();
      const read = answer === 'gone' ? null : answer;
      const state = read ? run.read(read.messages, read.conversation.status === IN_PROGRESS) : null;
      if (read && state === 'ended') return end(() => run.onEnded(read.messages, read));
      const now = Date.now();
      if (state === 'running') seenAt = now;
      const nothing = answer === 'gone' || (read !== null && state === null && now - seenAt >= GIVE_UP_AFTER_MS);
      if (nothing || now - droppedAt >= MAX_FOLLOW_MS) return end(() => run.onNothing());
      run.onChecked(state);
      const stopping = useChatStore.getState().slots[run.slotId]?.stopping;
      const wait = CHECK_AFTER_MS[stopping ? 0 : Math.min(checks, CHECK_AFTER_MS.length - 1)];
      checks += 1;
      // One check lands just as the wait for an answer is over.
      const left = seenAt + GIVE_UP_AFTER_MS - now;
      checkAfter(left > 0 ? Math.min(wait, left) : wait);
    };

    const wake = (): void => {
      if (document.visibilityState !== 'hidden') void check();
    };

    /** The slot's own rows hold the run's end. */
    const endsWith = (rows: ConversationMessage[] | null | undefined): boolean => {
      if (!rows || run.read(rows, true) !== 'ended') return false;
      end(() => run.onEnded(rows));
      return true;
    };

    const unsubscribe = useChatStore.subscribe((state, prev) => {
      const slot = state.slots[run.slotId];
      const before = prev.slots[run.slotId];
      if (over || slot === before) return;
      if (!run.isCurrent()) return end();
      if (slot.rawMessages !== before?.rawMessages) {
        // Something was stored in the chat: the run's end, or a part of it that only a read can place.
        if (!endsWith(slot.rawMessages)) void check();
        return;
      }
      // Stop was pressed: its end is asked for sooner than the next check would.
      if (slot.stopping && !before?.stopping) checkAfter(CHECK_AFTER_MS[0]);
    });

    window.addEventListener('online', wake);
    window.addEventListener('focus', wake);
    document.addEventListener('visibilitychange', wake);
    run.signal.addEventListener('abort', superseded, { once: true });
    if (!endsWith(useChatStore.getState().slots[run.slotId]?.rawMessages)) void check();
  });
}
