/**
 * DECIDE409: "Your decision could not be sent. Try again." on "Allow all 7". The card posted seven decisions at
 * once and they raced on the run. Now "Allow all" is one request, a transient refusal is retried once, and an
 * interaction someone already decided shows that decision instead of an error.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { approvalStreamCallbacks, decideApproval, decideApprovals } from '../approvals';
import { useChatStore } from '../store';
import type { PendingApproval } from '../types';

const IDS = Array.from({ length: 7 }, (_, i) => `ix.approve.t2.${i}`);
const BASE = '/api/v1/conversations/ses-1/runs/run-1/interactions';

/** A refused request as `apiClient` rejects it (`ProcessedError`). */
const refusal = (status: number, code: string) =>
  Object.assign(new Error(code), {
    statusCode: status,
    originalError: { response: { status, data: { error: { code } } } },
  });

describe('DECIDE409: deciding a batch', () => {
  let SLOT = '';
  beforeEach(() => {
    SLOT = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(SLOT, { durableRun: { sessionId: 'ses-1', runId: 'run-1' } });
    const callbacks = approvalStreamCallbacks(SLOT);
    for (const interactionId of IDS) {
      callbacks.onApprovalRequested?.({
        interactionId,
        toolCallId: interactionId,
        toolName: 'image_generator__generate_image',
        args: '{"prompt":"a wonder"}',
        argsSummary: null,
      });
    }
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  const cards = (): PendingApproval[] => useChatStore.getState().slots[SLOT]?.pendingApprovals ?? [];

  it('"Allow all 7" sends one batch request and every card shows its receipt', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { receipt: {}, interactions: IDS.map((interactionId) => ({ interactionId, decision: 'approved' })) },
    } as never);
    await decideApprovals(SLOT, cards(), 'approved');
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe(`${BASE}/decisions`);
    expect(post.mock.calls[0][1]).toEqual({ interactionIds: IDS, decision: 'approved', idempotencyKey: 'batch:approved' });
    expect(cards().map((c) => [c.status, c.errorCode])).toEqual(IDS.map(() => ['approved', null]));
  });

  it('a call someone already denied shows "denied", not an error', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        receipt: {},
        interactions: IDS.map((interactionId, i) =>
          i === 3 ? { interactionId, decision: 'denied', alreadyDecided: true } : { interactionId, decision: 'approved' },
        ),
      },
    } as never);
    await decideApprovals(SLOT, cards(), 'approved');
    expect(cards()[3]?.status).toBe('denied');
    expect(cards().filter((c) => c.status === 'approved')).toHaveLength(6);
    expect(cards().every((c) => c.errorCode === null)).toBe(true);
  });

  it('a 409 that was the run moving under the decision is retried once, then the receipt shows', async () => {
    vi.useFakeTimers();
    const post = vi
      .spyOn(apiClient, 'post')
      .mockRejectedValueOnce(refusal(409, 'HTTP_AGENT_HARNESS_REVISION_CONFLICT'))
      .mockResolvedValueOnce({ data: { receipt: {}, interaction: { interactionId: IDS[0], decision: 'approved' } } } as never);
    const sent = decideApproval(SLOT, cards()[0]!, 'approved');
    await vi.runAllTimersAsync();
    await sent;
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][1]).toEqual(post.mock.calls[0][1]);
    expect([cards()[0]?.status, cards()[0]?.errorCode]).toEqual(['approved', null]);
  });

  it('a refusal that is not transient is shown at once, without a retry', async () => {
    const post = vi.spyOn(apiClient, 'post').mockRejectedValue(refusal(410, 'HTTP_AGENT_HARNESS_RUN_ENDED'));
    await decideApprovals(SLOT, cards(), 'denied');
    expect(post).toHaveBeenCalledTimes(1);
    expect(cards().every((c) => c.status === 'pending' && c.errorCode === 'HTTP_AGENT_HARNESS_RUN_ENDED')).toBe(true);
  });
});
