/**
 * Archived Chats API
 *
 * Thin facade over ChatApi — conversations are chat domain objects, so we
 * delegate to the shared chat API rather than duplicating logic here.
 */
import { ChatApi } from '@/chat/api';

export const ArchivedChatsApi = {
  fetchArchivedConversations: ChatApi.fetchArchivedConversations.bind(ChatApi),
  fetchConversation: ChatApi.fetchConversation.bind(ChatApi),
  restoreConversation: ChatApi.restoreConversation.bind(ChatApi),
  deleteConversation: ChatApi.deleteConversation.bind(ChatApi),
};
