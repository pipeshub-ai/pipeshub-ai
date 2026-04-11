import { apiClient } from '@/lib/api';

// ============================================================
// Base URL
// ============================================================

const PROMPTS_URL = '/api/v1/configurationManager/prompts/system';

// ============================================================
// Default fallback prompt
// ============================================================

export const DEFAULT_SYSTEM_PROMPT =
  'You are an assistant. Answer queries in a professional, enterprise-appropriate format.';

// ============================================================
// Prompts API
// ============================================================

export const PromptsApi = {
  /**
   * GET /api/v1/configurationManager/prompts/system
   * Returns the currently stored custom system prompt.
   */
  async getSystemPrompt(): Promise<string> {
    try {
      const { data } = await apiClient.get<{ customSystemPrompt: string }>(PROMPTS_URL);
      return data?.customSystemPrompt ?? '';
    } catch {
      return '';
    }
  },

  /**
   * PUT /api/v1/configurationManager/prompts/system
   * Saves the custom system prompt.
   */
  async saveSystemPrompt(customSystemPrompt: string): Promise<void> {
    await apiClient.put(PROMPTS_URL, { customSystemPrompt });
  },
};
