/**
 * SLASH2 C2: `/compact [focus]`. The command saves a request on the conversation (never a message to a model);
 * the next run compacts, and its answer shows a saved row from what that run recorded.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import testI18n from '@/lib/__tests__/test-i18n';
import { useCommandStore } from '@/lib/store/command-store';
import { builtinReason, findBuiltin, type BuiltinCommand, type BuiltinContext } from '../builtin-commands';
import { useBuiltinResults } from '../builtin-results';
import { builtinReasonText } from '../components/chat-panel/slash-command-menu';
import type { ContextCompactedView, MessagePart } from '../types';
import { createAGUIEventHandler } from '../agui-event-handler';
import { getVisibleRootParts } from '../components/message-area/agent-activity';

const api = vi.hoisted(() => ({ requestCompact: vi.fn() }));
vi.mock('../api', () => ({ ChatApi: { requestCompact: (...a: unknown[]) => api.requestCompact(...a) } }));
vi.mock('../components/activity-panel/activity-panel-host', () => ({
  ViewActivityButton: () => <button type="button">View activity</button>,
}));

import { COMPACT_FOCUS_MAX, runBuiltin, type BuiltinDeps } from '../builtin-actions';
import { ContextCompactedNote, ContextCompactedRow, compactTokens } from '../components/message-area/context-compacted-row';

afterEach(() => cleanup());

const compact = findBuiltin('compact') as BuiltinCommand;
const t = testI18n.t.bind(testI18n) as never;

const READY: BuiltinContext = {
  running: false,
  conversation: true,
  answer: true,
  harness: true,
  forkable: true,
  reasoningModel: true,
  planStrategy: true,
  mcp: true,
  knowledge: true,
  memory: true,
  skills: true,
  answers: 2,
};

describe('/compact in the menu', () => {
  it('ships on the four executors and runs when the chat has something to compact', () => {
    expect(compact).toBeDefined();
    expect(compact.executors).toEqual(['loop', 'opencode', 'codex', 'claude_code']);
    expect(builtinReason(compact, READY)).toBeNull();
  });

  it.each([
    ['a reply is running', { running: true }, 'wait', 'Wait for this run to finish'],
    ['the chat is not saved', { conversation: false }, 'noConversation', 'Send a message first'],
    ['the chat is on the classic path', { harness: false }, 'legacy', 'Compacting needs the new harness'],
    ['only one answer so far', { answers: 1 }, 'nothingToCompact', 'Nothing to compact yet'],
    ["the viewer is a participant of someone else's shared chat", { notOwner: true }, 'notOwner', "Only the chat's owner can compact it"],
  ] as const)('is greyed out when %s', (_, patch, reason, text) => {
    const got = builtinReason(compact, { ...READY, ...patch });
    expect(got).toBe(reason);
    expect(builtinReasonText(t, compact, got as never)).toBe(text);
  });
});

describe('running /compact', () => {
  let deps: BuiltinDeps;
  const rows = () => useBuiltinResults.getState().rows['slot-1'] ?? [];

  beforeEach(() => {
    useBuiltinResults.setState({ rows: {} });
    useCommandStore.setState({ handlers: {}, pending: {} });
    api.requestCompact.mockReset();
    deps = {
      t,
      rowKey: 'slot-1',
      afterPairs: 3,
      slot: { convId: 'conv-1', threadAgentId: null, durableRun: null, lastDurableRun: { sessionId: 'conv-1', runId: 'run-1' }, messages: [] } as never,
      agentId: 'agent-1',
      running: false,
      harness: true,
      isAdmin: false,
      models: [],
      currentModel: null,
      selectModel: vi.fn(),
      setEffort: vi.fn(),
      setStrategy: vi.fn(),
      modeLabel: null,
      openModelPanel: vi.fn(),
      openKnowledgePanel: vi.fn(),
      knowledgeSelection: [],
      stop: vi.fn(),
      navigate: vi.fn(),
    };
  });

  it('saves the request with its focus and shows a local row; nothing is sent or stopped', async () => {
    api.requestCompact.mockResolvedValue({ requestedAt: 1 });
    await runBuiltin(compact, '  the travel numbers ', deps);
    expect(api.requestCompact).toHaveBeenCalledWith({ agentId: 'agent-1', sessionId: 'conv-1' }, 'the travel numbers');
    expect(deps.stop).not.toHaveBeenCalled();
    expect(rows()).toEqual([
      expect.objectContaining({ typed: '/compact   the travel numbers ', afterPairs: 3, result: { kind: 'compact', focus: 'the travel numbers' } }),
    ]);
  });

  it('sends no focus when none was typed, and caps a long one', async () => {
    api.requestCompact.mockResolvedValue({ requestedAt: 1 });
    await runBuiltin(compact, '', deps);
    await runBuiltin(compact, 'x'.repeat(COMPACT_FOCUS_MAX + 20), deps);
    expect(api.requestCompact.mock.calls.map((c) => (c[1] as string).length)).toEqual([0, COMPACT_FOCUS_MAX]);
  });

  it('says there is nothing to compact when the server finds no harness run yet', async () => {
    api.requestCompact.mockResolvedValue({ refused: 'HTTP_AGENT_HARNESS_NOTHING_TO_COMPACT' });
    await runBuiltin(compact, '', deps);
    expect(rows()[0].result).toEqual({ kind: 'note', text: 'Nothing to compact yet' });
  });

  it('shows the failed row on any other refusal', async () => {
    api.requestCompact.mockResolvedValue({ refused: 'HTTP_AGENT_HARNESS_NOT_FOUND' });
    await runBuiltin(compact, '', deps);
    expect(rows()[0].result).toEqual({ kind: 'failed' });
  });
});

describe('the saved row on the answer of the run that compacted', () => {
  const view = (patch: Partial<ContextCompactedView> = {}): ContextCompactedView => ({
    executor: 'loop',
    outcome: 'compacted',
    beforeTokens: 148_000,
    afterTokens: 19_000,
    userMessagesKept: true,
    ...patch,
  });
  const row = (compacted: ContextCompactedView) => {
    render(<Theme><ContextCompactedNote compacted={compacted} run={{ sessionId: 'conv-1', runId: 'run-2' }} /></Theme>);
    return screen.getByTestId('context-compacted-row');
  };

  it('formats token counts compactly', () => {
    expect([compactTokens(148_000), compactTokens(19_400), compactTokens(950)]).toEqual(['148k', '19k', '950']);
  });

  it('says what was compacted, the counts and that your messages were kept, with the run\'s activity link', () => {
    const el = row(view());
    expect(el.textContent).toContain('Context compacted · 148k → 19k tokens · your messages kept');
    expect(el.getAttribute('role')).toBe('status');
    expect(screen.getByText('View activity')).toBeTruthy();
  });

  it('leaves out counts it does not know and "messages kept" when the engine folded them in', () => {
    expect(row(view({ executor: 'opencode', beforeTokens: null, afterTokens: null, userMessagesKept: false })).textContent).toContain(
      'Context compacted',
    );
    expect(screen.getByTestId('context-compacted-row').textContent).not.toMatch(/tokens|kept/);
  });

  it.each([
    ['nothing_to_compact', 'Nothing to compact yet'],
    ['unsupported', "This engine can't compact on request"],
    ['failed', "Compaction didn't complete; the reply used the full context"],
  ] as const)('words the %s outcome', (outcome, text) => {
    expect(row(view({ outcome })).textContent).toContain(text);
  });
});

describe('the saved row comes with the answer, never from another request', () => {
  const COMPACTED: ContextCompactedView = {
    executor: 'claude_code',
    outcome: 'compacted',
    beforeTokens: 148_000,
    afterTokens: 19_000,
    userMessagesKept: false,
  };
  const PART: MessagePart = { type: 'context_compacted', compacted: COMPACTED, runId: 'run-2' };

  it("the answer's saved part shows the row once, never folded away with the steps", () => {
    const parts: MessagePart[] = [PART, { type: 'text', content: 'Done.', isFinal: true }];
    expect(getVisibleRootParts(parts, false)).toEqual([]);
    render(<Theme><ContextCompactedRow parts={parts} run={null} /></Theme>);
    expect(screen.getByTestId('context-compacted-row').textContent).toContain('Context compacted · 148k → 19k tokens');
  });

  it('an answer without one shows nothing', () => {
    const { container } = render(<Theme><ContextCompactedRow parts={[{ type: 'text', content: 'Done.' }]} run={null} /></Theme>);
    expect(container.querySelector('[data-testid="context-compacted-row"]')).toBeNull();
  });

  it("the live frame adds the run's part once, however often it is replayed", () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const frame = { type: 'CUSTOM', name: 'context_compacted', value: COMPACTED, runId: 'run-2' };
    handle({ id: 'a', event: 'CUSTOM', data: frame });
    handle({ id: 'b', event: 'CUSTOM', data: frame });
    expect(parts.filter((part) => part.type === 'context_compacted')).toEqual([PART]);
  });
});
