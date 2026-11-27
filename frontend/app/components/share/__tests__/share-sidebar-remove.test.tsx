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
import { ShareFeedbackShownError, type ShareAdapter, type SharedMember } from '../types';

const MEMBERS: SharedMember[] = [
  { id: 'me', type: 'user', name: 'Me Owner', role: 'OWNER', isOwner: true, isCurrentUser: true },
  {
    id: 'u1',
    type: 'user',
    name: 'Walter Writer',
    email: 'walter@example.com',
    role: 'WRITER',
    isOwner: false,
    isCurrentUser: false,
  },
  { id: 't1', type: 'team', name: 'Design', memberCount: 4, role: 'READER', isOwner: false, isCurrentUser: false },
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

async function openRemove(name: string, role: RegExp = /Can edit|Full access/) {
  await screen.findByText(name);
  const trigger = screen.getAllByRole('button', { name: role })[0]!;
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'Enter' });
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove' }));
}

async function chooseRemove(name: string) {
  await openRemove(name);
  fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
}

beforeEach(() => {
  installBrowserShims();
  vi.clearAllMocks();
});
afterEach(cleanup);

describe('share sidebar member removal', () => {
  it('always asks before removing, showing who loses which access', async () => {
    const a = adapter();
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={a} />);

    await openRemove('Walter Writer');
    expect(a.removeMember).not.toHaveBeenCalled();
    expect(await screen.findByText('Remove access?')).toBeTruthy();
    expect(
      screen.getByText(
        "Are you sure you want to remove Walter Writer's access to this project? You can share with them again later."
      )
    ).toBeTruthy();
    const details = screen.getByTestId('remove-access-member');
    expect(details.textContent).toContain('Walter Writer');
    expect(details.textContent).toContain('walter@example.com');
    expect(details.textContent).toContain('Current access: Can edit');

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(a.removeMember).toHaveBeenCalledWith('u1', 'user'));
    expect(toastMock.success).toHaveBeenCalled();
  });

  it('keeps access when the confirmation is cancelled', async () => {
    const a = adapter();
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={a} />);

    await openRemove('Walter Writer');
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Remove access?')).toBeNull());
    expect(a.removeMember).not.toHaveBeenCalled();
  });

  it('says what removing a team means, with its size', async () => {
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter({ teamsHaveRoles: true })} />);

    await openRemove('Design', /Can view/);
    expect(
      await screen.findByText((text) =>
        text.startsWith(
          "Are you sure you want to remove Design's access to this project? Its members keep access only if"
        )
      )
    ).toBeTruthy();
    const details = screen.getByTestId('remove-access-member');
    expect(details.textContent).toContain('4 members');
    expect(details.textContent).toContain('Current access: Can view');
  });

  it('names the item when the adapter knows its name', async () => {
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter({ entityName: 'Q4 plan' })} />);

    await openRemove('Walter Writer');
    expect(
      await screen.findByText((text) => text.includes("remove Walter Writer's access to “Q4 plan”?"))
    ).toBeTruthy();
  });

  it('says the member was already removed when the server removed nothing', async () => {
    const a = adapter({ removeMember: vi.fn().mockResolvedValue({ removed: false }) });
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={a} />);

    await chooseRemove('Walter Writer');
    await waitFor(() => expect(toastMock.info).toHaveBeenCalledWith('Already removed', expect.anything()));
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it('adds no toast when the adapter has already shown why removal failed', async () => {
    const a = adapter({ removeMember: vi.fn().mockRejectedValue(new ShareFeedbackShownError('shown')) });
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={a} />);

    await chooseRemove('Walter Writer');
    await waitFor(() => expect(a.removeMember).toHaveBeenCalled());
    expect(toastMock.error).not.toHaveBeenCalled();
    expect(screen.getByText('Walter Writer')).toBeTruthy();
  });

  it('toasts a removal failure the adapter did not handle', async () => {
    const a = adapter({ removeMember: vi.fn().mockRejectedValue(new Error('boom')) });
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={a} />);

    await chooseRemove('Walter Writer');
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith('Failed to revoke access', { description: 'boom' })
    );
  });

  it('only mentions teams in the search placeholder when teams can be picked', async () => {
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter()} />);
    expect(await screen.findByPlaceholderText('Emails or names (separated by commas)')).toBeTruthy();
    cleanup();
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter({ supportsTeams: true })} />);
    expect(await screen.findByPlaceholderText('Emails, teams or names (separated by commas)')).toBeTruthy();
  });
});
