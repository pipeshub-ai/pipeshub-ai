/**
 * CARDFAIL-1: a card whose answer cannot be sent says why and retries a transient failure by itself, then offers
 * Try again; a refusal with a reason is said and never retried. In a manual test, three answers to a question card got
 * 503 RUN_CONFIG_UNAVAILABLE while the app restarted, and the card stayed with no word, as if asked again.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '@/lib/__tests__/test-i18n';
import { apiClient } from '@/lib/api';
import { StreamError } from '@/lib/api/stream-errors';
import {
  cancelCardSend,
  cardSendState,
  sendCard,
  sendFailureOf,
  useCardSends,
  type SendAttempt,
} from '../card-send';
import { DurableAdmissionError } from '../harness-errors';
import { sendFailureOfError } from '../streaming';
import { approvalStreamCallbacks, decideApproval } from '../approvals';
import { approvalKey } from '../interactions';
import { useChatStore } from '../store';

const CONFIG_UNAVAILABLE = 'HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE';

/** A refused request as `apiClient` rejects it (`ProcessedError`); no status: no answer came. */
const refusal = (status: number | undefined, code?: string) =>
  Object.assign(new Error(code ?? 'network'), {
    ...(status !== undefined ? { statusCode: status } : {}),
    originalError: { response: status !== undefined ? { status, data: code ? { error: { code } } : {} } : undefined },
  });

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  useCardSends.setState({ sends: {} });
});

describe('CARDFAIL-1: which failures a card retries', () => {
  it('CF-1: no answer, a restart and a server error are transient; a 4xx with a reason is not', () => {
    expect(sendFailureOf(undefined, null)).toEqual({ retry: true, reason: 'offline' });
    expect(sendFailureOf(503, CONFIG_UNAVAILABLE)).toEqual({ retry: true, reason: 'restarting' });
    expect(sendFailureOf(502, null)).toEqual({ retry: true, reason: 'restarting' });
    expect(sendFailureOf(504, null)).toEqual({ retry: true, reason: 'restarting' });
    expect(sendFailureOf(500, null)).toEqual({ retry: true, reason: 'server' });
    // DECIDE409: the run moved under the decision on an older server; reading it again settles it.
    expect(sendFailureOf(409, 'HTTP_AGENT_HARNESS_REVISION_CONFLICT')).toEqual({ retry: true, reason: 'server' });
    expect(sendFailureOf(409, 'HTTP_AGENT_HARNESS_RUN_ACTIVE')).toEqual({ retry: false, code: 'HTTP_AGENT_HARNESS_RUN_ACTIVE' });
    expect(sendFailureOf(410, 'HTTP_AGENT_HARNESS_RUN_ENDED')).toEqual({ retry: false, code: 'HTTP_AGENT_HARNESS_RUN_ENDED' });
    expect(sendFailureOf(429, 'HTTP_AGENT_HARNESS_QUOTA_EXCEEDED')).toEqual({ retry: false, code: 'HTTP_AGENT_HARNESS_QUOTA_EXCEEDED' });
  });

  it("CF-2: a turn's failure before its first frame is read by its status and code", () => {
    expect(sendFailureOfError(new DurableAdmissionError(CONFIG_UNAVAILABLE, null, 503))).toEqual({ retry: true, reason: 'restarting' });
    expect(sendFailureOfError(new DurableAdmissionError('HTTP_AGENT_HARNESS_RUN_ACTIVE', null, 409))).toEqual({
      retry: false,
      code: 'HTTP_AGENT_HARNESS_RUN_ACTIVE',
    });
    expect(sendFailureOfError(new StreamError('offline'))).toEqual({ retry: true, reason: 'offline' });
    expect(sendFailureOfError(new StreamError('busy', 503))).toEqual({ retry: true, reason: 'restarting' });
    expect(sendFailureOfError(new TypeError('Failed to fetch'))).toEqual({ retry: true, reason: 'offline' });
  });
});

