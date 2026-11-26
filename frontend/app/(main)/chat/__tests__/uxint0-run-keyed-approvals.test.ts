import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChatApi } from '../api';
import { approvalStreamCallbacks, decideApproval, withApprovalRequested, withApprovalResolved } from '../approvals';
import { useChatStore } from '../store';
import type { PendingApproval, SSEApprovalRequestedEvent } from '../types';

// UXINT-0 (R1 + R6): a loop's interaction ids repeat in every run of a conversation (`ix.approve.t2.0`), so a card
// is named by its run as well, and a slot's cards are those of the run it follows now.
const asked: SSEApprovalRequestedEvent = {
  interactionId: 'ix.approve.t2.0',
  toolCallId: 'call-1',
  toolName: 'e2esaas__create_ticket',
  args: '{"title":"uxint s11"}',
  argsSummary: null,
};
const run = (runId: string) => ({ agentId: 'agent-1', sessionId: 'ses-1', runId });

describe('UXINT-0: approval cards keyed by run and interaction', () => {
  afterEach(() => vi.restoreAllMocks());

  it('the same interaction id in run 1 and run 2 is two cards, and a resolution closes only its own', () => {
    let list: PendingApproval[] = withApprovalRequested([], { ...asked, runId: 'run-1' });
    list = withApprovalResolved(list, { interactionId: asked.interactionId, decision: 'approved', runId: 'run-1' });
    list = withApprovalRequested(list, { ...asked, runId: 'run-2' });
    expect(list.map((a) => [a.runId, a.status])).toEqual([
      ['run-1', 'approved'],
      ['run-2', 'pending'],
    ]);
    list = withApprovalResolved(list, { interactionId: asked.interactionId, decision: 'denied', runId: 'run-2' });
    expect(list.map((a) => a.status)).toEqual(['approved', 'denied']);
  });

  it('a second same-shaped turn shows its own actionable card, decided on its own run', async () => {
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    const slot = useChatStore.getState().createSlot('ses-1');
    const cards = () => useChatStore.getState().slots[slot]?.pendingApprovals ?? [];
    const { onApprovalRequested } = approvalStreamCallbacks(slot);

    useChatStore.getState().updateSlot(slot, { durableRun: run('run-1') });
    onApprovalRequested?.(asked);
    await decideApproval(slot, cards()[0]!, 'approved');
    expect(cards().map((a) => [a.runId, a.status])).toEqual([['run-1', 'approved']]);

    // Run 1 ends; the next message starts run 2, which asks with the same id.
    useChatStore.getState().updateSlot(slot, { durableRun: null });
    expect(cards()).toEqual([]);
    useChatStore.getState().updateSlot(slot, { durableRun: run('run-2') });
    onApprovalRequested?.(asked);
    expect(cards().map((a) => [a.runId, a.status])).toEqual([['run-2', 'pending']]);

    await decideApproval(slot, cards()[0]!, 'approved');
    expect(decide).toHaveBeenLastCalledWith(
      run('run-2'),
      asked.interactionId,
      'approved',
      `${asked.interactionId}:approved`,
    );
  });

  it('a decided card never carries into the next run, even when no terminal frame cleared it', () => {
    const slot = useChatStore.getState().createSlot('ses-2');
    useChatStore.getState().updateSlot(slot, { durableRun: run('run-1') });
    approvalStreamCallbacks(slot).onApprovalRequested?.(asked);
    approvalStreamCallbacks(slot).onApprovalResolved?.({ interactionId: asked.interactionId, decision: 'approved' });
    useChatStore.getState().updateSlot(slot, { durableRun: run('run-2') });
    approvalStreamCallbacks(slot).onApprovalRequested?.(asked);
    const cards = useChatStore.getState().slots[slot]?.pendingApprovals ?? [];
    expect(cards.map((a) => [a.runId, a.status])).toEqual([['run-2', 'pending']]);
  });

  it('the same run handed over again (a reattach) keeps its cards', () => {
    const slot = useChatStore.getState().createSlot('ses-3');
    useChatStore.getState().updateSlot(slot, { durableRun: run('run-1') });
    approvalStreamCallbacks(slot).onApprovalRequested?.(asked);
    useChatStore.getState().updateSlot(slot, { durableRun: { ...run('run-1') } });
    expect(useChatStore.getState().slots[slot]?.pendingApprovals).toHaveLength(1);
  });
});
