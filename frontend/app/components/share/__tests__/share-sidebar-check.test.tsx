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

const adapter = (overrides: Partial<ShareAdapter>): ShareAdapter => ({
  entityType: 'collection',
  entityId: 'f1',
  sidebarTitle: 'Share',
  supportsRoles: false,
  supportsTeams: false,
  getSharedMembers: vi.fn().mockResolvedValue([]),
  share: vi.fn().mockResolvedValue(undefined),
  removeMember: vi.fn(),
  getSharingUsersPaginated: vi.fn().mockResolvedValue({ users: USERS, totalCount: 2 }),
  ...overrides,
});

beforeEach(() => installBrowserShims());
afterEach(cleanup);

describe('share sidebar pre-share check', () => {
  it('shows what the check says about each pick and keeps Share off while one is blocked', async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    const checkSelection = vi.fn(async (picked: { id: string }[]) =>
      Object.fromEntries(
        picked.map((p) => [
          p.id,
          p.id === 'u2' ? { status: 'blocked' as const, message: "Can't read: Pricing" } : { status: 'ok' as const },
        ])
      )
    );
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter({ share, checkSelection })} />);

    fireEvent.click(await screen.findByText('Walter Writer'));
    fireEvent.click(await screen.findByText('Rita Reader'));
    expect(await screen.findByText("Can't read: Pricing")).toBeTruthy();
    const shareButton = screen.getByRole('button', { name: 'Share' }) as HTMLButtonElement;
    expect(shareButton.disabled).toBe(true);

    // Removing the blocked pick lets the share go ahead.
    fireEvent.click(screen.getAllByText('r@x.com').at(-1)!.parentElement!.querySelector('div')!);
    await waitFor(() => expect(shareButton.disabled).toBe(false));
    fireEvent.click(shareButton);
    await waitFor(() => expect(share).toHaveBeenCalledWith(expect.objectContaining({ userIds: ['u1'] })));
  });

  it('shares as picked when the adapter has no check', async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    renderInTheme(<ShareSidebar open onOpenChange={() => undefined} adapter={adapter({ share })} />);
    fireEvent.click(await screen.findByText('Walter Writer'));
    const shareButton = screen.getByRole('button', { name: 'Share' }) as HTMLButtonElement;
    expect(shareButton.disabled).toBe(false);
  });
});
