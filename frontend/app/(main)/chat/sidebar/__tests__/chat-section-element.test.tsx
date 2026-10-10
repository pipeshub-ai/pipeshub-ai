import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useChatStore } from '@/chat/store';
import { useToastStore } from '@/lib/store/toast-store';
import type { Conversation } from '@/chat/types';
import type { AccessView } from '@/chat/collaboration-types';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
const route = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => route.params,
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/navigation', () => ({
  Link: ({ href, children, onClick, ...rest }: React.ComponentProps<'a'>) => (
    <a href={href as string} {...rest} onClick={(e) => { onClick?.(e); e.preventDefault(); }}>{children}</a>
  ),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const collab = vi.hoisted(() => ({ leave: vi.fn(), archiveSelf: vi.fn(), unarchiveSelf: vi.fn() }));
vi.mock('@/chat/collaboration-api', async (orig) => ({
  ...(await orig<typeof import('@/chat/collaboration-api')>()),
  CollaborationApi: collab,
}));
vi.mock('@/chat/api', () => ({ ChatApi: { archiveConversation: vi.fn(), deleteConversation: vi.fn(), renameConversation: vi.fn() } }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: {} }));
vi.mock('@/chat/project-api', () => ({ ProjectApi: {} }));

import { ChatSectionElement } from '../chat-section-element';

type FlagsState = Partial<ReturnType<typeof useFeatureFlagsStore.getState>>;
const setFlag = (on: boolean) =>
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as FlagsState);

function access(over: Partial<AccessView> = {}): AccessView {
  return { role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, canInvite: false, isCollaborative: true, ...over };
}

function conv(over: Partial<Conversation> = {}): Conversation {
  return {
    id: 'c1',
    title: 'Quarterly plan',
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    isShared: true,
    sharedWith: [],
    isOwner: false,
    access: access(),
    ...over,
  };
}

function renderRow(c: Conversation, agentId?: string) {
  return render(
    <Theme>
      <ChatSectionElement conversation={c} isActive={false} onClick={() => {}} agentId={agentId} />
    </Theme>,
  );
}


beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} });
  vi.clearAllMocks();
  route.params = new URLSearchParams();
  useChatStore.getState().reset();
  useToastStore.getState().clearAll();
  setFlag(true);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('shared row badges (UX-01)', () => {
  it('shows an icon role badge for a viewer and for an editor', () => {
    renderRow(conv({ access: access({ role: 'read', accessLevel: 'read', canSend: false }) }));
    expect(screen.getByRole('img', { name: 'Can view' })).toBeTruthy();
    cleanup();
    renderRow(conv());
    expect(screen.getByRole('img', { name: 'Can continue' })).toBeTruthy();
  });

  it('shows an unread dot whose accessible name includes "unread"', () => {
    renderRow(conv({ unreadCount: 3 }));
    expect(screen.getByRole('img', { name: '3 unread messages' })).toBeTruthy();
  });

  it('shows no dot when nothing is unread', () => {
    renderRow(conv({ unreadCount: 0 }));
    expect(screen.queryByRole('img', { name: /unread/ })).toBeNull();
  });

  it('shows the collaborator count to the owner, and no role badge', () => {
    renderRow(conv({ isOwner: true, access: access({ isOwner: true, role: 'owner', accessLevel: 'owner' }), collaboratorCount: 2 }));
    expect(screen.getByRole('img', { name: 'Shared with 2' })).toBeTruthy();
    expect(screen.queryByRole('img', { name: 'Can continue' })).toBeNull();
  });
});

