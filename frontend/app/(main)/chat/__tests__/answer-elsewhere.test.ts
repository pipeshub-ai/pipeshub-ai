import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MessagePart, PendingApprovalItem, ToolApprovalAnswer } from '../types';

type Slot = Record<string, unknown> & { messages?: unknown[]; streamingParts?: MessagePart[] };
const m = vi.hoisted(() => {
  const listeners = new Set<(s: unknown) => void>();
  const state = { activeSlotId: 'slot-here', slots: {} as Record<string, Slot> };
  return {
    listeners,
    state,
    // Mimics the store: a change tells every subscriber.
    update: (slotId: string, patch: Slot) => {
      state.slots[slotId] = { ...state.slots[slotId], ...patch };
      listeners.forEach((l) => l(state));
    },
    load: vi.fn(async (..._a: unknown[]) => 'slot-bg'),
    stream: vi.fn(async (..._a: unknown[]) => {}),
    build: vi.fn((..._a: unknown[]) => ({ query: 'q' }) as Record<string, unknown> | null),
    models: vi.fn(async (..._a: unknown[]) => []),
    pending: vi.fn(async (): Promise<unknown[]> => []),
    refresh: vi.fn(async () => {}),
    put: vi.fn(),
  };
});
vi.mock('../load-conversation', () => ({ loadConversationIntoSlot: m.load, isNotFound: (e: { statusCode?: number } | null) => e?.statusCode === 404 }));
vi.mock('../streaming', () => ({ streamMessageForSlot: m.stream }));
vi.mock('../runtime', () => ({ buildStreamChatRequestForSlot: m.build }));
vi.mock('../api', () => ({ fetchPendingApprovals: m.pending }));
vi.mock('../utils/fetch-models-for-context', () => ({ fetchModelsForContext: m.models }));
vi.mock('../pending-approvals-store', () => ({
  usePendingApprovals: { getState: () => ({ refresh: m.refresh, put: m.put }) },
}));
vi.mock('../store', () => ({
  useChatStore: {
    getState: () => m.state,
    subscribe: (l: (s: unknown) => void) => {
      m.listeners.add(l);
      return () => m.listeners.delete(l);
    },
  },
  ctxKeyFromAgent: (id: string | null) => id ?? 'assistant',
}));

import { answerElsewhere } from '../answer-elsewhere';

const card = { approvalId: 'ap-1', instanceId: 'i', serverName: 'Jira', toolName: 'create_issue', expiresAt: Date.now() + 600_000 };
const item: PendingApprovalItem = { conversationId: 'conv-bg', conversationTitle: 'Fix login', agentId: null, createdAt: 1, card };
const answer: ToolApprovalAnswer = { approvalId: 'ap-1', decision: 'allow' };

function rowWith(parts: MessagePart[], id = 'row-2') {
  return { id, role: 'assistant', content: [], metadata: { custom: { persistedParts: parts } } };
}
const waitingPart: MessagePart = { type: 'tool_call', toolCallId: 't1', status: 'awaiting_approval', approval: card } as MessagePart;
const settledPart = { ...waitingPart, status: 'running' } as MessagePart;

function slot(patch: Slot = {}) {
  m.state.slots['slot-bg'] = {
    convId: 'conv-bg',
    isStreaming: false,
    remoteRunInProgress: false,
    messages: [{ id: 'u1', role: 'user', content: [] }, rowWith([waitingPart])],
    ...patch,
  };
}

