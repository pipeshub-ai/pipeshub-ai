/**
 * Round 12 (WP-51 x WP-50/54): the run's plan is decided on its own card, never inside an "Allow all" group.
 *
 * WP-51 draws a plan approval as the plan card (summary, numbered steps, Approve plan / Decline) only when its group
 * holds that one approval. Two `submit_plan` asks in one batch (a run that revised its plan before anyone answered)
 * grouped into one generic card, whose "Allow all" approved plans nobody had read. The inbox already keeps a plan out
 * of a batch (Node's `batchExclusion`: only a layer's own write rules batch).
 *
 * R12-PLAN-1 two plan asks of one batch are two cards, each a plan card.
 * R12-PLAN-2 a plan ask and an ordinary write ask of the same batch never share a card.
 */
import { describe, expect, it } from 'vitest';
import { groupApprovals } from '../../../interactions';
import type { ApprovalReason, PendingApproval } from '../../../types';

const PLAN: ApprovalReason = { rule: 'plan_approval', layer: 'run', sources: [], moreSources: 0, conversationGrant: false };
const WRITE: ApprovalReason = { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: true };

function ask(id: string, toolName: string, reason: ApprovalReason): PendingApproval {
  return {
    interactionId: id,
    toolCallId: `call-${id}`,
    toolName,
    args: JSON.stringify({ plan: 'Tidy the backlog', steps: ['Read the issues', 'Close the stale ones'] }),
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    reason,
    afterTurns: 1,
    runId: 'run-1',
  };
}

describe('the plan card stands alone (G-04)', () => {
  it('R12-PLAN-1: two plan asks of one batch are two cards', () => {
    const groups = groupApprovals([ask('ix.1', 'submit_plan', PLAN), ask('ix.2', 'submit_plan', PLAN)]);
    expect(groups.map((group) => group.items.length)).toEqual([1, 1]);
  });

  it('R12-PLAN-2: a plan ask never shares a card with a write ask', () => {
    const groups = groupApprovals([ask('ix.1', 'submit_plan', PLAN), ask('ix.2', 'jira.create_issue', WRITE), ask('ix.3', 'jira.create_issue', WRITE)]);
    expect(groups.map((group) => group.items.map((item) => item.interactionId))).toEqual([['ix.1'], ['ix.2', 'ix.3']]);
  });
});
