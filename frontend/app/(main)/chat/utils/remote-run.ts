import type { ChatSlot, ConversationMessage } from '../types';

/**
 * The id a cancel must name for a run that kept going after this tab's connection dropped. While a
 * question has no answer yet, its row is the last one and carries the run's id; once an answer
 * exists the run is over (or is a regenerate, whose id is on no row yet), so there is nothing to name.
 */
export function remoteRunIdOf(messages: readonly ConversationMessage[] | null | undefined): string | null {
  const last = messages?.[messages.length - 1];
  if (last?.messageType !== 'user_query') return null;
  return typeof last.runId === 'string' && last.runId.length > 0 ? last.runId : null;
}

/** A run that outlived a reload and this person may stop: the composer then shows Stop in place of Send. */
export function isRemoteRunStoppable(
  slot: Pick<ChatSlot, 'isStreaming' | 'remoteRunInProgress' | 'remoteRunId'> | null | undefined,
): boolean {
  return Boolean(slot && !slot.isStreaming && slot.remoteRunInProgress && slot.remoteRunId);
}
