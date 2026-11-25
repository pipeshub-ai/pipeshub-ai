'use client';

import { createContext, useContext } from 'react';

/**
 * Row scope for a single assistant turn — matches `messagePairs[].key` / thread
 * `msg.id`. Used to build a stable `messageRow::chunk` key for the list-level
 * popover so Radix `open` can stay true across markdown re-parses while
 * streaming.
 */
export const CitationMessageRowKeyContext = createContext<string | null>(null);

export function useCitationMessageRowKeyForInline(): string | null {
  return useContext(CitationMessageRowKeyContext);
}

/**
 * @see `useInlineCitationPopoverStore` — list-scoped open key (Zustand) for performance.
 *
 * The same display number (e.g. two `[1]` markers) can appear multiple times in one
 * answer; `chunkIndex` alone is not unique. Pass `occurrenceKey` (e.g. `${charIndex}-${chunkIndex}`
 * from the parser) so only one popover opens per click.
 */
export function buildInlineCitationInstanceKey(
  messageRowKey: string,
  chunkIndex: number,
  occurrenceKey?: string | null,
): string {
  if (occurrenceKey != null && String(occurrenceKey).length > 0) {
    return `${messageRowKey}::c${chunkIndex}@${String(occurrenceKey)}`;
  }
  return `${messageRowKey}::${chunkIndex}`;
}

const OUTSIDE_LIST = 'outside:';

/**
 * The row scope of citations drawn outside the message list (a job's reply in its panel, BGUI-12): the list's
 * row check below never closes their popover, since no row of the list holds them.
 */
export const outsideListRowKey = (id: string): string => `${OUTSIDE_LIST}${id}`;

/**
 * @returns `true` if the active key still refers to a visible message row, or to citations outside the list.
 */
export function isCitationPopoverKeyStillValid(
  activeKey: string | null,
  messageRowKeys: string[],
): boolean {
  if (!activeKey || activeKey.startsWith(OUTSIDE_LIST)) return true;
  return messageRowKeys.some(
    (rowKey) => activeKey.startsWith(`${rowKey}::`) && rowKey.length > 0,
  );
}
