import { selectAgentHarnessEnabled, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { activityRun } from './activity';
import { useChatStore } from './store';
import type { QueryMode, StreamChatRequest } from './types';

type RunSlot = Parameters<typeof activityRun>[0];

/**
 * Which harness a conversation is on (ASSIST1): none yet, the durable harness
 * (the server says the session is pinned to it, or it has a durable run), or the classic loop.
 */
export type ConversationHarness = 'new' | 'durable' | 'legacy';

export function conversationHarness(slot: RunSlot): ConversationHarness {
  if (!slot?.convId) return 'new';
  // The session's own harness: an error answer saved before its run id was (LC2FIX) must not make it legacy.
  const followed = Boolean(slot.durableRun ?? slot.lastDurableRun);
  return slot.durable === true || followed || activityRun(slot) !== null ? 'durable' : 'legacy';
}

export interface ChatHarnessChoice {
  queryMode: QueryMode;
  /** The turn is a saved agent's. */
  scopedAgent: boolean;
  /** The org's agent harness is on: Agent mode comes with it (CTL-5) and the switch is shown (CTL-7). */
  offered: boolean;
  conversation: ConversationHarness;
  /** The person's "New agent harness" switch (CTL-7). */
  chosen: boolean;
  /** The agent has an engine profile, so it runs only on the harness. */
  needsHarness: boolean;
}

/**
 * What the "New agent harness" switch shows (CTL-7): an engine agent is always
 * on the harness, an existing conversation keeps its executor, and a new one
 * follows the person's choice.
 */
export function harnessSwitchOn(choice: ChatHarnessChoice): boolean {
  if (choice.needsHarness || choice.conversation === 'durable') return true;
  if (choice.conversation === 'legacy') return false;
  return choice.chosen;
}

/**
 * Why the switch cannot be changed here: an engine agent, a conversation that keeps its executor, or new runs
 * stopped for the org (the kill switch, an operator stop or infrastructure not ready) while its harness is on.
 */
export type HarnessSwitchLock = 'agent' | 'conversation' | 'paused';

export function harnessSwitchLock(choice: ChatHarnessChoice): HarnessSwitchLock | undefined {
  if (choice.needsHarness) return 'agent';
  return choice.conversation === 'new' ? undefined : 'conversation';
}

/**
 * The turn's `harness` field. With the switch off it asks for the classic
 * path; otherwise the default chat's Agent mode asks for the harness as in
 * ASSIST1 (a new conversation while the org's harness is on, a durable one
 * always). The server decides either way.
 */
export function chatHarnessField(choice: ChatHarnessChoice): Pick<StreamChatRequest, 'harness'> {
  if (choice.offered && !harnessSwitchOn(choice)) return { harness: 'legacy' };
  if (choice.queryMode !== 'agent' || choice.scopedAgent) return {};
  if (choice.conversation === 'durable') return { harness: 'durable' };
  if (choice.conversation === 'new' && choice.offered) return { harness: 'durable' };
  return {};
}

/** A turn's choice as the stores hold it now: the org's harness, the person's switch, the open agent. */
export function turnHarnessChoice(slot: RunSlot, queryMode: QueryMode, scopedAgent: boolean): ChatHarnessChoice {
  const chat = useChatStore.getState();
  return {
    queryMode,
    scopedAgent,
    offered: selectAgentHarnessEnabled(useFeatureFlagsStore.getState()),
    conversation: conversationHarness(slot),
    chosen: chat.settings.newHarness,
    needsHarness: scopedAgent && chat.agentNeedsHarness,
  };
}
