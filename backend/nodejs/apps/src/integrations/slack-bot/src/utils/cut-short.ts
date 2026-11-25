/**
 * The notice an answer a limit cut short carries in Slack (BUDGETEND), the same words the web
 * chat shows: the run answered from what it had gathered before its spending limit, turn limit
 * or working time ran out. A budget beyond the run's own that the answer names is worded by the
 * bot's extension point (`budgetStopNotice`) where it says something. The bot has no locale of
 * its own, so it says it in English.
 */
import { slackBotEdition } from '../edition';

const NOTICES: Record<string, string> = {
  budget: 'Stopped early: spending limit reached',
  turns: 'Stopped early: turn limit reached',
  time: 'Stopped early: time limit reached',
  stall: 'Stopped early: its steps kept repeating without progress',
  provider: 'Stopped early: the AI model provider was unavailable',
  engine_error: 'Stopped early: the coding engine ran into an error',
  secret_blocked:
    'Stopped early: PipesHub blocked a request that looked like it contained a secret',
};

const HOW_TO_CONTINUE =
  'The answer uses what was gathered before the limit. Reply “continue” to carry on.';

/**
 * The notice for `truncatedBy`, or `null` when the answer was not cut short (or names a limit this
 * build does not know). `budgetScope`: which budget beyond the run's own stopped a budget cut, when one did.
 */
export function cutShortNotice(
  truncatedBy: unknown,
  budgetScope?: unknown,
  now: Date = new Date(),
): string | null {
  if (typeof truncatedBy !== 'string') return null;
  const scoped =
    truncatedBy === 'budget' && typeof budgetScope === 'string'
      ? slackBotEdition().budgetStopNotice(budgetScope, now)
      : null;
  if (scoped !== null) return scoped;
  const notice = NOTICES[truncatedBy];
  return notice === undefined ? null : `${notice}. ${HOW_TO_CONTINUE}`;
}

/** The Block Kit context block posted under the answer, or `null` when there is nothing to say. */
export function cutShortBlocks(
  truncatedBy: unknown,
  budgetScope?: unknown,
  now?: Date,
): Array<Record<string, unknown>> | null {
  const notice = cutShortNotice(truncatedBy, budgetScope, now);
  if (notice === null) return null;
  return [
    {
      type: 'context',
      elements: [{ type: 'mrkdwn', text: `:warning: ${notice}` }],
    },
  ];
}

/**
 * WP-89: an answer whose outcome the web app words in the reader's language holds only the model's prose; the
 * bot has no locale, so it appends the presenter's English closing, which already says why it stopped and how to
 * carry on (so the cut-short notice is not added again).
 */
export function withOutcomeClosing<
  T extends {
    content: string;
    truncatedBy?: string;
    runOutcome?: { closing?: unknown };
  },
>(response: T): T {
  const closing = response.runOutcome?.closing;
  if (typeof closing !== 'string') return response;
  const { truncatedBy: _said, ...rest } = response;
  if (closing.length === 0) return rest as T;
  const content =
    response.content.length > 0 ? `${response.content}\n\n${closing}` : closing;
  const closed = { ...rest, content };
  return closed as T;
}
