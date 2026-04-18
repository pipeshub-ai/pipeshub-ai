// Re-export shared types from the chat domain.
export type { Conversation } from '@/chat/types';
export type { ConversationMessage } from '@/chat/types';

import type { Conversation } from '@/chat/types';

/** A group of archived agent conversations under one agent. */
export interface AgentArchivedGroup {
  agentKey: string;
  /** Display name fetched from the agents list (null until resolved). */
  agentName: string | null;
  conversations: Conversation[];
  totalCount: number;
  hasMore: boolean;
  /** Whether we are currently fetching more conversations for this group. */
  isLoadingMore: boolean;
}
