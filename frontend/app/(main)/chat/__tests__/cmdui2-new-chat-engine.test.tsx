/**
 * CMDUI2: `/engine` in a brand-new chat. The choice waits in the chat store (never saved anywhere, so a reload
 * drops it) and shows as "Codex for this chat" over the composer; the first message carries it as
 * `codingEngine`, and the server checks it, runs the first turn on it and saves it on the chat before the stream
 * opens. Once the run is accepted the chat's own choice is read back: one that did not hold gets /engine's plain
 * "not saved" line, and the message has already gone on the default engine. An agent's chat offers only the
 * agent's engines.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import i18n from 'i18next';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const api = vi.hoisted(() => ({
  getSessionSandbox: vi.fn(),
  setSessionCodingEngine: vi.fn(),
  streamMessage: vi.fn(),
}));
const engineView = vi.hoisted(() => ({
  view: {
    engines: [
      { kind: 'codex', available: true, reason: null },
      { kind: 'claude_code', available: true, reason: null },
      { kind: 'opencode', available: true, reason: null },
    ],
    orgDefault: null,
    choice: 'org_default',
  },
}));
vi.mock('@/app/(main)/workspace/ai-accounts/api', () => ({
  ModelAccountsApi: { codingEngine: async () => engineView.view, putCodingEngine: vi.fn() },
}));
vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    ChatApi: {
      ...actual.ChatApi,
      getSessionSandbox: (...a: unknown[]) => api.getSessionSandbox(...a),
      setSessionCodingEngine: (...a: unknown[]) => api.setSessionCodingEngine(...a),
      streamMessage: (...a: unknown[]) => api.streamMessage(...a),
    },
  };
});

import { builtinReason, findBuiltin, type BuiltinContext } from '../builtin-commands';
import { runBuiltin, type BuiltinDeps } from '../builtin-actions';
import { NEW_CHAT_KEY, useBuiltinResults, useBuiltinRows } from '../builtin-results';
import { BuiltinResultRows } from '../components/message-area/builtin-result-rows';
import { PendingEngineChip } from '../components/chat-panel/pending-engine-chip';
import { buildStreamChatRequestForSlot } from '../runtime';
import { streamMessageForSlot } from '../streaming';
import { useChatStore } from '../store';
import type { StreamChatRequest, StreamMessageCallbacks } from '../types';

const t = i18n.t.bind(i18n) as unknown as BuiltinDeps['t'];
const NOT_SAVED = "This engine can't be used now; the choice was not saved.";

const NEW_CHAT: BuiltinContext = {
  running: false,
  conversation: false,
  answer: false,
  harness: true,
  forkable: false,
  reasoningModel: true,
  planStrategy: false,
  mcp: true,
  knowledge: true,
  memory: true,
  skills: true,
  workspace: false,
  agentChat: false,
  agentEditor: false,
  answers: 0,
  engineChoice: 'choose',
};

function deps(over: Partial<BuiltinDeps> = {}): BuiltinDeps {
  return {
    t,
    rowKey: NEW_CHAT_KEY,
    afterPairs: 0,
    slot: undefined,
    agentId: null,
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
    ...over,
  };
}

const rows = (key = NEW_CHAT_KEY) => useBuiltinResults.getState().rows[key] ?? [];
const pending = () => useChatStore.getState().pendingCodingEngine;

function Rows({ rowKey = NEW_CHAT_KEY }: { rowKey?: string }) {
  const list = useBuiltinRows(rowKey);
  return (
    <Theme>
      <BuiltinResultRows rows={list} rowKey={rowKey} afterPairs={0} last />
    </Theme>
  );
}

const initial = useChatStore.getState();

beforeEach(() => {
  useChatStore.setState(initial, true);
  useChatStore.setState({ agentEngines: [], activeSlotId: null, pendingCodingEngine: null });
  useBuiltinResults.setState({ rows: {}, pairs: {} });
  for (const fn of Object.values(api)) fn.mockReset();
});
afterEach(() => cleanup());

describe('CMDUI2: the menu in a new chat', () => {
  it('offers /engine before the first message when that message goes to the harness', () => {
    const engine = findBuiltin('engine')!;
    expect(builtinReason(engine, NEW_CHAT)).toBe(null);
    expect(builtinReason(engine, { ...NEW_CHAT, harness: false })).toBe('legacy');
    expect(builtinReason(engine, { ...NEW_CHAT, engineChoice: 'quickChat' })).toBe('quickChat');
    // Everything else that acts on the chat's sandbox still waits for the chat.
    for (const name of ['files', 'sandbox', 'upload', 'run', 'logs']) {
      expect(builtinReason(findBuiltin(name)!, NEW_CHAT), name).toBe('noConversation');
    }
  });
});

describe('CMDUI2: choosing before the first message', () => {
  it('a typed name is held for this chat, not saved: nothing is sent until the first message', async () => {
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, 'Codex', deps()));
    expect(api.setSessionCodingEngine).not.toHaveBeenCalled();
    expect(pending()).toEqual({ agentId: null, engine: 'codex' });
    expect(rows().at(-1)?.result).toMatchObject({ kind: 'engine', current: 'codex', saved: true });
    expect(await screen.findByText('Your first message in this chat uses it.')).toBeTruthy();
  });

  it('the picker holds a pick, and "As my default" lets it go', async () => {
    render(<Rows />);
    await act(async () => runBuiltin(findBuiltin('engine')!, '', deps()));
    fireEvent.click(await screen.findByRole('radio', { name: 'Claude Code' }));
    await vi.waitFor(() => expect(pending()).toEqual({ agentId: null, engine: 'claude_code' }));
    await vi.waitFor(() => expect((screen.getByRole('radio', { name: 'Claude Code' }) as HTMLInputElement).checked).toBe(true));
    fireEvent.click(screen.getByRole('radio', { name: /As my default/ }));
    await vi.waitFor(() => expect(pending()).toBe(null));
    expect(api.setSessionCodingEngine).not.toHaveBeenCalled();
  });

  it("an agent's new chat offers only the agent's engines, and holds the choice for that agent", async () => {
    useChatStore.setState({ agentEngines: ['codex', 'claude_code'] });
    await runBuiltin(findBuiltin('engine')!, 'opencode', deps({ agentId: 'agent-1' }));
    expect(pending()).toBe(null);
    expect(rows().at(-1)?.result).toMatchObject({ kind: 'engine', unknown: 'opencode' });
    await runBuiltin(findBuiltin('engine')!, 'claude', deps({ agentId: 'agent-1' }));
    expect(pending()).toEqual({ agentId: 'agent-1', engine: 'claude_code' });
  });

  it('shows the choice over the composer as "Codex for this chat", which can be cleared', () => {
    useChatStore.setState({ pendingCodingEngine: { agentId: null, engine: 'codex' } });
    render(
      <Theme>
        <PendingEngineChip agentId={null} />
      </Theme>,
    );
    expect(screen.getByText('Codex for this chat')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Use my default engine' }));
    expect(pending()).toBe(null);
  });

  it("is not shown in another agent's chat", () => {
    useChatStore.setState({ pendingCodingEngine: { agentId: 'agent-1', engine: 'codex' } });
    render(
      <Theme>
        <PendingEngineChip agentId={null} />
      </Theme>,
    );
    expect(screen.queryByText('Codex for this chat')).toBe(null);
  });
});

describe('CMDUI2: the first message', () => {
  const newSlot = (convId: string | null = null): string => {
    const slotId = useChatStore.getState().createSlot(convId);
    useChatStore.setState({ activeSlotId: slotId });
    return slotId;
  };

  it('carries the held engine in the request that creates the chat, and only that one', () => {
    useChatStore.setState({ pendingCodingEngine: { agentId: null, engine: 'codex' } });
    expect(buildStreamChatRequestForSlot(newSlot(), 'hi')?.codingEngine).toBe('codex');
    expect(buildStreamChatRequestForSlot(newSlot('conv-1'), 'hi')).not.toHaveProperty('codingEngine');
    useChatStore.setState({ pendingCodingEngine: { agentId: 'agent-1', engine: 'codex' } });
    expect(buildStreamChatRequestForSlot(newSlot(), 'hi')).not.toHaveProperty('codingEngine');
  });

  const send = async (engine: string, route: (callbacks: StreamMessageCallbacks) => void) => {
    const slotId = newSlot();
    useChatStore.setState({ pendingCodingEngine: { agentId: null, engine } });
    let sent: StreamChatRequest | null = null;
    api.streamMessage.mockImplementation(async (request: StreamChatRequest, callbacks: StreamMessageCallbacks) => {
      sent = request;
      // The first message took the choice with it: nothing is held after it is sent.
      expect(pending()).toBe(null);
      route(callbacks);
    });
    const request = buildStreamChatRequestForSlot(slotId, 'hi')!;
    await act(async () => streamMessageForSlot(slotId, 'hi', request));
    return { slotId, sent: sent as StreamChatRequest | null };
  };

  it('the run uses it: the chat reads it back once the run is accepted, and nothing more is shown', async () => {
    api.getSessionSandbox.mockResolvedValue({ codingEngine: 'codex' });
    const { slotId, sent } = await send('codex', (callbacks) =>
      callbacks.onDurableRunAccepted?.({ sessionId: 'conv-new', runId: 'run-1' }),
    );
    expect(sent?.codingEngine).toBe('codex');
    await vi.waitFor(() => expect(api.getSessionSandbox).toHaveBeenCalledWith({ sessionId: 'conv-new' }));
    expect(rows(slotId)).toEqual([]);
  });

  it("a choice the server refused: /engine's plain line, and the message went on the default engine", async () => {
    api.getSessionSandbox.mockResolvedValue({ codingEngine: null });
    const { slotId } = await send('codex', (callbacks) => callbacks.onDurableRunAccepted?.({ sessionId: 'conv-new', runId: 'run-1' }));
    render(<Rows rowKey={slotId} />);
    expect(await screen.findByText(NOT_SAVED)).toBeTruthy();
    expect(api.streamMessage).toHaveBeenCalledTimes(1);
  });

  it('a turn the classic loop answered has no engine: the same line', async () => {
    const { slotId } = await send('codex', (callbacks) => callbacks.onLegacyTurn?.());
    render(<Rows rowKey={slotId} />);
    expect(await screen.findByText(NOT_SAVED)).toBeTruthy();
    expect(api.getSessionSandbox).not.toHaveBeenCalled();
  });
});
