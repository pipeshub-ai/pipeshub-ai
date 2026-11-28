/**
 * What a turn's model is given of the chat's earlier turns when people besides its owner read the chat: only the
 * earlier answers' text, never the tool results they were built on (the search results, fetched documents and tool
 * outputs of earlier turns). A fact a later answer states that no earlier answer said must then come from that turn's
 * own tools, which cite it, and the reader check judges the answer by those citations. A chat nobody else reads keeps
 * its earlier tool results. Decided when each turn starts, from the chat as it is then, so a turn after the chat is
 * shared follows the new state.
 */

/** What says who else reads a chat: its share rows while it is shared, and whether its project may open it. */
export interface ChatReach {
  isShared?: boolean | null;
  sharedWith?: readonly unknown[] | null;
  projectVisibility?: string | null;
}

/** Whether anyone besides the chat's owner may read it now: shared with a person or a team, or open to its project. */
export const readByOthers = (chat: ChatReach | null | undefined): boolean =>
  chat !== null &&
  chat !== undefined &&
  ((chat.isShared === true &&
    (chat.sharedWith ?? []).some(
      (row) => row !== null && typeof row === 'object',
    )) ||
    chat.projectVisibility === 'project');

/** The ask tool's rows are what the person answered on a question card: their own words, not data a tool read. */
const isAskResult = (row: unknown): boolean => {
  if (row === null || typeof row !== 'object') return false;
  const name = (row as { tool_name?: unknown }).tool_name;
  return typeof name === 'string' && name.includes('ask_user_question');
};

/**
 * History turns as a turn in a chat others read is given them: each earlier answer without the tool results replayed
 * under it (`tool_results`, except the question cards the person answered) and without the reference data its tools
 * returned (`referenceData`). Questions, their attachments and the answers' text are unchanged.
 */
export const answersOnlyTurns = <T extends Record<string, unknown>>(
  turns: readonly T[],
): T[] =>
  turns.map((turn) => {
    const {
      tool_results: results,
      referenceData: _referenceData,
      ...rest
    } = turn;
    const asks = Array.isArray(results) ? results.filter(isAskResult) : [];
    return (asks.length > 0 ? { ...rest, tool_results: asks } : rest) as T;
  });
