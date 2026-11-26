/**
 * BUGS-B: main's "Don't ask again" on the durable approval card. Shown only
 * where the run offered it (`reason.ruleGrant`: main's default rule for the
 * person's own tool) and only in the conversation's own chat; checked, Allow
 * and Deny send the decision with `remember`, so it becomes the person's rule
 * for that tool, as on today's chat card.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import { ChatApi } from '../../../api';
import { decideApproval } from '../../../approvals';
import { toApprovalReason } from '../../../approval-preview';
import { useChatStore } from '../../../store';
import type { ApprovalReason, PendingApproval } from '../../../types';

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const RULE: ApprovalReason = {
  rule: 'writes',
  layer: 'deployment',
  sources: [],
  moreSources: 0,
  conversationGrant: false,
  ruleGrant: true,
};

function approval(reason: ApprovalReason = RULE): PendingApproval {
  return {
    interactionId: 'ix.approve.t1.0',
    toolCallId: 'call-1',
    toolName: 'mcp_github_list_prs',
    args: '{"repo":"pipeshub"}',
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    reason,
  };
}

function renderCard(props: Partial<React.ComponentProps<typeof ApprovalCard>> = {}) {
  const onDecide = vi.fn();
  render(h(Theme, null, h(ApprovalCard, { approval: approval(), onDecide, conversationChoice: true, ...props })));
  return onDecide;
}

describe("BUGS-B: Don't ask again on the durable card", () => {
  it('DA-F1: offered by the run, unchecked by default: Allow and Deny decide this call only', () => {
    const onDecide = renderCard();
    expect(screen.getByRole('checkbox', { name: "Don't ask again for this tool" })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /allow/i }));
    expect(onDecide).toHaveBeenLastCalledWith('approved');
  });

  it('DA-F2: checked, Allow and Deny save the answer as the rule', () => {
    const onDecide = renderCard();
    fireEvent.click(screen.getByRole('checkbox', { name: "Don't ask again for this tool" }));
    fireEvent.click(screen.getByRole('button', { name: /allow/i }));
    expect(onDecide).toHaveBeenLastCalledWith('approved', 'tool');
    fireEvent.click(screen.getByRole('button', { name: /deny/i }));
    fireEvent.click(screen.getByTestId('deny-confirm'));
    expect(onDecide).toHaveBeenLastCalledWith('denied', 'tool');
  });

  it('DA-F3: not offered, or outside the conversation\'s own chat: no checkbox', () => {
    renderCard({ approval: approval({ ...RULE, ruleGrant: undefined }) });
    expect(screen.queryByRole('checkbox')).toBeNull();
    cleanup();
    renderCard({ conversationChoice: false });
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('DA-F4: the reason keeps ruleGrant only when the run sent it', () => {
    expect(toApprovalReason({ rule: 'writes', conversationGrant: false, ruleGrant: true })?.ruleGrant).toBe(true);
    expect(toApprovalReason({ rule: 'writes', conversationGrant: false })).not.toHaveProperty('ruleGrant');
  });

  it('DA-F5: the decision is sent with remember under its own idempotency key', async () => {
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    const slot = useChatStore.getState().createSlot('ses-da');
    useChatStore.getState().updateSlot(slot, {
      durableRun: { agentId: 'agent-1', sessionId: 'ses-da', runId: 'run-1' },
      pendingApprovals: [{ ...approval(), runId: 'run-1' }],
    });
    const card = useChatStore.getState().slots[slot]!.pendingApprovals![0]!;
    await decideApproval(slot, card, 'denied', 'tool');
    expect(decide).toHaveBeenLastCalledWith(
      { agentId: 'agent-1', sessionId: 'ses-da', runId: 'run-1' },
      'ix.approve.t1.0',
      'denied',
      'ix.approve.t1.0:denied:remember',
      'tool',
    );
    expect(useChatStore.getState().slots[slot]!.pendingApprovals![0]!.status).toBe('denied');
  });
});
