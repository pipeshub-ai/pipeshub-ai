import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import i18next from 'i18next';

import en from '@/lib/i18n/locales/en-US.json';
import type { ShareAdapter } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string | number>) => i18next.t(key, vars) as string,
  }),
}));
vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/config', () => ({
  useAuthStore: Object.assign(
    (selector: (s: { user: { id: string } }) => unknown) => selector({ user: { id: 'me' } }),
    { getState: () => ({ user: { id: 'me' } }) },
  ),
  BrandMark: () => null,
}));
vi.mock('@/app/components/share/api', () => ({
  ShareCommonApi: { getAllUsers: vi.fn().mockResolvedValue([]), listUserTeams: vi.fn().mockResolvedValue([]) },
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/app/components/team', () => ({ CreateTeamForm: () => null }));
vi.mock('@/app/(main)/workspace/components/avatar-cell', () => ({
  AvatarCell: ({ name }: { name: string }) => <span>{name}</span>,
}));

import { ShareSidebar } from '../share-sidebar';

function legacyAdapter(overrides: Partial<ShareAdapter> = {}): ShareAdapter {
  return {
    entityType: 'conversation',
    entityId: 'c1',
    sidebarTitle: 'Share Chat',
    supportsRoles: false,
    supportsTeams: false,
    getSharedMembers: vi.fn().mockResolvedValue([
      { id: 'me', name: 'Me', type: 'user', role: 'OWNER', isOwner: true, isCurrentUser: true },
      { id: 'u2', name: 'Bob', type: 'user', role: 'READER', isOwner: false, isCurrentUser: false },
    ]),
    share: vi.fn().mockResolvedValue(undefined),
    removeMember: vi.fn().mockResolvedValue(undefined),
    getSharingUsersPaginated: vi.fn().mockResolvedValue({ users: [{ id: 'u9', name: 'Dana', isInOrg: true }], totalCount: 1 }),
    ...overrides,
  };
}

beforeEach(async () => {
  vi.clearAllMocks();
  await i18next.init({ lng: 'en', resources: { en: { translation: en } }, interpolation: { escapeValue: false } });
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('share sidebar', () => {
  it('shares the picked people with the default role and toasts failures', async () => {
    const share = vi.fn().mockRejectedValueOnce({ message: 'nope' }).mockResolvedValue(undefined);
    render(<Theme><ShareSidebar open onOpenChange={() => {}} adapter={legacyAdapter({ share })} /></Theme>);
    await screen.findByText('Bob');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Dana' }));
    fireEvent.click(screen.getByRole('button', { name: en.action.share }));

    await waitFor(() => expect(share).toHaveBeenCalledTimes(1));
    expect(share.mock.calls[0]).toEqual([{ userIds: ['u9'], teamIds: [], role: 'READER' }]);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(en.shareSidebar.shareFailed, { description: 'nope' }));
  });
});
