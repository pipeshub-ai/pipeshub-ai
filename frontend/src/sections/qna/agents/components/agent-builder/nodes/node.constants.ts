// Node configuration constants
// Centralized configuration for all node types and their behaviors

/**
 * Node types that should NOT display input handles
 * These nodes connect via specific agent handles rather than generic inputs
 */
export const NODE_TYPES_WITHOUT_INPUT_HANDLES = {
  TOOLS: (type: string) => type.startsWith('tool-'),
  KNOWLEDGE_APPS: (type: string) => type.startsWith('app-'),
  KNOWLEDGE_BASES: (type: string) => type.startsWith('kb-'),
  CONNECTOR_GROUPS: (type: string) => type.startsWith('connector-group-'),
  LLM_MODELS: (type: string) => type.startsWith('llm-'),
} as const;

/**
 * Node types that should NOT display output handles
 * These are endpoint nodes that don't connect further
 */
export const NODE_TYPES_WITHOUT_OUTPUT_HANDLES = {
  CHAT_RESPONSE: (type: string) => type === 'chat-response',
  OUTPUT: (type: string) => type === 'output',
} as const;

/**
 * Special node types with custom rendering
 */
export const SPECIAL_NODE_TYPES = {
  AGENT_CORE: 'agent-core',
  CHAT_RESPONSE: 'chat-response',
  USER_INPUT: 'user-input',
} as const;

/**
 * Handle positioning configuration
 */
export const HANDLE_CONFIG = {
  INPUT: {
    POSITION_OFFSET: 45, // Base top position percentage
    POSITION_INCREMENT: 25, // Increment for multiple handles
    OFFSET_LEFT: -9, // Pixel offset from left edge (-(SIZE/2 + BORDER_WIDTH) for perfect centering)
    SIZE: 14, // Handle size in pixels
    BORDER_WIDTH: 2,
    Z_INDEX: 10,
  },
  OUTPUT: {
    POSITION_OFFSET: 45,
    POSITION_INCREMENT: 25,
    OFFSET_RIGHT: -9, // Pixel offset from right edge (-(SIZE/2 + BORDER_WIDTH) for perfect centering)
    SIZE: 14,
    BORDER_WIDTH: 2,
    Z_INDEX: 10,
  },
} as const;

/**
 * Node dimension constraints
 */
export const NODE_DIMENSIONS = {
  AGENT_CORE: {
    WIDTH: 420,
    MIN_HEIGHT: 650,
  },
  CHAT_RESPONSE: {
    WIDTH: 280,
    MIN_HEIGHT: 160,
  },
  LLM: {
    WIDTH: 280,
    MIN_HEIGHT: 160,
  },
  TOOL: {
    WIDTH: 280,
    MIN_HEIGHT: 140,
  },
  KNOWLEDGE: {
    WIDTH: 280,
    MIN_HEIGHT: 140,
  },
  DEFAULT: {
    WIDTH: 280,
    MIN_HEIGHT: 120,
  },
} as const;

/**
 * Dialog double-click timeout
 */
export const DOUBLE_CLICK_TIMEOUT = 300; // milliseconds

/**
 * Color scheme configuration for dark/light modes
 */
export const getNodeColors = (isDark: boolean) => ({
  primary: isDark ? '#6366f1' : '#4f46e5',
  secondary: isDark ? '#8b5cf6' : '#7c3aed',
  success: isDark ? '#10b981' : '#059669',
  warning: isDark ? '#f59e0b' : '#d97706',
  info: isDark ? '#06b6d4' : '#0891b2',
  background: {
    card: isDark ? '#1e1e1e' : '#ffffff',
    section: isDark ? '#2a2a2a' : '#f8fafc',
    hover: isDark ? '#333333' : '#f1f5f9',
  },
  border: {
    main: isDark ? '#3a3a3a' : '#e2e8f0',
    subtle: isDark ? '#2a2a2a' : '#f1f5f9',
    focus: isDark ? '#6366f1' : '#4f46e5',
  },
  text: {
    primary: isDark ? '#f8fafc' : '#0f172a',
    secondary: isDark ? '#cbd5e1' : '#64748b',
    muted: isDark ? '#94a3b8' : '#94a3b8',
  },
});

/**
 * Agent core handle types and their labels
 */
export const AGENT_CORE_HANDLES = {
  INPUT: 'input',
  ACTIONS: 'actions',
  KNOWLEDGE: 'knowledge',
  LLMS: 'llms',
} as const;

/**
 * Node category labels for UI display
 */
export const NODE_CATEGORY_LABELS = {
  TOOLS: 'Tools',
  KNOWLEDGE: 'Knowledge',
  APPS: 'Apps',
  MODELS: 'Models',
  AGENT: 'Agent',
} as const;

