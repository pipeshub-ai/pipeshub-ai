import { CUT_SHORT_REASONS, type CutShortReason } from '@/lib/contracts/harness/generated/core/enums';
import type { MessagePart, BudgetScope } from './types';

/**
 * Which limit cut an answer short (BUDGETEND): the run answered from what it had gathered before its spending limit,
 * turn limit or working time ran out. The saved message names it (durable and legacy runs alike), and so does a
 * durable answer's final text part, which a shared viewer also keeps.
 */
export function cutShortReason(value: unknown): CutShortReason | null {
  return typeof value === 'string' && (CUT_SHORT_REASONS as readonly string[]).includes(value)
    ? (value as CutShortReason)
    : null;
}

/** The message's own field first, else its final text part's. */
export function cutShortOf(truncatedBy: unknown, parts: MessagePart[] | undefined): CutShortReason | null {
  const own = cutShortReason(truncatedBy);
  if (own !== null) return own;
  const final = parts?.find((part) => part.type === 'text' && part.isFinal === true);
  return cutShortReason(final?.truncatedBy);
}

/** The shape of a budget scope on the wire (the answer's contract). */
const BUDGET_SCOPE = /^[a-z][a-z0-9_-]{0,31}$/;

/**
 * Which budget beyond the run's own stopped a budget cut: the message's own field first, else its final text part's;
 * null for the run's own spending limit, another limit, or a value not shaped as a scope.
 */
export function budgetScopeOf(
  reason: CutShortReason | null | undefined,
  own: unknown,
  parts: MessagePart[] | undefined,
): BudgetScope | null {
  if (reason !== 'budget') return null;
  const known = (value: unknown): BudgetScope | null =>
    typeof value === 'string' && BUDGET_SCOPE.test(value) ? value : null;
  return known(own) ?? known(parts?.find((part) => part.type === 'text' && part.isFinal === true)?.budgetScope);
}

/** The notice's i18n key for each limit. */
export const CUT_SHORT_KEYS: Record<CutShortReason, string> = {
  budget: 'chat.cutShort.budget',
  turns: 'chat.cutShort.turns',
  time: 'chat.cutShort.time',
  stall: 'chat.cutShort.stall',
  provider: 'chat.cutShort.provider',
  engine_error: 'chat.cutShort.engineError',
  secret_blocked: 'chat.cutShort.secretBlocked',
};

/**
 * The line under a cut-short answer saying how to go on: a model that kept refusing requests is not carried on by
 * "continue" while it refuses, so the person is told to wait or have an admin check its limits (FIX-429).
 */
export function cutShortHintKey(reason: CutShortReason | null): string {
  return reason === 'provider' ? 'chat.cutShort.providerHint' : 'chat.cutShort.hint';
}
