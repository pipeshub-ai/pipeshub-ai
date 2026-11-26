import { i18n } from '@/lib/i18n';
import { budgetRefusalKey } from './code-tasks';
import { answerErrorText, runErrorText } from './harness-errors';
import type { RunOutcome } from './run-outcome';

/** What a Node before the typed outcome sent beside the code (RUN_ERROR's fields, a saved failure's). */
export interface LegacyFailureFields {
  servers?: unknown;
  details?: unknown;
}

function labels(value: unknown): string[] | null {
  return Array.isArray(value) ? value.filter((label): label is string => typeof label === 'string') : null;
}

/**
 * WP-100 (ADR-RUN-01 D1): the words of a run that ended without an answer, one choice for the live `RUN_ERROR` and
 * the saved failure, so a reload says what the stream said. From the run's typed outcome when Node sent one (its
 * reason, the servers it names, the control that stopped it), else from the fields an older Node sent. An error
 * answer's code first (its words name the next step), then a budget stop, then the run ending's own words; null
 * keeps the server's message.
 */
export function runFailureWords(
  code: string | null,
  outcome: RunOutcome | null,
  legacy: LegacyFailureFields = {},
): string | null {
  const reason = outcome?.kind === 'failed' && outcome.reason !== undefined ? outcome.reason : code;
  const answer = answerErrorText(reason);
  if (answer !== null) return answer;
  const budget = budgetRefusalKey(reason);
  if (budget !== null) return i18n.t(budget);
  const details =
    outcome?.stop ?? (outcome?.memoryLimitMb !== undefined ? { memoryLimitMb: outcome.memoryLimitMb } : legacy.details);
  return runErrorText(reason, outcome?.servers ?? labels(legacy.servers), details);
}
