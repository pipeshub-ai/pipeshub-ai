'use client';

import { create } from 'zustand';
import { harnessHttpCode } from '@/lib/contracts/harness/codes';

/**
 * CARDFAIL: one way every card sends its answer (a question's answer, an approval's decision, a job's ask, an
 * engine sign-in's code). A failure is either transient (no answer came, or the server answered 5xx: PipesHub is
 * restarting or a service behind it is not up yet), which the card retries by itself with backoff for a bounded
 * time while the person's choice stays as they made it, then offers "Try again"; or a refusal with a reason (4xx),
 * which the card says and never retries. The send state lives here, keyed by the card, so the card, the floating
 * ask and a job's panel read the same state and a send is never started twice for one card.
 */

/** Why a transient failure happened, as the card words it. */
export type SendRetryReason = 'restarting' | 'offline' | 'server';

export type SendFailure = { retry: true; reason: SendRetryReason } | { retry: false; code: string | null };

/** What one attempt came to: sent (with the server's answer), or failed. */
export type SendAttempt<T> = { sent: T } | { failed: SendFailure };

/** The outcome of a whole send: sent, refused, given up after the retries (the card offers Try again), or dropped. */
export type SendOutcome<T> =
  | { sent: T }
  | { refused: string | null }
  | { stopped: SendRetryReason }
  /** The card was answered elsewhere or closed while this send waited: nothing more to do. */
  | { cancelled: true };

/** What the card shows while its answer is not sent. */
export type CardSendState =
  | { phase: 'retrying'; reason: SendRetryReason }
  | { phase: 'stopped'; reason: SendRetryReason; retry: () => void }
  /** A question's refusal (approvals keep theirs on the card, as `errorCode`). */
  | { phase: 'refused'; code: string | null };

/** A send that ended because the person stopped it or sent something else: nothing for the card to say. */
export const SEND_ABORTED = 'aborted';

const REVISION_CONFLICT = harnessHttpCode('REVISION_CONFLICT');

/**
 * Transient or permanent, from the HTTP status and the server's code. No status: the request got no answer. A 5xx
 * is the server's own trouble; a 502/503/504 is what a restart or a service that is not up yet answers. A run that
 * moved under the decision (an older server's 409) is read again by a retry, as DECIDE409 always did.
 */
export function sendFailureOf(status: number | undefined | null, code: string | null | undefined): SendFailure {
  if (status === undefined || status === null || status === 0) return { retry: true, reason: 'offline' };
  if (status === 502 || status === 503 || status === 504) return { retry: true, reason: 'restarting' };
  if (status >= 500) return { retry: true, reason: 'server' };
  if (code === REVISION_CONFLICT) return { retry: true, reason: 'server' };
  return { retry: false, code: code ?? null };
}

/**
 * Waits between attempts, about 75 s in all: long enough for an app restart (one observed took about 20 s to take
 * answers again), short enough that the person is not left wondering. Each wait is jittered so the cards of a batch do not
 * retry in step.
 */
export const CARD_RETRY_DELAYS_MS: readonly number[] = [1_000, 2_000, 4_000, 8_000, 15_000, 15_000, 15_000, 15_000];

interface CardSendStore {
  sends: Readonly<Record<string, CardSendState>>;
  put: (key: string, state: CardSendState | null) => void;
}

export const useCardSends = create<CardSendStore>((set) => ({
  sends: {},
  put: (key, state) =>
    set((s) => {
      if (state === null) {
        if (!(key in s.sends)) return s;
        const { [key]: _dropped, ...rest } = s.sends;
        return { sends: rest };
      }
      return { sends: { ...s.sends, [key]: state } };
    }),
}));

/** The send state of the first of `keys` that has one (a batch card is several calls). */
export function useCardSend(keys: string | readonly string[] | null | undefined): CardSendState | undefined {
  return useCardSends((s) => {
    if (!keys) return undefined;
    for (const key of typeof keys === 'string' ? [keys] : keys) {
      const state = s.sends[key];
      if (state) return state;
    }
    return undefined;
  });
}

export const cardSendState = (key: string): CardSendState | undefined => useCardSends.getState().sends[key];

/** The send key of a reply's question card: one card per assistant row. */
export const questionSendKey = (assistantMessageId: string): string => `question:${assistantMessageId}`;

/** The send each card is running now; a newer send or a cancel replaces it, and the older one stops at its next step. */
const running = new Map<string, symbol>();

/** Whether a send of this card is running (an attempt in flight or a wait between attempts). */
export const cardSendRunning = (key: string): boolean => running.has(key);

/** The card was answered elsewhere or closed: its send stops and it shows nothing more. */
export function cancelCardSend(key: string): void {
  running.delete(key);
  useCardSends.getState().put(key, null);
}

/** Clears what a card says about an earlier send (a refusal), when the person answers it again. */
export function clearCardSend(key: string): void {
  if (!running.has(key)) useCardSends.getState().put(key, null);
}

const wait = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

export interface SendCardOptions {
  /** Starts the whole send again, from the card's Try again. */
  again: () => void;
  /**
   * False for a send the server cannot recognise as the same when it arrives twice (an engine sign-in's code is
   * single-use at Anthropic): a transient failure then offers Try again at once and nothing is resent unasked.
   */
  autoRetry?: boolean;
  /** A question's refusal is said by the card's notice; an approval's by its own refusal line. */
  keepRefusal?: boolean;
  delaysMs?: readonly number[];
}

/**
 * Runs one card's send: the attempt, then on a transient failure the waits and attempts again, publishing what the
 * card shows. Every attempt must be idempotent on the server (the same decision key, the same turn run id), so a
 * retry after an answer that was lost replays the first result instead of answering twice.
 */
export async function sendCard<T>(
  key: string,
  attempt: () => Promise<SendAttempt<T>>,
  options: SendCardOptions,
): Promise<SendOutcome<T>> {
  const token = Symbol(key);
  running.set(key, token);
  const { put } = useCardSends.getState();
  const current = () => running.get(key) === token;
  const delays = options.autoRetry === false ? [] : (options.delaysMs ?? CARD_RETRY_DELAYS_MS);
  for (let tries = 0; ; tries += 1) {
    const out = await attempt();
    if (!current()) return { cancelled: true };
    if ('sent' in out) {
      running.delete(key);
      put(key, null);
      return out;
    }
    const { failed } = out;
    if (failed.retry === false) {
      running.delete(key);
      put(key, options.keepRefusal ? { phase: 'refused', code: failed.code } : null);
      return { refused: failed.code };
    }
    if (tries >= delays.length) {
      running.delete(key);
      put(key, { phase: 'stopped', reason: failed.reason, retry: options.again });
      return { stopped: failed.reason };
    }
    put(key, { phase: 'retrying', reason: failed.reason });
    await wait(delays[tries] * (1 + Math.random() * 0.25));
    if (!current()) return { cancelled: true };
  }
}
