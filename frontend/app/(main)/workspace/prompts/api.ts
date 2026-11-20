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
  /** Whether the Agent prompt is also sent to MCP clients of the PipesHub MCP server. */
  useInMcp: boolean;
}

export interface StoredSystemPrompts extends SystemPrompts {
  /** The longest Agent prompt, trimmed, that can be saved while `useInMcp` is on. */
  mcpPromptMaxChars: number;
}

/** What the server allowed before it said so itself. */
const DEFAULT_MCP_PROMPT_MAX_CHARS = 500;

// ============================================================
// Prompts API
// ============================================================

export const PromptsApi = {
  /**
   * GET /api/v1/configurationManager/prompts/system
   * Returns the currently stored custom system prompts.
   */
  async getSystemPrompts(): Promise<StoredSystemPrompts> {
    try {
      const { data } = await apiClient.get<Partial<StoredSystemPrompts>>(PROMPTS_URL);
      return {
        customSystemPrompt: data?.customSystemPrompt ?? '',
        customSystemPromptWebSearch: data?.customSystemPromptWebSearch ?? '',
        customSystemPromptAgent: data?.customSystemPromptAgent ?? '',
        useInMcp: data?.useInMcp === true,
        mcpPromptMaxChars: data?.mcpPromptMaxChars ?? DEFAULT_MCP_PROMPT_MAX_CHARS,
      };
    } catch {
      return {
        customSystemPrompt: '',
        customSystemPromptWebSearch: '',
        customSystemPromptAgent: '',
        useInMcp: false,
        mcpPromptMaxChars: DEFAULT_MCP_PROMPT_MAX_CHARS,
      };
    }
  },

  /**
   * PUT /api/v1/configurationManager/prompts/system
   * Saves the internal search, web search, and agent mode custom system prompts,
   * and whether the agent prompt is also sent to MCP clients.
   */
  async saveSystemPrompts(prompts: SystemPrompts): Promise<void> {
    await apiClient.put(PROMPTS_URL, prompts);
  },
};
