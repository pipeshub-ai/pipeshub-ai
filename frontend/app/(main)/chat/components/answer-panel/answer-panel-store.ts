'use client';

import { createContext, useEffect } from 'react';
import { create } from 'zustand';

/**
 * The right column beside a chat ("About this answer"): open or shut, and which answer it describes. In memory only:
 * a chat always opens with the column shut, and the reader opens it from the top bar or an answer's Sources.
 */
interface AnswerPanelState {
  /** Open or shut; unset (shut) again whenever the reader leaves the chat. */
  panelOpen?: boolean;
  /** Per chat slot, the answer the column describes (its row key): the one being read, or the one picked. */
  readingAnswer: Record<string, string>;
}

export const useAnswerPanelStore = create<AnswerPanelState>(() => ({ readingAnswer: {} }));

/**
 * Set by a chat's own message list: `column` from 1024px, where the column beside the list takes each answer's tabs
 * (open or shut); below that the answers keep their tabs, spread across the column. Null anywhere else.
 */
export const AnswerPanelScope = createContext<{ column: boolean } | null>(null);

/** The answer being read is the last whose top has passed this far down the readable box. */
export const READING_LINE = 0.4;
/** Within this of the foot, the reader is at the end and the column describes the newest answer. */
export const READING_END_PX = 120;

/**
 * The answer the column describes, from the answers' tops in list order: at the end of the list the newest, however
 * short; otherwise the last at or above `line`, else the first, when the reader is still above them all. Null with no
 * answers.
 */
export function readingKeyAt(answers: ReadonlyArray<{ key: string; top: number }>, line: number, atBottom = false): string | null {
  if (atBottom) return answers[answers.length - 1]?.key ?? null;
  let reading = answers[0]?.key ?? null;
  for (const answer of answers) if (answer.top <= line) reading = answer.key;
  return reading;
}

export function usePanelOpen(): boolean {
  return useAnswerPanelStore((s) => s.panelOpen ?? false);
}

export function setPanelOpen(open: boolean): void {
  useAnswerPanelStore.setState({ panelOpen: open });
}

/** Opening the column holds only while the reader stays in this chat: another chat, or coming back, starts it shut. */
export function useColumnOpensPerChat(convId: string | null | undefined): void {
  useEffect(() => () => useAnswerPanelStore.setState({ panelOpen: undefined }), [convId]);
}

export function useReadingAnswer(slotId: string | null | undefined): string | undefined {
  return useAnswerPanelStore((s) => (slotId ? s.readingAnswer[slotId] : undefined));
}

export function setReadingAnswer(slotId: string, key: string): void {
  if (useAnswerPanelStore.getState().readingAnswer[slotId] === key) return;
  useAnswerPanelStore.setState((s) => ({ readingAnswer: { ...s.readingAnswer, [slotId]: key } }));
}

const jumpListeners = new Set<{ slotId: string; listener: (key: string) => void }>();

/** A chat's scroller listens here for an answer picked in the column. Returns the unsubscribe. */
export function onJumpRequest(slotId: string, listener: (key: string) => void): () => void {
  const entry = { slotId, listener };
  jumpListeners.add(entry);
  return () => {
    jumpListeners.delete(entry);
  };
}

/** The chat scrolls to the row `key`. */
export function jumpTo(slotId: string, key: string): void {
  for (const entry of jumpListeners) if (entry.slotId === slotId) entry.listener(key);
}

/** The column's picker: the column describes `key` from now on, and the chat scrolls to it. */
export function pickAnswer(slotId: string, key: string): void {
  setReadingAnswer(slotId, key);
  jumpTo(slotId, key);
}

const sourcesListeners = new Set<{ slotId: string; listener: () => void }>();

/** The column listens here for an answer's Sources, to show that tab. Returns the unsubscribe. */
export function onSourcesRequest(slotId: string, listener: () => void): () => void {
  const entry = { slotId, listener };
  sourcesListeners.add(entry);
  return () => {
    sourcesListeners.delete(entry);
  };
}

/** An answer's Sources: the column opens on its Sources tab, for the answer being read; the chat stays where it is. */
export function openSources(slotId: string): void {
  setPanelOpen(true);
  for (const entry of sourcesListeners) if (entry.slotId === slotId) entry.listener();
}
