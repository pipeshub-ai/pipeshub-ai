import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useChatStore } from '../../store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { toast } from '@/lib/store/toast-store';
import { handleRejectedSend } from '../collab-send';

vi.mock('@/lib/store/toast-store', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

beforeEach(() => {
  useChatStore.getState().reset();
  vi.mocked(toast.error).mockClear();
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true } } as never);
});

describe('handleRejectedSend: OWNER_INACTIVE', () => {
  it('marks the slot (banner, no composer) instead of toasting, and keeps the text as the draft', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    const handled = handleRejectedSend(slotId, { code: 'OWNER_INACTIVE', statusCode: 403 }, { baseMessages: [], query: 'hello', clientMessageId: 'c1' });
    expect(handled).toBe(true);
    expect(useChatStore.getState().slots[slotId]?.ownerInactive).toBe(true);
    expect(useChatStore.getState().slots[slotId]?.isStreaming).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('still toasts other coded refusals', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    handleRejectedSend(slotId, { code: 'CONVERSATION_READ_ONLY', statusCode: 403 }, { baseMessages: [], query: 'hello', clientMessageId: 'c1' });
    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(useChatStore.getState().slots[slotId]?.ownerInactive).toBeFalsy();
  });
});