/** The stream settles the call in the live reply, as the server does once it takes the answer. */
function streamSettles() {
  m.stream.mockImplementationOnce(async () => {
    m.update('slot-bg', { isStreaming: true, streamingParts: [settledPart] });
    m.update('slot-bg', { isStreaming: false, streamingParts: [], messages: [rowWith([settledPart])] });
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  m.listeners.clear();
  m.state.activeSlotId = 'slot-here';
  m.state.slots = { 'slot-here': { convId: 'conv-here' } };
  m.pending.mockResolvedValue([item]);
});

describe('answerElsewhere', () => {
  it("streams on that chat's slot without switching the active slot", async () => {
    slot();
    streamSettles();
    await expect(answerElsewhere(item, answer)).resolves.toBe('sent');
    expect(m.load).toHaveBeenCalledWith('conv-bg', null);
    expect(m.build).toHaveBeenCalledWith('slot-bg', 'Tool approval: allow', undefined, answer, { agentMode: true, background: true });
    expect(m.stream).toHaveBeenCalledWith('slot-bg', 'Tool approval: allow', { query: 'q' }, { reuseRowId: 'row-2', quietFinish: true, onApprovalFailed: expect.any(Function) });
    expect(m.state.activeSlotId).toBe('slot-here');
  });

  it('is sent once the call settles, before the stream ends', async () => {
    slot();
    let end!: () => void;
    m.stream.mockImplementationOnce(() => new Promise<void>((r) => {
      m.update('slot-bg', { isStreaming: true, streamingParts: [settledPart] });
      end = r;
    }));
    await expect(answerElsewhere(item, answer)).resolves.toBe('sent');
    end();
  });

  it('a chat that is streaming sends nothing', async () => {
    slot({ isStreaming: true });
    await expect(answerElsewhere(item, answer)).resolves.toBe('busy');
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('a stale "run in progress" is fetched again once before giving up', async () => {
    slot({ remoteRunInProgress: true });
    m.load.mockImplementationOnce(async () => 'slot-bg').mockImplementationOnce(async () => {
      slot();
      return 'slot-bg';
    });
    streamSettles();
    await expect(answerElsewhere(item, answer)).resolves.toBe('sent');
    expect(m.load).toHaveBeenLastCalledWith('conv-bg', null, { refetch: true });
  });

  it('still running after the refetch → busy, nothing sent', async () => {
    slot({ remoteRunInProgress: true });
    await expect(answerElsewhere(item, answer)).resolves.toBe('busy');
    expect(m.load).toHaveBeenCalledTimes(2);
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('a slot loaded before the call existed is fetched again', async () => {
    slot({ messages: [rowWith([{ type: 'text', content: 'hi' } as MessagePart])] });
    m.load.mockImplementationOnce(async () => 'slot-bg').mockImplementationOnce(async () => {
      slot();
      return 'slot-bg';
    });
    streamSettles();
    await expect(answerElsewhere(item, answer)).resolves.toBe('sent');
  });

  it('missing row → failed', async () => {
    slot({ messages: [rowWith([{ type: 'text', content: 'hi' } as MessagePart])] });
    await expect(answerElsewhere(item, answer)).resolves.toBe('failed');
    expect(m.load).toHaveBeenCalledTimes(2);
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('a card the row already settled → stale, nothing sent', async () => {
    slot({ messages: [rowWith([{ ...waitingPart, status: 'completed', approvalAnswer: 'allowed' } as MessagePart])] });
    await expect(answerElsewhere(item, answer)).resolves.toBe('stale');
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('a call a newer message passed over → stale, nothing sent', async () => {
    slot({
      messages: [
        { id: 'u1', role: 'user', content: [] }, rowWith([waitingPart]),
        { id: 'u3', role: 'user', content: [] }, rowWith([{ type: 'text', content: 'hi' } as MessagePart], 'row-4'),
      ],
    });
    await expect(answerElsewhere(item, answer)).resolves.toBe('stale');
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('a call the server no longer lists (answered elsewhere) → stale, nothing sent', async () => {
    slot();
    m.pending.mockResolvedValue([]);
    await expect(answerElsewhere(item, answer)).resolves.toBe('stale');
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('a load that fails → failed', async () => {
    m.load.mockRejectedValueOnce(new Error('offline'));
    await expect(answerElsewhere(item, answer)).resolves.toBe('failed');
  });

  it('a failed stream → failed, and the call stays listed', async () => {
    slot();
    await expect(answerElsewhere(item, answer)).resolves.toBe('failed');
    expect(m.refresh).toHaveBeenCalled();
    expect(m.put).not.toHaveBeenCalled();
  });

  it('a failed stream that says why → failed with the reason, and no toast of its own', async () => {
    slot();
    m.stream.mockImplementationOnce(async (...args: unknown[]) => {
      (args[3] as { onApprovalFailed?: (why: string) => void }).onApprovalFailed?.('The tool server is down.');
    });
    await expect(answerElsewhere(item, answer)).resolves.toEqual({ failed: 'The tool server is down.' });
  });

  it('an edit the server refused comes back with its errors → refused', async () => {
    slot();
    const refused = { ...card, editErrors: [{ path: '/title', message: 'Too long' }] };
    m.stream.mockImplementationOnce(async () => {
      m.update('slot-bg', { messages: [rowWith([{ ...waitingPart, approval: refused } as MessagePart])] });
    });
    await expect(answerElsewhere(item, answer)).resolves.toBe('refused');
    expect(m.refresh).toHaveBeenCalled();
    expect(m.put.mock.calls[0][0].card.editErrors).toEqual([{ path: '/title', message: 'Too long' }]);
  });
});

describe('answerElsewhere on a deleted chat', () => {
  it('says the chat is gone when loading it finds nothing', async () => {
    m.load.mockRejectedValueOnce({ statusCode: 404 });
    expect(await answerElsewhere(item, answer)).toBe('gone');
    m.load.mockRejectedValueOnce({ statusCode: 500 });
    expect(await answerElsewhere(item, answer)).toBe('failed');
    expect(m.stream).not.toHaveBeenCalled();
  });
});
