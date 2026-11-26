import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAGUIEventHandler } from '../agui-event-handler';
import { ChatApi } from '../api';
import type { StreamMessageCallbacks } from '../api';
import {
  approvalStreamCallbacks,
  decideApproval,
  withApprovalRequested,
  withApprovalResolved,
} from '../approvals';
import { useChatStore } from '../store';
import type { PendingApproval, SSEApprovalRequestedEvent } from '../types';
import type { SSEEvent } from '@/lib/api';

const requested: SSEApprovalRequestedEvent = {
  interactionId: 'ix.approve.t1.0',
  toolCallId: 'call-1',
  toolName: 'slack__post_message',
  args: '{"text":"hi"}',
  argsSummary: null,
};

function frame(type: string, fields: Record<string, unknown>): SSEEvent {
  return { event: type, data: { type, ...fields } };
}

describe('approval frames (P2.6c UI-4)', () => {
  it('routes CUSTOM approval_requested / approval_resolved to their callbacks', () => {
    const onApprovalRequested = vi.fn();
    const onApprovalResolved = vi.fn();
    const handle = createAGUIEventHandler({ onApprovalRequested, onApprovalResolved } as StreamMessageCallbacks);
    handle(frame('CUSTOM', { name: 'approval_requested', value: requested }));
    handle(frame('CUSTOM', { name: 'approval_resolved', value: { interactionId: requested.interactionId, decision: 'approved' } }));
    expect(onApprovalRequested).toHaveBeenCalledWith(requested);
    expect(onApprovalResolved).toHaveBeenCalledWith({ interactionId: requested.interactionId, decision: 'approved' });
  });

  it('keeps one card per interaction and closes it on any resolution', () => {
    let list: PendingApproval[] = [];
    list = withApprovalRequested(list, requested);
    list = withApprovalRequested(list, requested);
    expect(list).toHaveLength(1);
    expect(list[0]?.status).toBe('pending');
    expect(withApprovalResolved(list, { interactionId: requested.interactionId, decision: 'denied' })[0]?.status).toBe('denied');
    expect(withApprovalResolved(list, { interactionId: requested.interactionId, decision: 'expired' })[0]?.status).toBe('closed');
    expect(withApprovalResolved(list, { interactionId: 'other', decision: 'approved' })[0]?.status).toBe('pending');
  });
});

describe('deciding an approval (P2.6c UI-2, UI-3)', () => {
  let SLOT = '';

  beforeEach(() => {
    SLOT = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(SLOT, {
      durableRun: { agentId: 'agent-1', sessionId: 'ses-1', runId: 'run-1' },
    });
    approvalStreamCallbacks(SLOT).onApprovalRequested?.(requested);
  });
  afterEach(() => vi.restoreAllMocks());

  const card = () => useChatStore.getState().slots[SLOT]?.pendingApprovals?.[0];

  it('sends the decision with a stable idempotency key and shows it once accepted', async () => {
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    await decideApproval(SLOT, card()!, 'approved');
    expect(decide).toHaveBeenCalledWith(
      { agentId: 'agent-1', sessionId: 'ses-1', runId: 'run-1' },
      requested.interactionId,
      'approved',
      `${requested.interactionId}:approved`,
    );
    expect(card()?.status).toBe('approved');
  });

  it('UX1: allowing for the conversation sends the scope under its own key and shows it once accepted', async () => {
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    await decideApproval(SLOT, card()!, 'approved', 'conversation');
    expect(decide).toHaveBeenCalledWith(
      { agentId: 'agent-1', sessionId: 'ses-1', runId: 'run-1' },
      requested.interactionId,
      'approved',
      `${requested.interactionId}:approved:conversation`,
      'conversation',
    );
    expect(card()?.status).toBe('approved');
    expect(card()?.scope).toBe('conversation');
  });

  it('a refused decision keeps the card open with the reason', async () => {
    vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({ refused: 'HTTP_AGENT_HARNESS_APPROVER_REQUIRED' });
    await decideApproval(SLOT, card()!, 'approved');
    expect(card()?.status).toBe('pending');
    expect(card()?.errorCode).toBe('HTTP_AGENT_HARNESS_APPROVER_REQUIRED');
  });
});
