/** Container identifiers for the conversation access guards and the turn handlers' dependencies. */
export const CONVERSATION_TYPES = {
  ConversationGuards: Symbol.for('conversation.Guards'),
  ConversationTurnDeps: Symbol.for('conversation.TurnDeps'),
} as const;
