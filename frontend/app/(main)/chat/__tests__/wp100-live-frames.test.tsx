/**
 * WP100-F (ADR-RUN-01 D1, C10.05, WP-97): the live chat words a failure from the same typed outcome as the
 * saved message, in every locale; the run's cost comes pushed from the stream and stops the poll; an approval
 * counted on an ask that needs several moves the live card's "1 of 2"; every stream request asks for the outcome frames.
 */
import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';

const getRunCost = vi.fn();
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, ChatApi: { ...actual.ChatApi, getRunCost: (...args: unknown[]) => getRunCost(...args) } };
});

import type { SSEEvent } from '@/lib/api';
import { FRAMES_HEADER, FRAMES_VERSION, streamClientHeaders } from '@/lib/api/streaming';
import { createAGUIEventHandler } from '../agui-event-handler';
import { withApprovalCounted } from '../approvals';
import { pushRunCost, pushedRunCost, resetRunCosts } from '../live-run-cost';
import { runOutcome } from '../run-outcome';
import { runFailureWords } from '../run-failure-words';
import { loadHistoricalMessages } from '../runtime';
import type { ConversationMessage, PendingApproval } from '../types';
import { LIVE_COST_POLL_MS, useLiveRunCost } from '../components/message-area/use-live-run-cost';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];

const failed = (reason: string, extra: Record<string, unknown> = {}) => ({
  kind: 'failed',
  reason,
  action: 'retry',
  sources: 0,
  kept: false,
  opening: false,
  closing: 'English words the web app never shows.',
  ...extra,
});

/** The message the live handler reports for one root RUN_ERROR frame. */
function liveWords(frame: Record<string, unknown>): string | undefined {
  const onError = vi.fn();
  createAGUIEventHandler({ onError })({ event: 'RUN_ERROR', data: { type: 'RUN_ERROR', ...frame } } as SSEEvent);
  return (onError.mock.calls[0]?.[0] as Error | undefined)?.message;
}

/** The text a reload shows for the same failure, saved as the conversation writer saves it. */
function savedWords(code: string, outcome: Record<string, unknown>): string {
  const saved = {
    _id: 'm-1',
    messageType: 'error',
    content: 'English words the web app never shows.',
    errorCode: code,
    runOutcome: outcome,
    citations: [],
  } as unknown as ConversationMessage;
  const { messages } = loadHistoricalMessages([saved]);
  const content = messages[0]?.content as Array<{ type: string; text?: string }>;
  return content[0]?.text ?? '';
}

const CASES: Array<[string, Record<string, unknown>]> = [
  ['budget_exhausted', failed('budget_exhausted')],
  ['budget_unavailable', failed('budget_unavailable')],
  ['mcp_tool_withdrawn', failed('mcp_tool_withdrawn', { servers: ['Atlassian', 'GitHub'] })],
  ['operator_stopped', failed('operator_stopped', { stop: { scope: 'org', reason: 'incident' } })],
  ['engine_tools_unreachable', failed('engine_tools_unreachable')],
];

afterEach(async () => {
  await i18n.changeLanguage('en-US');
});

describe('WP100-F1: one wording for a failure, live and saved', () => {
  it.each(LOCALES)('WP100-F1: %s says the same words live and on reload, never the English closing', async (locale) => {
    await i18n.changeLanguage(locale);
    for (const [code, outcome] of CASES) {
      const live = liveWords({ code, message: 'English words the web app never shows.', runOutcome: outcome });
      const saved = savedWords(code, outcome);
      expect(live, `${locale} ${code}`).toBe(saved);
      expect(live).toBe(runFailureWords(code, runOutcome(outcome)));
      expect(live).not.toBe('English words the web app never shows.');
    }
  });

  it('WP100-F2: a Node before the typed outcome is worded from the frame’s own fields, as before', () => {
    expect(liveWords({ code: 'mcp_tool_withdrawn', message: 'x', servers: ['Atlassian'] })).toBe(
      runFailureWords('mcp_tool_withdrawn', null, { servers: ['Atlassian'] }),
    );
    expect(liveWords({ code: 'mcp_tool_withdrawn', message: 'x', servers: ['Atlassian'] })).toContain('Atlassian');
    expect(liveWords({ code: 'something_new', message: 'The server’s own words.' })).toBe('The server’s own words.');
  });

  it('WP100-F3: the outcome’s reason wins over a code it disagrees with', () => {
    expect(runFailureWords('run_failed', runOutcome(failed('budget_exhausted')))).toBe(runFailureWords('budget_exhausted', null));
  });
});

