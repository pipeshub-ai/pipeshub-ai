/**
 * Archived Chats API
 *
 * Facade over ChatApi and AgentsApi — Assistant conversations delegate to ChatApi,
 * agent conversations delegate to AgentsApi.
 */
import { ChatApi } from '@/chat/api';
import { AgentsApi } from '@/app/(main)/agents/api';

export const ArchivedChatsApi = {
  // Assistant conversations
  fetchArchivedConversations: ChatApi.fetchArchivedConversations.bind(ChatApi),
  fetchConversation: ChatApi.fetchConversation.bind(ChatApi),
  restoreConversation: ChatApi.restoreConversation.bind(ChatApi),
  deleteConversation: ChatApi.deleteConversation.bind(ChatApi),

  // Agent conversations
  fetchAllAgentsArchivedConversations: AgentsApi.fetchAllAgentsArchivedConversations.bind(AgentsApi),
  fetchAgentArchivedConversations: AgentsApi.fetchAgentArchivedConversations.bind(AgentsApi),
  fetchAgentConversation: AgentsApi.fetchAgentConversation.bind(AgentsApi),
  restoreAgentConversation: AgentsApi.restoreAgentConversation.bind(AgentsApi),
  deleteAgentConversation: AgentsApi.deleteAgentConversation.bind(AgentsApi),
  getAgents: AgentsApi.getAgents.bind(AgentsApi),

  // Unified search across all archived conversations (assistant + agent)
  searchArchivedConversations: ChatApi.searchArchivedConversations.bind(ChatApi),
};
