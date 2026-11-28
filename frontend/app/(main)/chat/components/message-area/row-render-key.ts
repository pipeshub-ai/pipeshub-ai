'use client';

import { createContext, useCallback, useContext, useRef } from 'react';
import type { MessagePair } from './message-pairs';

/**
 * The React key a message row is drawn under. It is the pair's own key, except for a row whose run has ended: the
 * saved reply then takes the key the streamed one had, so the row on screen (and its trail) is the same instance
 * across the hand-off instead of mounting afresh. A list body that draws its own rows keys them with this.
 */
export const RowRenderKeyContext = createContext<(pairKey: string) => string>((pairKey) => pairKey);

export function useRowRenderKey(): (pairKey: string) => string {
  return useContext(RowRenderKeyContext);
}

/** A row whose answer was running when last drawn: where it was, what it asked, and the row as its run ended. */
type LiveRow = { key: string; renderKey: string; question: string; ended?: MessagePair };

/**
 * Gives the saved row that replaces a running one the running row's key: same place in the list, same question, a key
 * not seen before. Nothing carries over to another conversation (`scope`), to an edited question or to a row that
 * moved. Only the React key changes; everything else keeps using the pair's own key. A row counts as running while it
 * streams here, or while `drawnLive` says it is drawn from another tab's run.
 *
 * `awaitingSavedRow`: a running row has ended but is still the streamed one; its saved row comes next.
 */
export function useLiveRowAliases(
  pairs: readonly MessagePair[],
  scope: string | null,
  drawnLive?: (pair: MessagePair) => boolean,
): { renderKeyOf: (pairKey: string) => string; atRunEnd: boolean; awaitingSavedRow: boolean } {
  const ref = useRef<{ scope: string | null; aliases: Map<string, string>; live: Map<number, LiveRow> }>({
    scope,
    aliases: new Map(),
    live: new Map(),
  });
  // Decided while drawing, so the commit that swaps the rows already draws the saved one under the old key.
  /* eslint-disable react-hooks/refs -- intentional: the aliases are bookkeeping for React keys, not drawn state */
  const state = ref.current;
  if (state.scope !== scope) {
    state.scope = scope;
    state.aliases = new Map();
    state.live = new Map();
  }
  // An empty moment during the hand-off keeps what was running: the saved rows arrive right after.
  let awaitingSavedRow = false;
  if (pairs.length > 0) {
    const nextLive = new Map<number, LiveRow>();
    pairs.forEach((pair, index) => {
      const was = state.live.get(index);
      if (was && was.key !== pair.key && was.question === pair.question && !state.aliases.has(pair.key)) {
        state.aliases.set(pair.key, was.renderKey);
      }
      if (pair.isStreaming || drawnLive?.(pair)) {
        nextLive.set(index, { key: pair.key, renderKey: state.aliases.get(pair.key) ?? pair.key, question: pair.question });
      } else if (was && was.key === pair.key && was.question === pair.question && (was.ended ?? pair) === pair) {
        // Ended but not yet swapped for the saved row: still the row the saved one should follow. A row saved under
        // the key it ran with (a regenerated answer, a card's reply) arrives as a new pair: then nothing is awaited.
        nextLive.set(index, { ...was, ended: pair });
      }
    });
    // A run that starts leaves nothing of an earlier one waiting: that saved row is not coming (an earlier row asking
    // the same question is drawn running for the commit before the thread holds the new one).
    const starts = [...nextLive].some(([index, row]) => {
      const before = state.live.get(index);
      return !row.ended && (!before || before.ended !== undefined || before.key !== row.key);
    });
    if (starts) for (const [index, row] of nextLive) if (row.ended) nextLive.delete(index);
    state.live = nextLive;
    awaitingSavedRow = [...nextLive.values()].some((row) => row.ended !== undefined);
  }
  const atRunEnd = state.live.size > 0;
  /* eslint-enable react-hooks/refs */
  const renderKeyOf = useCallback((pairKey: string) => ref.current.aliases.get(pairKey) ?? pairKey, []);
  return { renderKeyOf, atRunEnd, awaitingSavedRow };
}