describe('WP100-F4: the run’s cost is pushed and the poll stops', () => {
  const RUN = { sessionId: 'ses-1', runId: 'run-cost' } as never;

  beforeEach(() => {
    resetRunCosts();
    getRunCost.mockReset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('WP100-F4a: a run_cost frame reaches the store and the header’s value', async () => {
    const onRunCost = vi.fn(pushRunCost);
    createAGUIEventHandler({ onRunCost })({
      event: 'CUSTOM',
      data: { type: 'CUSTOM', name: 'run_cost', value: { runId: 'run-cost', budget: { committed: { usdMicros: 420_000 } } } },
    } as SSEEvent);
    expect(onRunCost).toHaveBeenCalledTimes(1);
    expect(pushedRunCost('run-cost')).toBe(420_000);
    getRunCost.mockResolvedValue({ status: 'running', budget: { committed: { usdMicros: 1 } } });
    const { result } = renderHook(() => useLiveRunCost(RUN, true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_COST_POLL_MS * 3);
    });
    expect(getRunCost, 'a pushed run is never polled').not.toHaveBeenCalled();
    expect(result.current).toBe('$0.42');
    act(() => pushRunCost({ runId: 'run-cost', budget: { committed: { usdMicros: 1_250_000 } } }));
    expect(result.current).toBe('$1.25');
  });

  it('WP100-F4b: an older Node that pushes nothing is polled until a push arrives', async () => {
    getRunCost.mockResolvedValue({ status: 'running', budget: { committed: { usdMicros: 100_000 } } });
    const { result } = renderHook(() => useLiveRunCost(RUN, true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_COST_POLL_MS + 10);
    });
    expect(getRunCost).toHaveBeenCalledTimes(2);
    expect(result.current).toBe('$0.10');
    act(() => pushRunCost({ runId: 'run-cost', budget: null }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_COST_POLL_MS * 3);
    });
    expect(getRunCost, 'the push took over').toHaveBeenCalledTimes(2);
    expect(result.current, 'no account: nothing to show').toBe(null);
  });

  it('WP100-F4c: a malformed frame changes nothing', () => {
    pushRunCost({ budget: { committed: { usdMicros: 5 } } });
    pushRunCost('x');
    expect(pushedRunCost('run-cost')).toBe(undefined);
  });
});

describe('WP100-F5: an approval counted on an ask that needs several moves the live card', () => {
  const card = (extra: Partial<PendingApproval> = {}): PendingApproval =>
    ({ interactionId: 'ix-1', toolCallId: 'c-1', toolName: 'mail__send', args: '{}', status: 'pending', errorCode: null, ...extra }) as PendingApproval;

  it('WP100-F5a: the frame reaches the callback with counts only', () => {
    const onApprovalCounted = vi.fn();
    const handle = createAGUIEventHandler({ onApprovalCounted });
    handle({ event: 'CUSTOM', data: { type: 'CUSTOM', name: 'approval_counted', value: { interactionId: 'ix-1', approvals: { counted: 1, required: 2 } } } } as SSEEvent);
    handle({ event: 'CUSTOM', data: { type: 'CUSTOM', name: 'approval_counted', value: { interactionId: 'ix-1', approvals: { counted: 2, required: 2 } } } } as SSEEvent);
    handle({ event: 'CUSTOM', data: { type: 'CUSTOM', name: 'approval_counted', value: { approvals: { counted: 1, required: 2 } } } } as SSEEvent);
    expect(onApprovalCounted.mock.calls).toEqual([[{ interactionId: 'ix-1', approvals: { counted: 1, required: 2 } }]]);
  });

  it('WP100-F5b: only the open card of that ask takes the count', () => {
    const list = [card(), card({ interactionId: 'ix-2' }), card({ interactionId: 'ix-1', childRunId: 'child-1' })];
    const next = withApprovalCounted(list, { interactionId: 'ix-1', approvals: { counted: 1, required: 2 } });
    expect(next[0]?.approvals).toEqual({ counted: 1, required: 2 });
    expect(next[1]?.approvals).toBe(undefined);
    expect(next[2]?.approvals, "a sub-agent's card of the same id is another ask").toBe(undefined);
    const decided = withApprovalCounted([card({ status: 'approved' })], { interactionId: 'ix-1', approvals: { counted: 1, required: 2 } });
    expect(decided[0]?.approvals).toBe(undefined);
  });
});

describe('WP100-F6: streams ask for the outcome frames', () => {
  it('WP100-F6c: every stream request names the frames this app reads', () => {
    expect(streamClientHeaders()[FRAMES_HEADER]).toBe(FRAMES_VERSION);
    expect(FRAMES_VERSION).toBe('2');
  });
});

void React;
