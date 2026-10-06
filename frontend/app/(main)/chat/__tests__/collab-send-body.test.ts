/**
 * What the composer puts in the send body: consent and concurrency fields exist only in a
 * collaborative chat with the flag on, so a solo chat or a flag-off build posts what it always did.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: { getState: () => ({ isHydrated: true }) },
  hydrateAuthStore: vi.fn(),
  LOGIN_NAVIGATION_EVENT: 'pipeshub:request-login-navigation',
}));

const streamMessageForSlot = vi.fn();
vi.mock('../streaming', () => ({
  streamMessageForSlot: (...args: unknown[]) => streamMessageForSlot(...args),
  cancelStreamForSlot: vi.fn(),
}));

vi.mock('../utils/fetch-models-for-context', () => ({ fetchModelsForContext: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({
  toast: { warning: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

const { useChatStore, ASSISTANT_CTX, ctxKeyFromAgent } = await import('../store');
const { buildExternalStoreConfig } = await import('../runtime');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');

const MODEL = { modelKey: 'model-1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' };
const initialSettings = useChatStore.getState().settings;
const COLLAB_ACCESS = {
  role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, canInvite: false, isCollaborative: true,
} as const;

function setup(opts: { flag: boolean; collaborative: boolean; agentId?: string }) {
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: opts.flag } });
  useChatStore.getState().setDefaultModelForCtx(opts.agentId ? ctxKeyFromAgent(opts.agentId) : ASSISTANT_CTX, MODEL);
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.setState({ activeSlotId: slotId });
  useChatStore.getState().updateSlot(slotId, {
    access: { ...COLLAB_ACCESS, isCollaborative: opts.collaborative },
    ...(opts.agentId ? { threadAgentId: opts.agentId } : {}),
    messages: [
      { id: 'u1', role: 'user', content: [{ type: 'text', text: 'earlier' }], metadata: { custom: { seq: 3 } } },
      { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'answer' }], metadata: { custom: { seq: 4 } } },
    ],
  });
  return slotId;
}

async function send(slotId: string, custom: Record<string, unknown> = {}) {
  await buildExternalStoreConfig(slotId).onNew!({
    role: 'user',
    content: [{ type: 'text', text: 'hello' }],
    metadata: { custom },
  } as never);
  return streamMessageForSlot.mock.calls[0][2] as Record<string, unknown>;
}

const attachment = { recordId: 'r1', virtualRecordId: 'v1', recordName: 'a.pdf', mimeType: 'application/pdf', sizeBytes: 1 };

beforeEach(() => {
  streamMessageForSlot.mockReset();
  useChatStore.setState({
    slots: {},
    activeSlotId: null,
    settings: { ...initialSettings, selectedModels: {}, defaultModels: {}, availableModels: {} },
  });
});

describe('the send body in a collaborative chat', () => {
  it('carries filesShared; the id and baseSeq are added when the stream starts', async () => {
    const body = await send(setup({ flag: true, collaborative: true }));

    expect(body).toMatchObject({ filesShared: false });
    expect(body).not.toHaveProperty('clientMessageId');
    expect(body).not.toHaveProperty('baseSeq');
    expect(body).not.toHaveProperty('shareToolResults');
  });

  it('sets filesShared when the turn has attachments', async () => {
    const body = await send(setup({ flag: true, collaborative: true }), { attachments: [attachment] });

    expect(body.filesShared).toBe(true);
  });

  it('sends shareToolResults in an agent chat, default off, on when ticked', async () => {
    const off = await send(setup({ flag: true, collaborative: true, agentId: 'agent-1' }));
    expect(off.shareToolResults).toBe(false);

    streamMessageForSlot.mockReset();
    const on = await send(setup({ flag: true, collaborative: true, agentId: 'agent-1' }), { shareToolResults: true });
    expect(on.shareToolResults).toBe(true);
  });

  it('ignores a shareToolResults tick outside an agent chat', async () => {
    const body = await send(setup({ flag: true, collaborative: true }), { shareToolResults: true });

    expect(body).not.toHaveProperty('shareToolResults');
  });
});

describe('the send body without collaboration', () => {
  const FIELDS = ['clientMessageId', 'baseSeq', 'filesShared', 'shareToolResults', 'resume'];

  it('has none of the fields in a solo chat with the flag on', async () => {
    const body = await send(setup({ flag: true, collaborative: false, agentId: 'agent-1' }), { attachments: [attachment], shareToolResults: true });

    for (const key of FIELDS) expect(body).not.toHaveProperty(key);
  });

  it('has none of the fields with the flag off, even in a shared chat', async () => {
    const body = await send(setup({ flag: false, collaborative: true, agentId: 'agent-1' }), { attachments: [attachment], shareToolResults: true });

    for (const key of FIELDS) expect(body).not.toHaveProperty(key);
  });
});
