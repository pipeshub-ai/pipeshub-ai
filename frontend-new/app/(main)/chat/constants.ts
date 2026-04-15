import type { QueryModeConfig } from './types';

// ── LLM Model Metadata Maps ──────────────────────────────────────────

/**
 * Maps provider API key → human-readable display name.
 * Add new providers here as they become available.
 */
export const PROVIDER_FRIENDLY_NAMES: Record<string, string> = {
  openAI: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  mistral: 'Mistral',
  meta: 'Meta',
  cohere: 'Cohere',
  groq: 'Groq',
};

/**
 * Maps model name → short one-liner description shown under the model name.
 * Keyed by modelName (exact match from API).
 */
export const MODEL_DESCRIPTIONS: Record<string, string> = {
  'gpt-4o-mini': 'Fast and affordable GPT-4 class model for everyday tasks',
  'gpt-4o': 'Flagship multimodal model with vision and audio support',
  'gpt-5': 'Most capable OpenAI model with advanced reasoning',
  'gpt-5.2': 'Advanced conversational AI with strong reasoning and multimodal capabilities',
  'gpt-5-nano': 'Compact and fast model optimised for quick responses',
  'claude-3-5-sonnet-20241022': 'Balanced performance and intelligence from Anthropic',
  'claude-3-7-sonnet-20250219': "Anthropic's most intelligent model yet",
  'claude-opus-4-5': 'Context-aware multimodal AI with real-time memory and understanding',
  'gemini-1.5-pro': 'Long context multimodal reasoning from Google',
  'gemini-2.0-flash': 'Fast and efficient Gemini model with next-gen features',
  'mistral-large-latest': 'Mistral\'s top-tier reasoning and instruction-following model',
};


/**
 * Query mode configurations for the mode selector panel.
 *
 * Each mode references CSS variables defined in globals.css
 * (--mode-{id}-bg, --mode-{id}-fg, --mode-{id}-icon).
 *
 * Disabled modes are shown in the panel but cannot be selected.
 * Enable them here once backend support is added.
 */
export const QUERY_MODES: QueryModeConfig[] = [
  {
    id: 'agent',
    label: 'Agent',
    toolbarLabel: 'chat.queryModes.agent.toolbarLabel',
    description: 'Delegate multi-step reasoning and tool use with a configurable strategy',
    icon: 'smart_toy',
    iconType: 'material',
    colors: {
      bg: 'var(--mode-agent-bg)',
      fg: 'var(--mode-agent-fg)',
      icon: 'var(--mode-agent-icon)',
      toggle: 'var(--mode-agent-toggle)',
    },
    enabled: true,
  },
  {
    id: 'chat',
    label: 'Internal Search',
    toolbarLabel: 'chat.queryModes.chat.toolbarLabel',
    description: 'Think, ask, and reason across your knowledge in a conversational flow',
    icon: 'chat-star',
    iconType: 'component',
    colors: {
      bg: 'var(--mode-chat-bg)',
      fg: 'var(--mode-chat-fg)',
      icon: 'var(--mode-chat-icon)',
      toggle: 'var(--mode-chat-active-toggle)',
    },
    enabled: true,
  },
  {
    id: 'web-search',
    label: 'Web Search',
    toolbarLabel: 'chat.queryModes.web-search.toolbarLabel',
    description: 'Live web search',
    icon: 'language',
    iconType: 'material',
    colors: {
      bg: 'var(--mode-web-search-bg)',
      fg: 'var(--mode-web-search-fg)',
      icon: 'var(--mode-web-search-icon)',
      toggle: 'var(--mode-web-search-toggle)',
    },
    enabled: true,
  },
];

/** Search mode colors (separate from query modes) */
export const SEARCH_MODE_COLORS = {
  bg: 'var(--mode-search-bg)',
  fg: 'var(--mode-search-fg)',
  icon: 'var(--mode-search-icon)',
} as const;

/**
 * Returns the QueryModeConfig for a given query mode ID.
 */
export function getQueryModeConfig(id: string): QueryModeConfig | undefined {
  return QUERY_MODES.find((m) => m.id === id);
}

/**
 * Hardcoded follow-up question sets for the "Ask More" feature.
 *
 * Each set contains 4–6 generic questions. At render time one set is
 * randomly selected and displayed in full beneath the last bot response.
 * The randomisation happens per new message pair so users see fresh
 * suggestions after every answer.
 *
 * TODO: Replace with an API-driven suggestion endpoint when available.
 */
export const ASK_MORE_QUESTION_SETS: string[][] = [
  // Set 1 — strategy & planning
  [
    'What are the key challenges and how are they being addressed',
    'Can you break down the main strategies involved',
    'What metrics or KPIs are used to measure success',
    'How does this compare to industry best practices',
    'What are the potential risks and mitigation plans',
    'Who are the main stakeholders and what are their roles',
  ],
  // Set 2 — deep-dive & analysis
  [
    'Can you provide more details on the implementation process',
    'What tools or technologies are being used',
    'How has this evolved over the past few years',
    'What are the most common pain points teams face',
    'Are there any case studies or examples to reference',
  ],
  // Set 3 — operational focus
  [
    'How is the team structured to support this',
    'What does the onboarding process look like',
    'How are decisions prioritized and communicated',
    'What feedback mechanisms are in place',
  ],
  // Set 4 — growth & impact
  [
    'What growth opportunities have been identified',
    'How does this impact the overall business goals',
    'What integrations or partnerships are involved',
    'Can you summarize the key takeaways',
    'What would a roadmap for the next quarter look like',
    'How are results reported to leadership',
  ],
  // Set 5 — exploration & context
  [
    'What background context is important to understand this',
    'How does this relate to other ongoing initiatives',
    'What are the short-term vs long-term priorities',
    'Are there any dependencies or blockers to be aware of',
    'What lessons have been learned so far',
  ],
];

/** Maximum number of visible chats in the sidebar. */
export const MAX_VISIBLE_CHATS = 10;

/** Number of conversations fetched per page in the More Chats infinite scroll panel. */
export const MORE_CHATS_PAGE_SIZE = 20;

/** Page size for the chat sidebar Agents panel (search + infinite scroll). */
export const AGENTS_SIDEBAR_PAGE_SIZE = 20;

/** How many agents to show in the main chat sidebar before "More agents". */
export const MAX_VISIBLE_AGENTS_IN_SIDEBAR = 3;

/** Fetch at least this many agents to decide if "More" is needed. */
export const SIDEBAR_AGENTS_PREVIEW_FETCH_LIMIT = 20;

/** Page size for GET /api/v1/agents/:id/conversations (main + more panel). */
export const AGENT_CONVERSATIONS_PAGE_SIZE = 20;
