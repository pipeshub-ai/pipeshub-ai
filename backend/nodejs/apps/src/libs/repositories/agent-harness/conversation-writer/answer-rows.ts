/**
 * The rows that stand as a turn's answer, which a regenerate may name: the
 * answer, or the error row a failed run left in its place. Try again on a
 * failed answer redoes it there, as today's loop does, and the new answer
 * replaces it.
 */
export const ANSWER_ROW_TYPES = ['bot_response', 'error'] as const;

/** Whether a row of this type stands as its turn's answer. */
export const isAnswerRow = (messageType: unknown): boolean =>
  (ANSWER_ROW_TYPES as readonly unknown[]).includes(messageType);
