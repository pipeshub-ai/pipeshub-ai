// Re-export shared types from the chat domain.
// Archived chats are still conversations — no new domain object needed.
export type { Conversation } from '@/chat/types';
export type { ConversationMessage } from '@/chat/types';
