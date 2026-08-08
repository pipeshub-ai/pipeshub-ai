import { apiClient } from '@/lib/api';

// ============================================================
// Base URL
// ============================================================

const PROMPTS_URL = '/api/v1/configurationManager/prompts/system';

// ============================================================
// Types
// ============================================================

export interface SystemPrompts {
  customSystemPrompt: string;
  customSystemPromptWebSearch: string;
  customSystemPromptAgent: string;
}

// ============================================================
// Prompts API
// ============================================================

export const PromptsApi = {
  /**
   * GET /api/v1/configurationManager/prompts/system
   * Returns the currently stored custom system prompts.
   */
  async getSystemPrompts(): Promise<SystemPrompts> {
    try {
      const { data } = await apiClient.get<SystemPrompts>(PROMPTS_URL);
      return {
        customSystemPrompt: data?.customSystemPrompt ?? '',
        customSystemPromptWebSearch: data?.customSystemPromptWebSearch ?? '',
        customSystemPromptAgent: data?.customSystemPromptAgent ?? '',
      };
    } catch {
      return { customSystemPrompt: '', customSystemPromptWebSearch: '', customSystemPromptAgent: '' };
    }
  },

  /**
   * PUT /api/v1/configurationManager/prompts/system
   * Saves the internal search, web search, and agent mode custom system prompts.
   */
  async saveSystemPrompts(prompts: SystemPrompts): Promise<void> {
    await apiClient.put(PROMPTS_URL, prompts);
  },
};
