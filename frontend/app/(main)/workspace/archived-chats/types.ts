// Re-export shared types from the chat domain.
export type { Conversation } from '@/chat/types';
export type { ConversationMessage } from '@/chat/types';

import type { Conversation } from '@/chat/types';

export interface AgentGroupPagination {
  page: number;
  limit: number;
  totalCount: number;
  totalPages: number;
  hasNextPage: boolean;
  hasPrevPage: boolean;
}

/** A group of archived agent conversations under one agent. */
export interface AgentArchivedGroup {
  agentKey: string;
  /** Display name fetched from the agents list (null until resolved). */
  agentName: string | null;
  conversations: Conversation[];
  pagination: AgentGroupPagination;
  /** Whether we are currently fetching more conversations for this group. */
  isLoadingMore: boolean;
}