describe('shared row menu (CL-04)', () => {
  async function openSharedMenu() {
    const row = screen.getByText('Quarterly plan');
    fireEvent.mouseEnter(row.closest('a')!.parentElement!);
    fireEvent.pointerDown(await screen.findByRole('button', { name: 'Chat options' }), { button: 0, ctrlKey: false });
    fireEvent.keyDown(screen.getByRole('button', { name: 'Chat options' }), { key: 'Enter' });
  }

  it('offers Archive and Leave to a non-owner', async () => {
    renderRow(conv());
    await openSharedMenu();
    expect(await screen.findByRole('menuitem', { name: 'Archive' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Leave' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: /^Delete/i })).toBeNull();
  });

  it('Leave asks for confirmation, then calls leave, removes the row and evicts the slot', async () => {
    collab.leave.mockResolvedValue(undefined);
    route.params = new URLSearchParams('conversationId=c1');
    const store = useChatStore.getState();
    store.setSharedConversations([conv()]);
    const slotId = store.createSlot('c1');
    renderRow(conv());
    await openSharedMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Leave' }));
    const dialog = await screen.findByRole('dialog');
    expect(collab.leave).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Leave' }));
    await waitFor(() => expect(collab.leave).toHaveBeenCalledWith({ kind: 'chat', id: 'c1' }));
    await waitFor(() => expect(useChatStore.getState().sharedConversations).toHaveLength(0));
    expect(useChatStore.getState().slots[slotId]).toBeUndefined();
    expect(router.replace).toHaveBeenCalledWith('/chat/');
  });

  it('Cancel on the confirm sends nothing', async () => {
    renderRow(conv());
    await openSharedMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Leave' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(collab.leave).not.toHaveBeenCalled();
  });

  it('leaves an agent chat through the agent route', async () => {
    collab.leave.mockResolvedValue(undefined);
    renderRow(conv(), 'agent-1');
    await openSharedMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Leave' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Leave' }));
    await waitFor(() => expect(collab.leave).toHaveBeenCalledWith({ kind: 'agent', agentKey: 'agent-1', id: 'c1' }));
  });

  it('shows the coded message when the server refuses (owner-only)', async () => {
    collab.leave.mockRejectedValue({ code: 'CONVERSATION_OWNER_ONLY' });
    useChatStore.getState().setSharedConversations([conv()]);
    renderRow(conv());
    await openSharedMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Leave' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Leave' }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.title)).toContain('Only the owner can do this.'),
    );
    expect(useChatStore.getState().sharedConversations).toHaveLength(1);
  });

  it('Archive calls archiveSelf, drops the row and does not leave', async () => {
    collab.archiveSelf.mockResolvedValue(undefined);
    useChatStore.getState().setSharedConversations([conv()]);
    renderRow(conv());
    await openSharedMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(collab.archiveSelf).toHaveBeenCalledWith({ kind: 'chat', id: 'c1' }));
    expect(collab.leave).not.toHaveBeenCalled();
    await waitFor(() => expect(useChatStore.getState().sharedConversations).toHaveLength(0));
  });

  it('Unarchive is offered for a row from an archive list (archivedForMe)', async () => {
    collab.unarchiveSelf.mockResolvedValue(undefined);
    renderRow(conv({ archivedForMe: true }));
    await openSharedMenu();
    expect(screen.queryByRole('menuitem', { name: 'Archive' })).toBeNull();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Unarchive' }));
    await waitFor(() => expect(collab.unarchiveSelf).toHaveBeenCalledWith({ kind: 'chat', id: 'c1' }));
  });

  it('a run status of "archived" is not an archive flag: Archive stays, Unarchive is absent', async () => {
    renderRow(conv({ status: 'archived' }));
    await openSharedMenu();
    expect(await screen.findByRole('menuitem', { name: 'Archive' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Unarchive' })).toBeNull();
  });

  it('the owner menu is unchanged: no Leave', async () => {
    renderRow(conv({ isOwner: true, access: access({ isOwner: true, role: 'owner', accessLevel: 'owner' }) }));
    const row = screen.getByText('Quarterly plan');
    fireEvent.mouseEnter(row.closest('a')!.parentElement!);
    const trigger = await screen.findByRole('button');
    fireEvent.pointerDown(trigger, { button: 0 });
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(await screen.findByRole('menuitem', { name: 'Rename' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: /^Delete/i })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Leave' })).toBeNull();
  });
});

describe('flag-off parity', () => {
  it('shows no badges, no unread dot and no menu on a shared row', () => {
    setFlag(false);
    const { container } = renderRow(conv({ unreadCount: 4, collaboratorCount: 2 }));
    expect(screen.queryByText('Can continue')).toBeNull();
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByText(/Shared with/)).toBeNull();
    const row = screen.getByText('Quarterly plan');
    fireEvent.mouseEnter(row.closest('a')!.parentElement!);
    expect(screen.queryByRole('button')).toBeNull();
    expect(container.querySelectorAll('a')).toHaveLength(1);
  });

  it('renders a shared row identically with or without collaboration fields', () => {
    setFlag(false);
    const plain = renderRow(conv({ access: undefined })).container.innerHTML;
    cleanup();
    const withFields = renderRow(conv({ unreadCount: 4, collaboratorCount: 2 })).container.innerHTML;
    expect(withFields).toBe(plain);
  });
});
