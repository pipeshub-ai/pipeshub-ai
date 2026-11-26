import type { NextActionKind } from '@/lib/contracts/harness/generated/core/enums';
import type { ApprovalReason, DecisionAuthority, PendingApproval } from './types';

/**
 * Rules beyond the usual one that decide who approves an ask, as the chat shows them. Editions plug in via config.ts;
 * this default names none, so every approval follows the usual rule.
 */
export interface ApprovalAskRules {
  /** A rule beyond the usual one decides who approves this ask, so the card may wait on someone else. */
  holds(reason: ApprovalReason | undefined): boolean;
  /**
   * Where a rule decides the ask: whether the viewer may approve it now. Undefined: the authority's `approve`.
   * `interactionId` is given on the ask's own card.
   */
  mayApprove(
    authority: DecisionAuthority,
    approval: Pick<PendingApproval, 'reason'> & Partial<Pick<PendingApproval, 'interactionId'>>,
  ): boolean | undefined;
  /** The sentence key explaining a hold reason this build does not word; undefined: none. */
  whyKey(reason: ApprovalReason): string | undefined;
  /** The values that sentence takes, read from the reason's edition fields; undefined: none. */
  whyValues(reason: ApprovalReason): Readonly<Record<string, string | number>> | undefined;
  /** A rule's ask is decided on its own card, never with others under "Allow all". */
  ownCard(reason: ApprovalReason): boolean;
  /** The Needs-you list's one-line key for a hold reason this build does not word; undefined: its default line. */
  lineKey(reason: ApprovalReason): string | undefined;
  /** Words and the next step for error answer codes an edition adds (its overlay of `AnswerErrorCode`), by code. */
  answerErrors: Readonly<Record<string, { text: () => string; next: NextActionKind }>>;
  /** Text keys for the refusals a rule answers with, by error code. */
  refusalKeys: Readonly<Record<string, string>>;
}

export const approvalAskRules: ApprovalAskRules = Object.freeze({
  holds: () => false,
  mayApprove: () => undefined,
  whyKey: () => undefined,
  whyValues: () => undefined,
  ownCard: () => false,
  lineKey: () => undefined,
  answerErrors: Object.freeze({}),
  refusalKeys: Object.freeze({}),
});
