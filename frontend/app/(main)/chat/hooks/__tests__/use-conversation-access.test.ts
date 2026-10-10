import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStore } from '../../store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import type { AccessView, ConversationRole } from '../../collaboration-types';
import { useConversationAccess } from '../use-conversation-access';

type FlagsState = Partial<ReturnType<typeof useFeatureFlagsStore.getState>>;
const setFlag = (on: boolean | null) =>
  useFeatureFlagsStore.setState({ flags: on === null ? null : { ENABLE_COLLABORATIVE_CHATS: on } } as FlagsState);

function view(role: ConversationRole, o: Partial<AccessView> = {}): AccessView {
  return {
    role,
    isOwner: role === 'owner',
    accessLevel: role,
    canSend: role !== 'read',
    canManage: role === 'owner',
    canInvite: role === 'owner',
    isCollaborative: true,
    ...o,
  };
}

function seed(access: AccessView | null, extra: Record<string, unknown> = {}) {
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, { access, isInitialized: true, ...extra });
  return slotId;
}

beforeEach(() => {
  useChatStore.getState().reset();
  setFlag(false);
});

describe('useConversationAccess', () => {
  it('has no access info for a missing slot but keeps the composer', () => {
    const { result } = renderHook(() => useConversationAccess(null));
    expect(result.current).toMatchObject({ isOwner: null, showComposer: true, showReadOnlyBanner: false, canShare: false });
  });

  it('flag on, owner: composer, share, no banner', () => {
    setFlag(true);
    const id = seed(view('owner'));
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current).toMatchObject({
      collabEnabled: true, showComposer: true, canShare: true, canManage: true, showReadOnlyBanner: false,
    });
  });

  it('flag on, editor: composer, no share, no banner', () => {
    setFlag(true);
    const id = seed(view('write'));
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current).toMatchObject({ showComposer: true, canShare: false, showReadOnlyBanner: false });
  });

  it('flag on, editor allowed to invite: share without manage', () => {
    setFlag(true);
    const id = seed(view('write', { canInvite: true }));
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current).toMatchObject({ canShare: true, canManage: false, canInvite: true });
  });

  it('flag on, editor on a chat whose owner is inactive: no composer, read-only banner (D11)', () => {
    setFlag(true);
    const id = seed(view('write'), { ownerInactive: true });
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current).toMatchObject({ ownerInactive: true, showComposer: false, showReadOnlyBanner: true });
  });

  it('the owner flag never takes the owner\'s own composer away, and access lost wins over it', () => {
    setFlag(true);
    const owner = renderHook(() => useConversationAccess(seed(view('owner'), { ownerInactive: true })));
    expect(owner.result.current).toMatchObject({ ownerInactive: false, showComposer: true });
    useChatStore.getState().reset();
    const lost = renderHook(() => useConversationAccess(seed(view('write'), { ownerInactive: true, accessLost: true })));
    expect(lost.result.current).toMatchObject({ ownerInactive: false, accessLost: true, showComposer: false });
  });

  it('flag on, viewer: no composer, no share, banner', () => {
    setFlag(true);
    const id = seed(view('read'));
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current).toMatchObject({ showComposer: false, canShare: false, showReadOnlyBanner: true });
  });

  it('flag on, access lost: composer gone, banner is the lost one, view kept', () => {
    setFlag(true);
    const id = seed(view('write'), { accessLost: true });
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current).toMatchObject({ accessLost: true, showComposer: false, showReadOnlyBanner: false });
  });

  it('flag on, no view yet (loading): composer stays visible', () => {
    setFlag(true);
    const id = seed(null);
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current).toMatchObject({ isOwner: null, showComposer: true, showReadOnlyBanner: false });
  });

  it('flag off ignores role and collaboration fields', () => {
    for (const unloaded of [false, null] as const) {
      setFlag(unloaded);
      const write = seed(view('write', { isOwner: false, canInvite: true }));
      const { result: w } = renderHook(() => useConversationAccess(write));
      expect(w.current).toMatchObject({
        collabEnabled: false, showComposer: false, canShare: false, canInvite: false, showReadOnlyBanner: false, isCollaborative: false,
      });
      const owner = seed(view('owner'));
      const { result: o } = renderHook(() => useConversationAccess(owner));
      expect(o.current).toMatchObject({ showComposer: true, canShare: true, canManage: true });
    }
  });

  it('flag off ignores accessLost', () => {
    const id = seed(view('owner'), { accessLost: true });
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current).toMatchObject({ accessLost: false, showComposer: true });
  });

  it('reacts to flag and slot changes', () => {
    const id = seed(view('read', { isOwner: false }));
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current.showReadOnlyBanner).toBe(false);
    act(() => setFlag(true));
    expect(result.current.showReadOnlyBanner).toBe(true);
    act(() => useChatStore.getState().updateSlot(id, { access: view('write') }));
    expect(result.current).toMatchObject({ showReadOnlyBanner: false, showComposer: true });
  });

  it('exposes activeRun from the slot', () => {
    setFlag(true);
    const run = { runId: 'r1', userId: 'u2', displayName: 'Bob', startedAt: '2026-10-01T00:00:00Z' };
    const id = seed(view('write'), { activeRun: run });
    const { result } = renderHook(() => useConversationAccess(id));
    expect(result.current.activeRun).toEqual(run);
  });
});
