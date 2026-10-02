/**
 * Log-safe summary of a request to the AI service. User queries, conversation
 * history, attachments and caller context are customer content and must not
 * reach logs; sizes and counts are enough to debug payload problems.
 */
export interface AiRequestLogMetadata {
  queryLength: number;
  previousConversationCount: number;
  previousConversationChars: number;
  attachmentCount: number;
  filterAppCount: number;
  filterKbCount: number;
  toolCount: number;
  chatMode?: string;
  modelKey?: string;
}

const lengthOf = (value: unknown): number =>
  typeof value === 'string' ? value.length : 0;

const countOf = (value: unknown): number =>
  Array.isArray(value) ? value.length : 0;

export const buildAiRequestLogMetadata = (
  payload: Record<string, unknown>,
): AiRequestLogMetadata => {
  const history = Array.isArray(payload.previousConversations)
    ? payload.previousConversations
    : [];
  const filters =
    payload.filters !== null && typeof payload.filters === 'object'
      ? (payload.filters as Record<string, unknown>)
      : {};
  return {
    queryLength: lengthOf(payload.query),
    previousConversationCount: history.length,
    previousConversationChars: history.reduce<number>(
      (total, turn) =>
        total + lengthOf((turn as Record<string, unknown> | null)?.content),
      0,
    ),
    attachmentCount: countOf(payload.attachments),
    filterAppCount: countOf(filters.apps),
    filterKbCount: countOf(filters.kb),
    toolCount: countOf(payload.tools),
    ...(typeof payload.chatMode === 'string'
      ? { chatMode: payload.chatMode }
      : {}),
    ...(typeof payload.modelKey === 'string'
      ? { modelKey: payload.modelKey }
      : {}),
  };
};
