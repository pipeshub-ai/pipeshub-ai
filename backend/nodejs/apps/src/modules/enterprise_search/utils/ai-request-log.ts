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
}

export interface FilterLogMetadata {
  filterAppCount: number;
  filterKbCount: number;
}

const lengthOf = (value: unknown): number =>
  typeof value === 'string' ? value.length : 0;

const countOf = (value: unknown): number =>
  Array.isArray(value) ? value.length : 0;

// Filter values (app and collection ids, and any free text a caller put in
// them) are request content, so only their counts are logged.
export const buildFilterLogMetadata = (filters: unknown): FilterLogMetadata => {
  const groups =
    filters !== null && typeof filters === 'object'
      ? (filters as Record<string, unknown>)
      : {};
  return {
    filterAppCount: countOf(groups.apps),
    filterKbCount: countOf(groups.kb),
  };
};

export const buildAiRequestLogMetadata = (
  payload: Record<string, unknown>,
): AiRequestLogMetadata => {
  const history = Array.isArray(payload.previousConversations)
    ? payload.previousConversations
    : [];
  return {
    queryLength: lengthOf(payload.query),
    previousConversationCount: history.length,
    previousConversationChars: history.reduce<number>(
      (total, turn) =>
        total + lengthOf((turn as Record<string, unknown> | null)?.content),
      0,
    ),
    attachmentCount: countOf(payload.attachments),
    ...buildFilterLogMetadata(payload.filters),
    toolCount: countOf(payload.tools),
    // chatMode is an enum in the request schemas; modelKey is any non-empty
    // string a caller sends, so it is left out.
    ...(typeof payload.chatMode === 'string'
      ? { chatMode: payload.chatMode }
      : {}),
  };
};