describe('CARDFAIL-1: the shared send', () => {
  const restarting: SendAttempt<string> = { failed: { retry: true, reason: 'restarting' } };

  it('CF-3: retries a transient failure with backoff, saying so, and clears once sent', async () => {
    vi.useFakeTimers();
    const attempt = vi.fn<() => Promise<SendAttempt<string>>>()
      .mockResolvedValueOnce(restarting)
      .mockResolvedValueOnce(restarting)
      .mockResolvedValueOnce({ sent: 'ok' });
    const done = sendCard('k', attempt, { again: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    expect(cardSendState('k')).toEqual({ phase: 'retrying', reason: 'restarting' });
    expect(attempt).toHaveBeenCalledTimes(1);
    // The first wait is about a second (jittered up to a quarter more).
    await vi.advanceTimersByTimeAsync(1_300);
    expect(attempt).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2_600);
    expect(await done).toEqual({ sent: 'ok' });
    expect(attempt).toHaveBeenCalledTimes(3);
    expect(cardSendState('k')).toBeUndefined();
  });

  it('CF-4: gives up after the bounded waits and offers Try again, which starts the send again', async () => {
    vi.useFakeTimers();
    const again = vi.fn();
    const attempt = vi.fn<() => Promise<SendAttempt<string>>>().mockResolvedValue(restarting);
    const done = sendCard('k', attempt, { again, delaysMs: [10, 20] });
    await vi.runAllTimersAsync();
    expect(await done).toEqual({ stopped: 'restarting' });
    expect(attempt).toHaveBeenCalledTimes(3);
    const state = cardSendState('k');
    expect(state?.phase).toBe('stopped');
    if (state?.phase === 'stopped') state.retry();
    expect(again).toHaveBeenCalledTimes(1);
  });

  it('CF-5: the default waits stay bounded: about 75 s in all, never more than 15 s between tries', async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0);
    const attempt = vi.fn<() => Promise<SendAttempt<string>>>().mockResolvedValue(restarting);
    const done = sendCard('k', attempt, { again: vi.fn() });
    await vi.advanceTimersByTimeAsync(74_999);
    expect(cardSendState('k')?.phase).toBe('retrying');
    await vi.advanceTimersByTimeAsync(1);
    expect(await done).toEqual({ stopped: 'restarting' });
    expect(attempt).toHaveBeenCalledTimes(9);
  });

  it('CF-6: a refusal with a reason is never retried', async () => {
    const attempt = vi.fn<() => Promise<SendAttempt<string>>>().mockResolvedValue({ failed: { retry: false, code: 'X' } });
    expect(await sendCard('k', attempt, { again: vi.fn() })).toEqual({ refused: 'X' });
    expect(attempt).toHaveBeenCalledTimes(1);
    expect(cardSendState('k')).toBeUndefined();
    expect(await sendCard('q', attempt, { again: vi.fn(), keepRefusal: true })).toEqual({ refused: 'X' });
    expect(cardSendState('q')).toEqual({ phase: 'refused', code: 'X' });
  });

  it('CF-7: a send that may not be repeated unasked offers Try again at once', async () => {
    const attempt = vi.fn<() => Promise<SendAttempt<string>>>().mockResolvedValue(restarting);
    expect(await sendCard('k', attempt, { again: vi.fn(), autoRetry: false })).toEqual({ stopped: 'restarting' });
    expect(attempt).toHaveBeenCalledTimes(1);
  });

  it('CF-8: a card answered elsewhere while its send waits stops the retries', async () => {
    vi.useFakeTimers();
    const attempt = vi.fn<() => Promise<SendAttempt<string>>>().mockResolvedValue(restarting);
    const done = sendCard('k', attempt, { again: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    cancelCardSend('k');
    await vi.runAllTimersAsync();
    expect(await done).toEqual({ cancelled: true });
    expect(attempt).toHaveBeenCalledTimes(1);
    expect(cardSendState('k')).toBeUndefined();
  });
});

describe('CARDFAIL-1: an approval decided while PipesHub restarts', () => {
  let SLOT = '';
  const card = () => useChatStore.getState().slots[SLOT]?.pendingApprovals?.[0];
  beforeEach(() => {
    SLOT = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(SLOT, { durableRun: { sessionId: 'ses-1', runId: 'run-1' } });
    approvalStreamCallbacks(SLOT).onApprovalRequested?.({
      interactionId: 'ix.approve.t1.0',
      toolCallId: 'call-1',
      toolName: 'jira__create_issue',
      args: '{}',
      argsSummary: null,
    });
  });

  it('CF-9: two 503s are retried under the same idempotency key, the card keeps "sending", then shows the decision', async () => {
    vi.useFakeTimers();
    const post = vi
      .spyOn(apiClient, 'post')
      .mockRejectedValueOnce(refusal(503, CONFIG_UNAVAILABLE))
      .mockRejectedValueOnce(refusal(undefined))
      .mockResolvedValueOnce({ data: { interaction: { interactionId: 'ix.approve.t1.0', decision: 'approved' } } } as never);
    const sent = decideApproval(SLOT, card()!, 'approved');
    await vi.advanceTimersByTimeAsync(0);
    const key = approvalKey(card()!);
    expect(cardSendState(key)).toEqual({ phase: 'retrying', reason: 'restarting' });
    expect(card()?.status).toBe('sending');
    // A second click while it retries sends nothing more.
    await decideApproval(SLOT, card()!, 'approved');
    await vi.runAllTimersAsync();
    await sent;
    expect(post).toHaveBeenCalledTimes(3);
    expect(new Set(post.mock.calls.map((call) => JSON.stringify(call[1])))).toEqual(
      new Set([JSON.stringify({ decision: 'approved', idempotencyKey: 'ix.approve.t1.0:approved' })]),
    );
    expect([card()?.status, card()?.errorCode]).toEqual(['approved', null]);
    expect(cardSendState(key)).toBeUndefined();
  });

  it('CF-10: still restarting after the waits: the card is open again with Try again, and Try again decides it', async () => {
    vi.useFakeTimers();
    const post = vi.spyOn(apiClient, 'post').mockRejectedValue(refusal(503, CONFIG_UNAVAILABLE));
    const sent = decideApproval(SLOT, card()!, 'denied');
    await vi.runAllTimersAsync();
    await sent;
    const key = approvalKey(card()!);
    expect([card()?.status, card()?.errorCode]).toEqual(['pending', null]);
    const state = cardSendState(key);
    expect(state?.phase).toBe('stopped');
    post.mockReset().mockResolvedValue({ data: { interaction: { interactionId: 'ix.approve.t1.0', decision: 'denied' } } } as never);
    if (state?.phase === 'stopped') state.retry();
    await vi.runAllTimersAsync();
    expect(card()?.status).toBe('denied');
    expect(cardSendState(key)).toBeUndefined();
  });

  it('CF-11: a refusal with a reason is shown at once and not retried', async () => {
    const post = vi.spyOn(apiClient, 'post').mockRejectedValue(refusal(403, 'HTTP_AGENT_HARNESS_APPROVER_REQUIRED'));
    await decideApproval(SLOT, card()!, 'approved');
    expect(post).toHaveBeenCalledTimes(1);
    expect([card()?.status, card()?.errorCode]).toEqual(['pending', 'HTTP_AGENT_HARNESS_APPROVER_REQUIRED']);
    expect(cardSendState(approvalKey(card()!))).toBeUndefined();
  });

  it('CF-12: decided elsewhere while the send waits: the retries stop and the card shows that decision', async () => {
    vi.useFakeTimers();
    const post = vi.spyOn(apiClient, 'post').mockRejectedValue(refusal(503, CONFIG_UNAVAILABLE));
    const sent = decideApproval(SLOT, card()!, 'approved');
    await vi.advanceTimersByTimeAsync(0);
    approvalStreamCallbacks(SLOT).onApprovalResolved?.({ interactionId: 'ix.approve.t1.0', decision: 'denied' } as never);
    await vi.runAllTimersAsync();
    await sent;
    expect(post).toHaveBeenCalledTimes(1);
    expect(card()?.status).toBe('denied');
    expect(cardSendState(approvalKey(card()!))).toBeUndefined();
  });
});
