import { apiClient } from '@/lib/api';

const base = (conversationId: string) => `/api/v1/conversations/${conversationId}/compare`;

export const CompareApi = {
  /** Copies the history before the question into a hidden branch conversation. */
  async createBranch(conversationId: string, userMessageId: string): Promise<string> {
    const { data } = await apiClient.post<{ branchConversationId: string }>(
      `${base(conversationId)}/branch`,
      { userMessageId },
      { suppressErrorToast: true },
    );
    return data.branchConversationId;
  },

  async listBranches(
    conversationId: string,
  ): Promise<Array<{ branchConversationId: string; userMessageId: string }>> {
    const { data } = await apiClient.get<{
      branches: Array<{ branchConversationId: string; userMessageId: string }>;
    }>(base(conversationId), { suppressErrorToast: true });
    return data.branches ?? [];
  },

  /** `choice` is `primary` or a branch conversation id. */
  async choose(conversationId: string, choice: string): Promise<void> {
    await apiClient.post(`${base(conversationId)}/choose`, { choice }, { suppressErrorToast: true });
  },
};
