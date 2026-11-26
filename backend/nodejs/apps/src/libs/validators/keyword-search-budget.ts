import { z } from 'zod';

/**
 * How long each search waits for the keyword (grep) pattern match after its
 * semantic results: `short` caps the wait (Python `pattern_match_wait_budget`)
 * for callers that would rather answer sooner, such as agents calling chat
 * over MCP; `full`, or leaving it out, waits for all of it.
 *
 * The one schema the chat routes and the agent harness's run input both
 * apply (R33JGAPS-FU-1), so a turn the chat routes take is one the durable
 * route can carry rather than send to today's loop.
 */
export const keywordSearchBudgetSchema = z.enum(['short', 'full']);

export type KeywordSearchBudget = z.infer<typeof keywordSearchBudgetSchema>;
