import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/app/components/team', () => ({ CreateTeamForm: () => null }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/config', async () => ({ useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: 'me' } }) }));

import { ShareSidebar } from '../share-sidebar';
import type { ShareAdapter } from '../types';

const USERS = [
  { id: 'u1', name: 'Walter Writer', email: 'w@x.com', isInOrg: true },
  { id: 'u2', name: 'Rita Reader', email: 'r@x.com', isInOrg: true },
];

const adapter = (share: ReturnType<typeof vi.fn>): ShareAdapter => ({
  entityType: 'conversation',
  entityId: 'c1',
  sidebarTitle: 'Share Chat',
  supportsRoles: true,
  supportsTeams: false,
  roleOptions: {
    roles: ['WRITER', 'READER'],
    labels: {
      OWNER: { label: 'Full Access', description: '' },
      WRITER: { label: 'Can edit', description: 'edit' },
      READER: { label: 'Can view', description: 'view' },
    },
  },
  getSharedMembers: vi.fn().mockResolvedValue([]),
  share: share as never,
  removeMember: vi.fn(),
  getSharingUsersPaginated: vi.fn().mockResolvedValue({ users: USERS, totalCount: 2 }),
});

beforeEach(() => installBrowserShims());
afterEach(cleanup);

describe('share sidebar level', () => {
  it('goes back to Can view after each share, so the next person is not given edit by accident', async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter(share)} />);

    fireEvent.click(await screen.findByText('Walter Writer'));
    const trigger = screen.getByRole('button', { name: /Can view/ });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitemradio', { name: /Can edit/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Share' }));
    await waitFor(() => expect(share).toHaveBeenCalledTimes(1));
    expect(share.mock.calls[0]![0]).toMatchObject({ userIds: ['u1'], role: 'WRITER' });

    fireEvent.click(await screen.findByText('Rita Reader'));
    expect(screen.getByRole('button', { name: /Can view/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Share' }));
    await waitFor(() => expect(share).toHaveBeenCalledTimes(2));
    expect(share.mock.calls[1]![0]).toMatchObject({ userIds: ['u2'], role: 'READER' });
  });
});
