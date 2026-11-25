/**
 * AP-06 (C3.75), WP-50: approving with changes posts the edited arguments on the call's own decision route (never
 * with a scope or "Don't ask again"), and the card keeps what ran for its receipt; a saved answer brings it back
 * after a reload; the authority read says whether the viewer may edit.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { ChatApi } from '../api';
import { approvalStreamCallbacks, decideApproval } from '../approvals';
import { approvalFromPart } from '../durable-run-ui';
import { toApprovalReason } from '../approval-preview';
import { useChatStore } from '../store';
import type { MessagePart } from '../types';

const requested = {
  interactionId: 'ix.approve.t1.0',
  toolCallId: 'call-1',
  toolName: 'gmail__send_email',
  args: '{"to":"a@acme.com"}',
  argsSummary: null,
};

describe('AP-06 deciding with edits', () => {
  let SLOT = '';
  beforeEach(() => {
    SLOT = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(SLOT, { durableRun: { agentId: 'agent-1', sessionId: 'ses-1', runId: 'run-1' } });
    approvalStreamCallbacks(SLOT).onApprovalRequested?.(requested);
  });
  afterEach(() => vi.restoreAllMocks());

  it('AP-06: the edits go with the approval of this one call, and the receipt keeps them', async () => {
    const decide = vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({});
    await decideApproval(SLOT, { ...requested, runId: 'run-1' }, 'approved', 'once', undefined, { to: 'b@acme.com' });
    expect(decide).toHaveBeenCalledWith(
      { agentId: 'agent-1', sessionId: 'ses-1', runId: 'run-1' },
      'ix.approve.t1.0',
      'approved',
      'ix.approve.t1.0:approved:edited',
      undefined,
      undefined,
      { to: 'b@acme.com' },
    );
    const [card] = useChatStore.getState().slots[SLOT]?.pendingApprovals ?? [];
    expect(card).toMatchObject({ status: 'approved', editedArgs: '{"to":"b@acme.com"}' });
  });

  it('AP-06: a refused edit leaves the card waiting with the refusal, and nothing kept', async () => {
    vi.spyOn(ChatApi, 'decideDurableInteractionOutcome').mockResolvedValue({ refused: 'HTTP_AGENT_HARNESS_EDIT_NOT_ALLOWED' });
    await decideApproval(SLOT, { ...requested, runId: 'run-1' }, 'approved', 'once', undefined, { to: 'b@acme.com' });
    const [card] = useChatStore.getState().slots[SLOT]?.pendingApprovals ?? [];
    expect(card).toMatchObject({ status: 'pending', errorCode: 'HTTP_AGENT_HARNESS_EDIT_NOT_ALLOWED' });
    expect(card?.editedArgs).toBeUndefined();
  });

  it('AP-06: the route body carries `edits` only with a plain approval', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { interaction: { decision: 'approved' } } });
    const run = { agentId: 'agent-1', sessionId: 'ses-1', runId: 'run-1' };
    await ChatApi.decideDurableInteraction(run, 'ix', 'approved', 'k', undefined, undefined, { to: 'b' });
    expect(post.mock.calls[0]?.[1]).toEqual({ decision: 'approved', idempotencyKey: 'k', edits: { to: 'b' } });
    await ChatApi.decideDurableInteraction(run, 'ix', 'approved', 'k', 'conversation', undefined, { to: 'b' });
    expect(post.mock.calls[1]?.[1]).toEqual({ decision: 'approved', idempotencyKey: 'k', scope: 'conversation' });
  });
});

describe('AP-06 after a reload and the authority read', () => {
  afterEach(() => vi.restoreAllMocks());

  it('AP-06: a saved approval brings back the arguments that ran; a denial never carries them', () => {
    const part = { type: 'approval', interactionId: 'ix', toolName: 't', args: '{"a":1}', status: 'approved', editedArgs: '{"a":2}' } as MessagePart;
    expect(approvalFromPart(part)?.editedArgs).toBe('{"a":2}');
    expect(approvalFromPart({ ...part, status: 'denied' } as MessagePart)?.editedArgs).toBeUndefined();
  });

  it('AP-06: the reason says whether the call offers editing', () => {
    expect(toApprovalReason({ rule: 'writes', editable: true })?.editable).toBe(true);
    expect(toApprovalReason({ rule: 'writes' })?.editable).toBeUndefined();
  });

  it('AP-06: the authority read passes on whether the viewer may edit', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { approve: true, deny: true, edit: true } });
    expect(await ChatApi.getDecisionAuthority({ sessionId: 's', runId: 'r' })).toEqual({ approve: true, deny: true, edit: true });
  });
});
