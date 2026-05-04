import { apiClient } from '@/lib/api';

// ============================================================
// Base URL
// ============================================================

const PROMPTS_URL = '/api/v1/configurationManager/prompts/system';

// ============================================================
// Default fallback prompts
// ============================================================

export const DEFAULT_SYSTEM_PROMPT =
  'You are an assistant. Answer queries in a professional, enterprise-appropriate format.';

export const DEFAULT_WEB_SEARCH_PROMPT =
  'You are a helpful web research assistant.';

// ============================================================
// Types
// ============================================================

export interface SystemPrompts {
  customSystemPrompt: string;
  customSystemPromptWebSearch: string;
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
      };
    } catch {
      return { customSystemPrompt: '', customSystemPromptWebSearch: '' };
    }
  },

  /**
   * PUT /api/v1/configurationManager/prompts/system
   * Saves both the internal and web search custom system prompts.
   */
  async saveSystemPrompts(prompts: SystemPrompts): Promise<void> {
    await apiClient.put(PROMPTS_URL, prompts);
  },
};
