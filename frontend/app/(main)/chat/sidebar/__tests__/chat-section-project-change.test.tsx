import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useChatStore } from '@/chat/store';
import type { Conversation } from '@/chat/types';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/navigation', () => ({
  Link: ({ href, children, onClick, ...rest }: React.ComponentProps<'a'>) => (
    <a href={href as string} {...rest} onClick={(e) => { onClick?.(e); e.preventDefault(); }}>{children}</a>
  ),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const collab = vi.hoisted(() => ({ previewAccessChange: vi.fn(), getCollaborators: vi.fn() }));
vi.mock('@/chat/collaboration-api', async (orig) => ({
  ...(await orig<typeof import('@/chat/collaboration-api')>()),
  CollaborationApi: collab,
}));
const project = vi.hoisted(() => ({ setConversationProject: vi.fn(), list: vi.fn() }));
vi.mock('@/chat/project-api', () => ({ ProjectApi: project }));
vi.mock('@/chat/api', () => ({ ChatApi: {} }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: {} }));
vi.mock('@/app/components/share/api', () => ({
  ShareCommonApi: { listUserTeams: vi.fn().mockResolvedValue([]), getUsersByIds: vi.fn().mockResolvedValue([]) },
}));

import { ChatSectionElement } from '../chat-section-element';

function setFlags(collabOn: boolean) {
  useFeatureFlagsStore.setState({
    flags: { ENABLE_COLLABORATIVE_CHATS: collabOn, ENABLE_PROJECTS: true },
  } as never);
}

const OWNED: Conversation = {
  id: 'c1',
  title: 'Quarterly plan',
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  isShared: true,
  sharedWith: [],
  isOwner: true,
  projectId: 'p1',
};

function renderRow(agentId?: string) {
  return render(
    <Theme>
      <ChatSectionElement conversation={OWNED} isActive={false} onClick={() => {}} agentId={agentId} />
    </Theme>,
  );
}

async function chooseRemoveFromProject() {
  fireEvent.mouseEnter(screen.getByText('Quarterly plan').closest('a')!.parentElement!);
  const trigger = await screen.findByRole('button');
  fireEvent.pointerDown(trigger, { button: 0 });
  fireEvent.keyDown(trigger, { key: 'Enter' });
  fireEvent.click(await screen.findByRole('menuitem', { name: /remove from project/i }));
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} });
  vi.clearAllMocks();
  useChatStore.getState().reset();
  collab.getCollaborators.mockRejectedValue(new Error('no names'));
  project.setConversationProject.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('project link/unlink goes through the access-change dialog (PI-07)', () => {
  it('flag on: unlink shows the losers first, and Apply then calls setConversationProject(null)', async () => {
    setFlags(true);
    collab.previewAccessChange.mockResolvedValue({
      gains: [],
      loses: [{ userId: 'u2', role: 'editor' }],
      becomesReadOnly: [],
      truncated: false,
    });
    renderRow();
    await chooseRemoveFromProject();

    const dialog = await screen.findByRole('dialog');
    expect(collab.previewAccessChange).toHaveBeenCalledWith({ kind: 'chat', id: 'c1' }, { type: 'unlink' }, expect.anything());
    expect(project.setConversationProject).not.toHaveBeenCalled();
    expect(await within(dialog).findByText('Will lose access')).toBeTruthy();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(project.setConversationProject).toHaveBeenCalledWith('c1', null, { agentKey: undefined }));
  });

  it('flag on: Cancel applies nothing', async () => {
    setFlags(true);
    collab.previewAccessChange.mockResolvedValue({ gains: [], loses: [], becomesReadOnly: [], truncated: false });
    renderRow();
    await chooseRemoveFromProject();
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText("No one's access changes.");
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(project.setConversationProject).not.toHaveBeenCalled();
  });

  it('flag on: a preview error leaves Apply disabled and nothing is applied', async () => {
    setFlags(true);
    collab.previewAccessChange.mockRejectedValue({ statusCode: 500 });
    renderRow();
    await chooseRemoveFromProject();
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByRole('alert');
    const apply = within(dialog).getByRole('button', { name: 'Apply' }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    fireEvent.click(apply);
    expect(project.setConversationProject).not.toHaveBeenCalled();
  });

  it('flag off: unlink is applied directly, with no preview and no dialog', async () => {
    setFlags(false);
    renderRow();
    await chooseRemoveFromProject();
    await waitFor(() => expect(project.setConversationProject).toHaveBeenCalledWith('c1', null, { agentKey: undefined }));
    expect(collab.previewAccessChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
