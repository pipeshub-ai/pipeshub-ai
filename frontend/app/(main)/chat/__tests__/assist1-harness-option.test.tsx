/**
 * ASSIST1 AS1-13 and CTL-5: the default chat's Agent mode runs on the harness
 * whenever the org's harness is on, with no switch of its own; the turn asks
 * with `harness: "durable"` for a new conversation then, or for a conversation
 * already on the harness. The server decides.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { streamSSERequest } from '@/lib/api';
import type { StreamChatRequest } from '../types';
import { asOwner, completedRun } from './durable-frames';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
  streamSSEGet: vi.fn(),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const { chatHarnessField, conversationHarness } = await import('../assistant-harness');
const { selectDurableAssistantAgentOffered } = await import('@/lib/store/feature-flags-store');
const { ChatApi } = await import('../api');
const { PlusMenuContent } = await import('../components/chat-panel/plus-menu-button');

const mockedStream = vi.mocked(streamSSERequest);
const harness = { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true };

afterEach(() => cleanup());
beforeEach(() => mockedStream.mockReset());

describe('CTL-5: Agent mode on the harness comes with the harness; there is no switch', () => {
  it.each([
    [{ ENABLE_DURABLE_ASSISTANT_AGENT: false }, harness, true],
    [{}, harness, true],
    [null, harness, true],
    [{ ENABLE_DURABLE_ASSISTANT_AGENT: true }, { ...harness, useHarness: false, reason: 'kill_switch' }, false],
    [{ ENABLE_DURABLE_ASSISTANT_AGENT: true }, null, false],
  ])('flags %j, harness %j -> %s', (flags, agentHarness, offered) => {
    expect(selectDurableAssistantAgentOffered({ flags, agentHarness })).toBe(offered);
  });

  it('the "+" menu has no new-harness switch', () => {
    render(
      <Theme>
        <PlusMenuContent
          onAttachFiles={vi.fn()}
          internalSearch
          webSearch
          onToggleInternalSearch={vi.fn()}
          onToggleWebSearch={vi.fn()}
        />
      </Theme>,
    );
    expect(screen.queryByRole('switch', { name: /new harness/ })).toBeNull();
  });
});

describe('AS1-13: the turn asks for the harness, and a conversation keeps its harness', () => {
  const durableAnswer = { role: 'assistant', metadata: { custom: { durableRunId: 'run-1' } } };
  const legacyAnswer = { role: 'assistant', metadata: { custom: {} } };
  const slot = (overrides: Record<string, unknown>) =>
    ({ convId: null, threadAgentId: null, durableRun: null, lastDurableRun: null, messages: [], isOwner: true, ...overrides }) as never;

  it('reads the conversation\'s harness from what it holds', () => {
    expect(conversationHarness(undefined)).toBe('new');
    expect(conversationHarness(slot({}))).toBe('new');
    expect(conversationHarness(slot({ convId: 'c-1', messages: [durableAnswer] }))).toBe('durable');
    expect(conversationHarness(slot({ convId: 'c-1', messages: [legacyAnswer] }))).toBe('legacy');
  });

  const field = (overrides: Partial<Parameters<typeof chatHarnessField>[0]>) =>
    chatHarnessField({
      queryMode: 'agent',
      scopedAgent: false,
      offered: true,
      conversation: 'new',
      chosen: true,
      needsHarness: false,
      ...overrides,
    });

  it.each([
    ['a new conversation with the harness on', {}, { harness: 'durable' }],
    ['a new conversation with the harness off', { offered: false }, {}],
    ['a durable conversation, the harness since turned off', { conversation: 'durable', offered: false }, { harness: 'durable' }],
    ['an old-loop conversation keeps the classic loop (CTL-7)', { conversation: 'legacy' }, { harness: 'legacy' }],
    ['an old-loop conversation, the harness off', { conversation: 'legacy', offered: false }, {}],
    ['another mode', { queryMode: 'chat' }, {}],
    ['web search', { queryMode: 'web-search' }, {}],
    ['a saved agent', { scopedAgent: true }, {}],
  ] as const)('%s', (_name, overrides, expected) => {
    expect(field(overrides)).toEqual(expected);
  });

  const request = (extra: Partial<StreamChatRequest> = {}): StreamChatRequest => ({
    query: 'Question',
    modelKey: 'model-1',
    modelName: 'Model',
    modelFriendlyName: 'Model',
    chatMode: 'agent',
    filters: { apps: [], kb: [] },
    runId: '3f1c2a8e-9d0b-4c5e-8a7f-112233445566',
    ...extra,
  });
  const serve = () =>
    mockedStream.mockImplementation(async (_url, _body, options) => {
      asOwner(completedRun()).forEach((frame) => options?.onEvent(frame));
    });

  it('the assistant\'s stream sends the field as the request carries it, and not otherwise', async () => {
    serve();
    await ChatApi.streamMessage(request({ harness: 'durable' }), { onComplete: vi.fn(), onChunk: vi.fn() });
    expect(mockedStream).toHaveBeenLastCalledWith(
      '/api/v1/conversations/stream',
      expect.objectContaining({ chatMode: 'agent', harness: 'durable' }),
      expect.any(Object),
    );
    await ChatApi.streamMessage(request(), { onComplete: vi.fn(), onChunk: vi.fn() });
    expect(mockedStream.mock.lastCall?.[1]).not.toHaveProperty('harness');
  });

  it('a regenerate of a durable conversation asks again', async () => {
    serve();
    const base = { modelKey: 'm', modelName: 'M', modelFriendlyName: 'M', chatMode: 'agent' as const, filters: { apps: [], kb: [] } };
    await ChatApi.streamRegenerate('c-1', 'msg-1', { onComplete: vi.fn(), onChunk: vi.fn() }, { ...base, harness: 'durable' });
    expect(mockedStream.mock.lastCall?.[1]).toMatchObject({ harness: 'durable' });
    await ChatApi.streamRegenerate('c-1', 'msg-1', { onComplete: vi.fn(), onChunk: vi.fn() }, base);
    expect(mockedStream.mock.lastCall?.[1]).not.toHaveProperty('harness');
  });
});
