export const NODE_TYPES_WITHOUT_INPUT_HANDLES = {
  TOOLS: (type: string) => type.startsWith('tool-'),
  KNOWLEDGE_APPS: (type: string) => type.startsWith('app-'),
  KNOWLEDGE_BASES: (type: string) => type.startsWith('kb-'),
  CONNECTOR_GROUPS: (type: string) => type.startsWith('connector-group-'),
  LLM_MODELS: (type: string) => type.startsWith('llm-'),
} as const;

export const NODE_TYPES_WITHOUT_OUTPUT_HANDLES = {
  CHAT_RESPONSE: (type: string) => type === 'chat-response',
  OUTPUT: (type: string) => type === 'output',
} as const;

export const HANDLE_CONFIG = {
  INPUT: {
    POSITION_OFFSET: 44,
    POSITION_INCREMENT: 22,
    OFFSET_LEFT: -8,
    SIZE: 13,
    Z_INDEX: 50,
  },
  OUTPUT: {
    POSITION_OFFSET: 44,
    POSITION_INCREMENT: 22,
    OFFSET_RIGHT: -8,
    SIZE: 13,
    Z_INDEX: 50,
  },
} as const;
