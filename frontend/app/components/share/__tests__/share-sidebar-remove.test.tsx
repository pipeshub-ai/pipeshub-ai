import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/app/components/team', () => ({ CreateTeamForm: () => null }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({ toast: toastMock }));
vi.mock('@/config', async () => ({ useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: 'me' } }) }));

import { ShareSidebar } from '../share-sidebar';
import type { ShareAdapter, SharedMember } from '../types';

const MEMBERS: SharedMember[] = [
  { id: 'me', type: 'user', name: 'Me Owner', role: 'OWNER', isOwner: true, isCurrentUser: true },
  { id: 'u1', type: 'user', name: 'Walter Writer', role: 'WRITER', isOwner: false, isCurrentUser: false },
];

function adapter(overrides: Partial<ShareAdapter> = {}): ShareAdapter {
  return {
    entityType: 'project',
    entityId: 'p1',
    sidebarTitle: 'Share project',
    supportsRoles: true,
    supportsTeams: false,
    getSharedMembers: vi.fn().mockResolvedValue(MEMBERS),
    share: vi.fn(),
    removeMember: vi.fn().mockResolvedValue({ removed: true }),
    getSharingUsersPaginated: vi.fn().mockResolvedValue({ users: [], totalCount: 0 }),
    ...overrides,
  };
}

async function chooseRemove(name: string) {
  await screen.findByText(name);
  const trigger = screen.getAllByRole('button', { name: /Can edit|Full access/ })[0]!;
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'Enter' });
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove' }));
}

beforeEach(() => {
  installBrowserShims();
  vi.clearAllMocks();
});
afterEach(cleanup);

describe('share sidebar member removal', () => {
  it('asks before removing when the adapter wants confirmation', async () => {
    const a = adapter({ confirmBeforeRemove: true });
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={a} />);

    await chooseRemove('Walter Writer');
    expect(a.removeMember).not.toHaveBeenCalled();
    expect(await screen.findByText('Remove access?')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(a.removeMember).toHaveBeenCalledWith('u1', 'user'));
    expect(toastMock.success).toHaveBeenCalled();
  });

  it('says the member was already removed when the server removed nothing', async () => {
    const a = adapter({ removeMember: vi.fn().mockResolvedValue({ removed: false }) });
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={a} />);

    await chooseRemove('Walter Writer');
    await waitFor(() => expect(toastMock.info).toHaveBeenCalledWith('Already removed', expect.anything()));
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it('only mentions teams in the search placeholder when teams can be picked', async () => {
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter()} />);
    expect(await screen.findByPlaceholderText('Emails or names (separated by commas)')).toBeTruthy();
    cleanup();
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter({ supportsTeams: true })} />);
    expect(await screen.findByPlaceholderText('Emails, teams or names (separated by commas)')).toBeTruthy();
  });
});
