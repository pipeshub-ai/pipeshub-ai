/**
 * Sending from the composer before the page's model list has loaded must
 * wait for the list, not go out with an empty model (which the server
 * rejects with a 400 and a "No AI model configured" toast).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

// See reasoning-effort.test.ts: auth-store hydrates from localStorage at import.
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

const fetchModelsForContext = vi.fn();
vi.mock('../utils/fetch-models-for-context', () => ({
  fetchModelsForContext: (...args: unknown[]) => fetchModelsForContext(...args),
}));

const toastWarning = vi.fn();
vi.mock('@/lib/store/toast-store', () => ({
  toast: { warning: (...args: unknown[]) => toastWarning(...args), error: vi.fn(), success: vi.fn() },
}));

const { useChatStore, ASSISTANT_CTX } = await import('../store');
const { buildExternalStoreConfig } = await import('../runtime');

const MODEL = { modelKey: 'model-1', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' };
const initialSettings = useChatStore.getState().settings;

function send(slotId: string) {
  const config = buildExternalStoreConfig(slotId);
  return config.onNew!({ role: 'user', content: [{ type: 'text', text: 'hello' }] } as never);
}

beforeEach(() => {
  streamMessageForSlot.mockReset();
  fetchModelsForContext.mockReset();
  toastWarning.mockReset();
  useChatStore.setState({
    slots: {},
    activeSlotId: null,
    settings: { ...initialSettings, selectedModels: {}, defaultModels: {}, availableModels: {} },
  });
});

describe('sending before the model list has loaded', () => {
  it('waits for the model list and sends with the default model', async () => {
    fetchModelsForContext.mockImplementation(async (ctxKey: string) => {
      useChatStore.getState().setDefaultModelForCtx(ctxKey, MODEL);
      return [];
    });
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });

    await send(slotId);

    expect(fetchModelsForContext).toHaveBeenCalledWith(ASSISTANT_CTX);
    expect(toastWarning).not.toHaveBeenCalled();
    expect(streamMessageForSlot).toHaveBeenCalledTimes(1);
    expect(streamMessageForSlot.mock.calls[0][2]).toMatchObject(MODEL);
  });

  it('does not refetch when a model is already known', async () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().setDefaultModelForCtx(ASSISTANT_CTX, MODEL);
    useChatStore.setState({ activeSlotId: slotId });

    await send(slotId);

    expect(fetchModelsForContext).not.toHaveBeenCalled();
    expect(streamMessageForSlot.mock.calls[0][2]).toMatchObject(MODEL);
  });

  it('still warns when the workspace really has no model', async () => {
    fetchModelsForContext.mockResolvedValue([]);
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.setState({ activeSlotId: slotId });

    await send(slotId);

    expect(toastWarning).toHaveBeenCalledWith('No AI model configured', expect.anything());
  });
});
