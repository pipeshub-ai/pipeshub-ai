import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useChatStore } from '../../store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import type { AccessView, ConversationRole } from '../../collaboration-types';
import { CollaborationApi } from '../../collaboration-api';
import { useOwnerInactiveCheck } from '../use-owner-inactive-check';

vi.mock('../../collaboration-api', async (orig) => ({
  ...(await orig<typeof import('../../collaboration-api')>()),
  CollaborationApi: { getReadiness: vi.fn() },
}));
const getReadiness = vi.mocked(CollaborationApi.getReadiness);

const view = (role: ConversationRole): AccessView => ({
  role, isOwner: role === 'owner', accessLevel: role, canSend: role !== 'read', canManage: role === 'owner', canInvite: false, isCollaborative: true,
});

function seed(role: ConversationRole, extra: Record<string, unknown> = {}) {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, { access: view(role), isInitialized: true, ...extra });
  return slotId;
}
const inactive = (slotId: string) => useChatStore.getState().slots[slotId]?.ownerInactive;

beforeEach(() => {
  useChatStore.getState().reset();
  getReadiness.mockReset();
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true } } as never);
});

describe('useOwnerInactiveCheck', () => {
  it('marks the slot when the readiness route names OWNER_INACTIVE for an editor', async () => {
    getReadiness.mockResolvedValue({ canSend: false, reasons: ['OWNER_INACTIVE'] });
    const id = seed('write');
    renderHook(() => useOwnerInactiveCheck(id));
    await waitFor(() => expect(inactive(id)).toBe(true));
    expect(getReadiness).toHaveBeenCalledTimes(1);
    expect(getReadiness.mock.calls[0][0]).toEqual({ kind: 'chat', id: 'conv-1' });
  });

  it('leaves the slot alone when the owner is active, the check fails, or the reason is another one', async () => {
    getReadiness.mockResolvedValueOnce({ canSend: true, reasons: [] });
    const ok = seed('write');
    renderHook(() => useOwnerInactiveCheck(ok));
    await waitFor(() => expect(getReadiness).toHaveBeenCalledTimes(1));
    expect(inactive(ok)).toBeFalsy();

    getReadiness.mockRejectedValueOnce(new Error('offline'));
    const failing = seed('write');
    renderHook(() => useOwnerInactiveCheck(failing));
    await waitFor(() => expect(getReadiness).toHaveBeenCalledTimes(2));
    expect(inactive(failing)).toBeFalsy();
  });

  it('does not ask for the owner, a viewer, with the flag off, or once the slot already knows', async () => {
    const slots = [seed('owner'), seed('read'), seed('write', { ownerInactive: true })];
    for (const id of slots) renderHook(() => useOwnerInactiveCheck(id));
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: false } } as never);
    const off = seed('write');
    renderHook(() => useOwnerInactiveCheck(off));
    await new Promise((r) => setTimeout(r, 0));
    expect(getReadiness).not.toHaveBeenCalled();
  });
});
