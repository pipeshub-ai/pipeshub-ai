import type { AxiosRequestConfig } from 'axios';
import { AgentsApi } from '@/app/(main)/agents/api';
import type { ProcessedError } from '@/lib/api/api-error';
import { ChatApi } from './api';
import { loadHistoricalMessages } from './runtime';
import { useChatStore } from './store';
import type { ChatSlot } from './types';
import { pickModelInfoFromConversationBundle } from './utils/apply-conversation-model-info';

/** A conversation with its first page of messages, from the agent's route for an agent chat. */
export function fetchConversationDetail(convId: string, agentId: string | null, quiet?: Pick<AxiosRequestConfig, 'suppressErrorToast'>) {
  if (!quiet) return agentId ? AgentsApi.fetchAgentConversation(agentId, convId) : ChatApi.fetchConversation(convId);
  return agentId ? AgentsApi.fetchAgentConversation(agentId, convId, quiet) : ChatApi.fetchConversation(convId, undefined, undefined, quiet);
}

/** A conversation that no longer exists (deleted): its loader says so, with no error toast. */
export const isNotFound = (error: unknown) => (error as ProcessedError | null)?.statusCode === 404;

/** What a slot holds once a conversation's history is loaded into it. */
export function conversationSlotPatch(detail: Awaited<ReturnType<typeof fetchConversationDetail>>): Partial<ChatSlot> {
  const modelInfo = pickModelInfoFromConversationBundle({
    modelInfo: detail.conversation.modelInfo,
    messages: detail.messages,
  });
  const { messages, unansweredAskUserQuestion } = loadHistoricalMessages(detail.messages);
  return {
    messages,
    rawMessages: detail.messages,
    isInitialized: true,
    hasLoaded: true,
    remoteRunInProgress: detail.conversation.status === 'Inprogress',
    isOwner: detail.conversation.access?.isOwner ?? false,
    messagePagination: {
      currentPage: detail.pagination.page,
      hasOlderMessages: detail.pagination.hasNextPage,
      isLoadingOlder: false,
    },
    ...(modelInfo ? { conversationModelInfo: modelInfo } : {}),
    pendingAskUserQuestion: unansweredAskUserQuestion,
  };
}

/** The slot holding this conversation, loading its history into a new one when no slot has it
 * yet; `refetch` loads it again into a slot that has it (unless that slot is streaming). It never
 * changes which chat is shown. */
export async function loadConversationIntoSlot(
  convId: string,
  agentId: string | null,
  { refetch = false }: { refetch?: boolean } = {},
): Promise<string> {
  const store = useChatStore.getState();
  const found = store.getSlotByConvId(convId, { forAgentId: agentId });
  if (found?.slot.isInitialized && (!refetch || found.slot.isStreaming)) return found.slotId;
  const slotId = found?.slotId ?? store.createSlot(convId);
  if (!found && agentId) store.updateSlot(slotId, { threadAgentId: agentId });
  // A chat deleted since its call was listed: the caller says so in its own words.
  const detail = await fetchConversationDetail(convId, agentId, { suppressErrorToast: isNotFound });
  useChatStore.getState().updateSlot(slotId, conversationSlotPatch(detail));
  return slotId;
}
