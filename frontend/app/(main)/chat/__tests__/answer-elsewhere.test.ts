import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MessagePart, PendingApprovalItem, ToolApprovalAnswer } from '../types';

const m = vi.hoisted(() => ({
  load: vi.fn(async (..._a: unknown[]) => 'slot-bg'),
  stream: vi.fn(async (..._a: unknown[]) => {}),
  build: vi.fn((..._a: unknown[]) => ({ query: 'q' }) as Record<string, unknown> | null),
  models: vi.fn(async (..._a: unknown[]) => []),
  refresh: vi.fn(async () => {}),
  put: vi.fn(),
  state: {
    activeSlotId: 'slot-here',
    slots: {} as Record<string, Record<string, unknown>>,
  },
}));
vi.mock('../load-conversation', () => ({ loadConversationIntoSlot: m.load }));
vi.mock('../streaming', () => ({ streamMessageForSlot: m.stream }));
vi.mock('../runtime', () => ({ buildStreamChatRequestForSlot: m.build }));
vi.mock('../utils/fetch-models-for-context', () => ({ fetchModelsForContext: m.models }));
vi.mock('../pending-approvals-store', () => ({
  usePendingApprovals: { getState: () => ({ refresh: m.refresh, put: m.put }) },
}));
vi.mock('../store', () => ({
  useChatStore: { getState: () => m.state },
  ctxKeyFromAgent: (id: string | null) => id ?? 'assistant',
}));

import { answerElsewhere } from '../answer-elsewhere';

const card = { approvalId: 'ap-1', instanceId: 'i', serverName: 'Jira', toolName: 'create_issue', expiresAt: Date.now() + 600_000 };
const item: PendingApprovalItem = { conversationId: 'conv-bg', conversationTitle: 'Fix login', agentId: null, createdAt: 1, card };
const answer: ToolApprovalAnswer = { approvalId: 'ap-1', decision: 'allow' };

function rowWith(parts: MessagePart[], id = 'row-2') {
  return { id, role: 'assistant', content: [], metadata: { custom: { persistedParts: parts } } };
}
const waiting: MessagePart = { type: 'tool_call', toolCallId: 't1', status: 'awaiting_approval', approval: card } as MessagePart;

function slot(patch: Record<string, unknown> = {}) {
  m.state.slots['slot-bg'] = {
    convId: 'conv-bg',
    isStreaming: false,
    remoteRunInProgress: false,
    messages: [{ id: 'u1', role: 'user', content: [] }, rowWith([waiting])],
    ...patch,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  m.state.activeSlotId = 'slot-here';
  m.state.slots = { 'slot-here': { convId: 'conv-here' } };
});

describe('answerElsewhere', () => {
  it("streams on that chat's slot without switching the active slot", async () => {
    slot();
    await expect(answerElsewhere(item, answer)).resolves.toBe('sent');
    expect(m.load).toHaveBeenCalledWith('conv-bg', null);
    expect(m.build).toHaveBeenCalledWith('slot-bg', 'Tool approval: allow', undefined, answer, { agentMode: true });
    expect(m.stream).toHaveBeenCalledWith('slot-bg', 'Tool approval: allow', { query: 'q' }, { reuseRowId: 'row-2' });
    expect(m.state.activeSlotId).toBe('slot-here');
  });

  it('busy other chat sends nothing', async () => {
    slot({ isStreaming: true });
    await expect(answerElsewhere(item, answer)).resolves.toBe('busy');
    slot({ remoteRunInProgress: true });
    await expect(answerElsewhere(item, answer)).resolves.toBe('busy');
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('missing row → failed', async () => {
    slot({ messages: [rowWith([{ type: 'text', content: 'hi' } as MessagePart])] });
    await expect(answerElsewhere(item, answer)).resolves.toBe('failed');
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('a card the row already settled → stale, nothing sent', async () => {
    slot({ messages: [rowWith([{ ...waiting, status: 'completed', approvalAnswer: 'allowed' } as MessagePart])] });
    await expect(answerElsewhere(item, answer)).resolves.toBe('stale');
    expect(m.stream).not.toHaveBeenCalled();
  });

  it('a load that fails → failed', async () => {
    m.load.mockRejectedValueOnce(new Error('offline'));
    await expect(answerElsewhere(item, answer)).resolves.toBe('failed');
  });

  it('an edit the server refused comes back with its errors once the stream ends', async () => {
    slot();
    const refused = { ...card, editErrors: [{ path: '/title', message: 'Too long' }] };
    m.stream.mockImplementationOnce(async () => {
      m.state.slots['slot-bg'].messages = [rowWith([{ ...waiting, approval: refused } as MessagePart])];
    });
    await answerElsewhere(item, answer);
    await vi.waitFor(() => expect(m.put).toHaveBeenCalled());
    expect(m.refresh).toHaveBeenCalled();
    expect(m.put.mock.calls[0][0].card.editErrors).toEqual([{ path: '/title', message: 'Too long' }]);
  });
});
